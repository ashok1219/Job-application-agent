import unittest

from job_agent import score_job, search_queries


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


if __name__ == "__main__":
    unittest.main()
