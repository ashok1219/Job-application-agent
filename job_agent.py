"""
Personal Job Application Agent (approval-first MVP)
- Discovers recent job links through Google News RSS search feeds.
- Scores jobs against resume text and a QA/testing profile.
- Deduplicates using SQLite.
- Creates a daily HTML digest and optionally emails it via SMTP.
- Does NOT scrape LinkedIn, bypass CAPTCHAs, or submit applications.
  You review each listing and apply manually after approval.
"""
from __future__ import annotations

import argparse
import email.message
import html
import json
import os
import re
import smtplib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "jobs.db"
RESUME_PATH = ROOT / "resume.txt"
PROFILE_PATH = ROOT / "profile.json"
REPORT_PATH = ROOT / "daily_jobs.html"

load_dotenv(ROOT / ".env")

TARGET_TITLES = [
    "qa engineer", "quality assurance engineer", "quality engineer",
    "software tester", "software test engineer", "test engineer",
    "manual tester", "automation tester", "automation test engineer",
    "sdet", "junior sdet", "quality analyst",
]
RESUME_ROLE_TERMS = [
    *TARGET_TITLES,
    "software engineer", "frontend developer", "front-end developer",
    "backend developer", "back-end developer", "full stack developer",
    "web developer", "mobile developer", "data analyst", "data scientist",
    "data engineer", "machine learning engineer", "devops engineer",
    "cloud engineer", "business analyst", "product manager", "project manager",
    "security engineer", "network engineer", "systems administrator",
]
LOCATIONS = ["India", "Hyderabad", "Chennai", "Bengaluru", "Bangalore", "Pune",
             "Gurugram", "Gurgaon", "Noida", "Mumbai", "Remote"]
SEARCH_QUERIES = [
    '"QA Engineer" Selenium Java India jobs',
    '"Automation Test Engineer" Selenium India jobs',
    '"Software Test Engineer" manual automation India jobs',
    '"Manual Tester" API testing India jobs',
    '"Junior SDET" Java India jobs',
    '"Quality Engineer" testing India jobs',
    'site:careers.* Selenium QA India jobs',
    'site:jobs.* "QA Engineer" India Selenium',
]
DEFAULT_SKILLS = [
    "selenium", "java", "testng", "postman", "api testing", "rest api",
    "sql", "jira", "jenkins", "playwright", "automation framework",
]
RESUME_SKILL_TERMS = sorted(set(DEFAULT_SKILLS + [
    "python", "javascript", "typescript", "c++", "c#", "go", "rust", "ruby",
    "php", "kotlin", "swift", "react", "angular", "vue", "node.js", "django",
    "flask", "fastapi", "spring", "dotnet", ".net", "aws", "azure", "gcp",
    "docker", "kubernetes", "terraform", "linux", "git", "github actions",
    "machine learning", "deep learning", "data analysis", "pandas", "numpy",
    "tableau", "power bi", "excel", "spark", "hadoop", "postgresql", "mysql",
    "mongodb", "redis", "graphql", "microservices", "agile", "scrum",
    "cypress", "appium", "cucumber", "robot framework", "load testing",
    "performance testing", "security testing", "figma", "html", "css",
    "project management", "product management", "salesforce",
]))

def load_profile() -> dict:
    if not PROFILE_PATH.exists():
        return {}
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise ValueError("profile.json must contain a JSON object.")
    return profile

def configured_terms(profile: dict, key: str, fallback: list[str]) -> list[str]:
    value = profile.get(key, fallback)
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = value
    else:
        raise ValueError(f"profile.json field '{key}' must be a string or a list of strings.")
    return [item.strip().lower() for item in values if item.strip()]

def profile_locations(profile: dict) -> list[str]:
    if "preferred_locations" in profile:
        locations = profile["preferred_locations"]
    elif "location" in profile:
        locations = profile["location"]
    else:
        locations = LOCATIONS
    if isinstance(locations, str):
        locations = locations.split(",")
    elif not isinstance(locations, list) or not all(isinstance(location, str) for location in locations):
        raise ValueError("profile.json location fields must be a string or a list of strings.")
    return [location.strip().lower() for location in locations if location.strip()]

