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
REPORT_PATH = ROOT / "daily_jobs.html"

load_dotenv(ROOT / ".env")

PROFILE_TERMS = {
    "qa": ["qa engineer", "quality assurance", "quality engineer", "software tester",
           "software test engineer", "test engineer", "quality analyst"],
    "automation": ["automation tester", "automation test engineer", "test automation",
                   "selenium", "sdet", "playwright"],
    "manual": ["manual tester", "manual testing", "functional testing", "regression testing"],
    "skills": ["selenium", "java", "testng", "postman", "api testing", "rest api",
               "sql", "jira", "jenkins", "playwright", "automation framework"],
}
TARGET_TITLES = [
    "qa engineer", "quality assurance engineer", "quality engineer",
    "software tester", "software test engineer", "test engineer",
    "manual tester", "automation tester", "automation test engineer",
    "sdet", "junior sdet", "quality analyst",
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

def score_job(title: str, summary: str, resume: str):
    text = f"{title} {summary}".lower()
    score = 0
    matched = []
    title_l = title.lower()
    if any(term in title_l for term in TARGET_TITLES):
        score += 35
        matched.append("target title")
    for group, terms in PROFILE_TERMS.items():
        group_hits = [term for term in terms if term in text]
        if group_hits:
            matched.extend(group_hits[:])
            score += min(25 if group == "skills" else 15, len(group_hits) * (4 if group == "skills" else 6))
    if any(loc.lower() in text for loc in LOCATIONS):
        score += 10
        matched.append("India/location")
    # Only reward resume overlap, never penalize for missing resume file.
    resume_hits = [term for term in PROFILE_TERMS["skills"] if term in resume and term in text]
    if resume_hits:
        score += min(15, len(resume_hits) * 3)
        matched.extend([f"resume:{x}" for x in resume_hits])
    if re.search(r"\b(0\s*[-–to]+\s*3|1\s*[-–to]+\s*2|1\+|0\+|1\s*year|two years|2\s*years?)\b", text):
        score += 8
        matched.append("potentially junior-friendly")
    if re.search(r"\b(5\+ years|6\+ years|7\+ years|8\+ years|10\+ years|lead|principal)\b", text):
        score -= 20
        matched.append("seniority warning")
    return max(0, min(100, score)), sorted(set(matched))

def fetch_jobs(days: int = 7):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    resume = resume_text()
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    found = 0
    errors = []
    headers = {"User-Agent": "PersonalJobAgent/1.0 (job seeker; RSS reader)"}
    for query in SEARCH_QUERIES:
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
                score, matched = score_job(title, summary, resume)
                pub = published.isoformat() if published else ""
                conn.execute("""
                    INSERT INTO jobs(url,title,source,published,summary,score,matched_terms,status,first_seen,last_seen)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(url) DO UPDATE SET
                      title=excluded.title, source=excluded.source,
                      published=CASE WHEN excluded.published='' THEN jobs.published ELSE excluded.published END,
                      summary=excluded.summary, score=MAX(jobs.score, excluded.score),
                      matched_terms=excluded.matched_terms, last_seen=excluded.last_seen
                """, (link, title, source, pub, summary, score, ", ".join(matched), "NEW", now, now))
                found += 1
        except Exception as exc:
            errors.append(f"{query}: {exc}")
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
    host = os.getenv("SMTP_HOST")
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
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
    return True

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
