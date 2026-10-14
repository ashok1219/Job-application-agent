import unittest
import sqlite3
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from job_agent import (
    fetch_jobs,
    infer_resume_roles,
    infer_resume_skills,
    score_job,
    search_queries,
)


class ProfileMatchingTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "target_roles": ["Data Analyst"],
            "skills": ["Python", "SQL"],
            "preferred_locations": ["Remote"],
            "experience_years": 1.5,
        }

    def test_profile_terms_influence_job_score(self):
        score, reasons = score_job(
            "Junior Data Analyst - Remote",
            "Python, SQL, 1-2 years experience",
            "Python SQL analyst",
            self.profile,
        )

        self.assertGreaterEqual(score, 50)
        self.assertIn("target title", reasons)
        self.assertIn("python", reasons)
        self.assertIn("preferred location", reasons)

    def test_experience_mismatch_reduces_match_score(self):
        junior_score, _ = score_job(
            "Data Analyst - Remote",
            "Python, SQL, 1-2 years experience",
            "",
            self.profile,
        )
        senior_score, reasons = score_job(
            "Data Analyst - Remote",
            "Python, SQL, 5+ years experience",
            "",
            self.profile,
        )

        self.assertLess(senior_score, junior_score)
        self.assertIn("experience above profile", reasons)

    def test_search_queries_include_profile_role_and_skill(self):
        queries = search_queries(self.profile)
        self.assertIn('"data analyst" python sql remote jobs', queries)
        self.assertFalse(any("qa engineer" in query for query in queries))
        self.assertTrue(search_queries())
        self.assertEqual(
            ['"python" remote jobs', '"sql" remote jobs'],
            search_queries({
                "target_roles": [],
                "skills": ["Python", "SQL"],
                "preferred_locations": ["Remote"],
            }),
        )

    def test_resume_skills_are_detected_without_partial_word_matches(self):
        skills = infer_resume_skills("Built APIs with Python, SQL and React. MySQL was also used.")

        self.assertIn("python", skills)
        self.assertIn("sql", skills)
        self.assertIn("react", skills)
        self.assertIn("mysql", skills)
        self.assertNotIn("sql", infer_resume_skills("Used PostgreSQL and MySQL."))

    def test_resume_roles_are_suggested_from_resume_text(self):
        roles = infer_resume_roles("Experience as a Data Analyst and Business Analyst.")

        self.assertIn("data analyst", roles)
        self.assertIn("business analyst", roles)

    def test_uploaded_resume_drives_search_and_current_database_scores(self):
        published = format_datetime(datetime.now(timezone.utc))
        feed = f"""<?xml version="1.0"?>
        <rss version="2.0"><channel><item>
          <title>Data Analyst - Remote</title>
          <link>https://example.com/jobs/1</link>
          <pubDate>{published}</pubDate>
          <description>Python analytics opportunity</description>
        </item></channel></rss>""".encode()

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jobs.db"
            with patch("job_agent.DB_PATH", database_path), patch(
                "job_agent.requests.get"
            ) as request:
                request.return_value.content = feed
                profile = {
                    "target_roles": ["Data Analyst"],
                    "skills": ["Python"],
                    "preferred_locations": ["Remote"],
                }
                count, errors = fetch_jobs(
                    resume="Python analytics experience",
                    profile=profile,
                )

                self.assertEqual(1, count)
                self.assertEqual([], errors)
                query = parse_qs(urlparse(request.call_args.args[0]).query)["q"][0]
                self.assertIn("data analyst", query)
                with sqlite3.connect(database_path) as connection:
                    first_terms = connection.execute(
                        "SELECT matched_terms FROM jobs"
                    ).fetchone()[0]
                self.assertIn("resume:python", first_terms)

                fetch_jobs(
                    resume="Java analytics experience",
                    profile={
                        "target_roles": ["Data Analyst"],
                        "skills": ["Java"],
                        "preferred_locations": ["Remote"],
                    },
                )
                with sqlite3.connect(database_path) as connection:
                    updated_terms = connection.execute(
                        "SELECT matched_terms FROM jobs"
                    ).fetchone()[0]
                self.assertNotIn("resume:python", updated_terms)
                self.assertIn("target title", updated_terms)


if __name__ == "__main__":
    unittest.main()