def profile_experience_years(profile: dict):
    value = profile.get("experience_years", profile.get("experience"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("profile.json experience must be a number or a string containing years.")
    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        raise ValueError("profile.json experience must include a numeric number of years.")
    return float(match.group())

def job_minimum_experience(text: str):
    range_match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*\+?\s*years?\b",
        text,
    )
    if range_match:
        return float(range_match.group(1))
    minimum_match = re.search(r"\b(\d+(?:\.\d+)?)\s*\+\s*years?\b", text)
    if minimum_match:
        return float(minimum_match.group(1))
    return None

def search_queries(profile: dict | None = None) -> list[str]:
    profile = profile or {}
    if not profile.get("target_roles") and not profile.get("skills"):
        return SEARCH_QUERIES
    roles = configured_terms(profile, "target_roles", TARGET_TITLES)
    skills = configured_terms(profile, "skills", DEFAULT_SKILLS)
    locations = profile_locations(profile)
    location = locations[0] if locations else "India"
    keywords = " ".join(skills[:2])
    if roles:
        custom = [
            " ".join(f'"{role}" {keywords} {location} jobs'.split())
            for role in roles[:5]
        ]
    else:
        custom = [f'"{skill}" {location} jobs' for skill in skills[:5]]
    return list(dict.fromkeys(custom))

def infer_resume_skills(resume: str) -> list[str]:
    text = resume.lower()
    return [skill for skill in RESUME_SKILL_TERMS if re.search(
        rf"(?<![a-z0-9_]){re.escape(skill)}(?![a-z0-9_])", text
    )]

def infer_resume_roles(resume: str) -> list[str]:
    text = resume.lower()
    return [role for role in RESUME_ROLE_TERMS if role in text]

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            url TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            source TEXT,
            published TEXT,
            summary TEXT,
            score INTEGER DEFAULT 0,
            matched_terms TEXT DEFAULT '',
            status TEXT DEFAULT 'NEW',
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            notes TEXT DEFAULT ''
        )
    """)
    conn.commit()
    return conn

def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(value or "", "html.parser").get_text(" ")).strip()

def parse_date(entry):
    raw = entry.get("published") or entry.get("updated")
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return None

def build_feed_url(query: str) -> str:
    # Google News RSS search feed; results are links to source articles/job pages.
    return "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-IN&gl=IN&ceid=IN:en"

def resume_text() -> str:
    if RESUME_PATH.exists():
        return RESUME_PATH.read_text(encoding="utf-8", errors="ignore").lower()
    return ""

def score_job(title: str, summary: str, resume: str, profile: dict | None = None):
    profile = profile or {}
    text = f"{title} {summary}".lower()
    score = 0
    matched = []
    title_l = title.lower()
    roles = configured_terms(profile, "target_roles", TARGET_TITLES)
    skills = configured_terms(profile, "skills", DEFAULT_SKILLS)
    locations = profile_locations(profile)
    if any(term in title_l for term in roles):
        score += 35
        matched.append("target title")
    skill_hits = [term for term in skills if term in text]
    if skill_hits:
        matched.extend(skill_hits)
        score += min(25, len(skill_hits) * 5)
    if any(loc in text for loc in locations):
        score += 10
        matched.append("preferred location")
    resume_hits = [term for term in skills if term in resume and term in text]
    if resume_hits:
        score += min(15, len(resume_hits) * 3)
        matched.extend([f"resume:{x}" for x in resume_hits])
    experience = profile_experience_years(profile)
    minimum_experience = job_minimum_experience(text)
    if minimum_experience is not None and experience is not None and minimum_experience > experience:
        score -= 20
        matched.append("experience above profile")
    elif (experience is None or experience <= 3) and re.search(
        r"\b(0\s*[-–to]+\s*3|1\s*[-–to]+\s*2|1\+|0\+|1\s*year|two years|2\s*years?)\b",
        text,
    ):
        score += 8
        matched.append("potentially junior-friendly")
    if minimum_experience is None and re.search(
        r"\b(5\+ years|6\+ years|7\+ years|8\+ years|10\+ years|lead|principal)\b",
        text,
    ) and experience is not None and experience < 5:
        score -= 20
        matched.append("seniority warning")
    return max(0, min(100, score)), sorted(set(matched))

def fetch_jobs(days: int = 7, resume: str | None = None, profile: dict | None = None):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    resume = resume_text() if resume is None else resume.lower()
    profile = load_profile() if profile is None else profile
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    found = 0
    errors = []
    headers = {"User-Agent": "PersonalJobAgent/1.0 (job seeker; RSS reader)"}
    for query in search_queries(profile):
        url = build_feed_url(query)
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            for entry in feed.entries:
                published = parse_date(entry)
                if published and published < cutoff:
                    continue
                title = clean_text(entry.get("title", ""))
                link = entry.get("link", "")
                summary = clean_text(entry.get("summary", ""))
                source = clean_text(entry.get("source", {}).get("title", "")) if isinstance(entry.get("source"), dict) else ""
                if not title or not link:
                    continue
                score, matched = score_job(title, summary, resume, profile)
                pub = published.isoformat() if published else ""
                conn.execute("""
                    INSERT INTO jobs(url,title,source,published,summary,score,matched_terms,status,first_seen,last_seen)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(url) DO UPDATE SET
                      title=excluded.title, source=excluded.source,
                      published=CASE WHEN excluded.published='' THEN jobs.published ELSE excluded.published END,
                      summary=excluded.summary, score=excluded.score,
                      matched_terms=excluded.matched_terms, last_seen=excluded.last_seen
                """, (link, title, source, pub, summary, score, ", ".join(matched), "NEW", now, now))
                found += 1
        except Exception as exc:
            errors.append(f"{query}: {exc}")
    rows = conn.execute("SELECT url, title, summary FROM jobs").fetchall()
    for row in rows:
        score, matched = score_job(row["title"], row["summary"] or "", resume, profile)
        conn.execute(
            "UPDATE jobs SET score=?, matched_terms=? WHERE url=?",
            (score, ", ".join(matched), row["url"]),
        )
    conn.commit()
    conn.close()
    return found, errors

def get_jobs(days: int = 7, min_score: int = 25):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = db()
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE (published >= ? OR published = '' OR published IS NULL)
          AND score >= ?
          AND status NOT IN ('APPLIED','REJECTED','IRRELEVANT')
        ORDER BY score DESC, published DESC, first_seen DESC
    """, (cutoff, min_score)).fetchall()
    conn.close()
    return rows

def render_report(rows, errors=None):
    generated = datetime.now().astimezone().strftime("%d %b %Y, %I:%M %p %Z")
    cards = []
    for row in rows:
        title = html.escape(row["title"])
        url = html.escape(row["url"], quote=True)
        summary = html.escape((row["summary"] or "")[:700])
        terms = html.escape(row["matched_terms"] or "")
        published = html.escape(row["published"] or "Date not provided")
        source = html.escape(row["source"] or "Source via news RSS")
        cards.append(f"""
        <article class="job">
          <div class="meta">Score: <b>{row['score']}/100</b> · {published} · {source}</div>
          <h2><a href="{url}" target="_blank" rel="noopener">{title}</a></h2>
          <p>{summary}</p>
          <p class="terms"><b>Why it matched:</b> {terms}</p>
          <p><b>Approval status:</b> {html.escape(row['status'])}</p>
          <p><a href="{url}" target="_blank" rel="noopener">Open listing and review →</a></p>
        </article>""")
    error_html = ""
    if errors:
        error_html = "<h2>Feed warnings</h2><ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in errors) + "</ul>"
    content = "\n".join(cards) if cards else "<p>No matching recent listings found today. Try increasing --days or lowering --min-score.</p>"
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
    <title>Daily Job Agent</title><style>
    body{{font-family:Arial,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#222}}
    .job{{border:1px solid #ddd;border-radius:12px;padding:16px;margin:16px 0}}
    .meta,.terms{{color:#555;font-size:14px}}a{{overflow-wrap:anywhere}}h1{{font-size:26px}}
    </style></head><body><h1>Daily QA & Testing Job Shortlist</h1>
    <p>Generated: {html.escape(generated)} · Approval-first: no applications submitted automatically.</p>
    <p>Review every job on the employer's official site, confirm it is open, then apply manually.</p>
    {content}{error_html}</body></html>"""
    REPORT_PATH.write_text(page, encoding="utf-8")
    return REPORT_PATH

def send_email(subject: str, html_body: str):
    #host = os.getenv("SMTP_HOST")
    host = "smtp.gmail.com"
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    recipient = os.getenv("EMAIL_TO")
    #port = int(os.getenv("SMTP_PORT") or "587")
    #port = int(os.getenv("SMTP_PORT", "587"))
    port = 587
    if not all([host, user, password, recipient]):
        print("Email not configured; report saved locally.")
        return False
    msg = email.message.EmailMessage()
    msg["SUBJECT"] = subject
    msg["From"] = os.getenv("EMAIL_FROM", user)
    msg["To"] = recipient
    msg.set_content("Your daily job shortlist is attached as an HTML report. Open daily_jobs.html.")
    msg.add_alternative(html_body, subtype="html")
    #with smtplib.SMTP(host, port, timeout=30) as server:
    #   server.starttls()
    #    server.login(user, password)
    #    server.send_message(msg)
    #return True
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.set_debuglevel(1)

        server.ehlo()
        server.starttls()
        server.ehlo()

        print("USER:", user)
        print("USER length:", len(user or ""))
        print("PASSWORD length:", len(password or ""))

        server.login(user, password)

        print("Sending email...")
        server.send_message(msg)


def read_report():
    return REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else ""

def set_status(url: str, status: str, notes: str = ""):
    allowed = {"NEW", "SHORTLISTED", "APPROVED", "APPLIED", "REJECTED", "IRRELEVANT", "INTERVIEW"}
    status = status.upper()
    if status not in allowed:
        raise ValueError(f"Status must be one of: {', '.join(sorted(allowed))}")
    conn = db()
    cur = conn.execute("UPDATE jobs SET status=?, notes=? WHERE url=?", (status, notes, url))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise ValueError("URL not found in the database.")

def export_csv():
    import csv
    conn = db()
    rows = conn.execute("SELECT title,url,source,published,score,matched_terms,status,notes FROM jobs ORDER BY score DESC").fetchall()
    path = ROOT / "applications.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title","url","source","published","score","matched_terms","status","notes"])
        writer.writerows([tuple(r) for r in rows])
    conn.close()
    return path

def main():
    parser = argparse.ArgumentParser(description="Daily QA/testing job finder")
    parser.add_argument("--days", type=int, default=int(os.getenv("LOOKBACK_DAYS", "7")))
    parser.add_argument("--min-score", type=int, default=int(os.getenv("MIN_SCORE", "25")))
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--status", nargs=3, metavar=("URL", "STATUS", "NOTES"))
    parser.add_argument("--export-csv", action="store_true")
    args = parser.parse_args()
    db()
    if args.status:
        set_status(*args.status)
        print("Status updated.")
        return
    if args.export_csv:
        print(f"Exported: {export_csv()}")
        return
    count, errors = fetch_jobs(args.days)
    rows = get_jobs(args.days, args.min_score)
    path = render_report(rows, errors)
    print(f"Feed entries processed: {count}")
    print(f"Matching jobs in report: {len(rows)}")
    print(f"Report: {path}")
    if not args.no_email:
        ok = send_email(f"Daily QA job shortlist — {datetime.now().astimezone().strftime('%d %b %Y')}", read_report())
        print("Email sent." if ok else "Email skipped (SMTP not configured).")
    if errors:
        print(f"Warnings from {len(errors)} feed(s). See report.")

if __name__ == "__main__":
    main()
