# Personal Job Application Agent — Approval-first MVP

A Python starter project for finding recent QA/software-testing vacancies, scoring them against a resume, avoiding duplicate links, creating a daily shortlist, and optionally emailing it. It does **not** submit applications. Review each opening and apply manually after approval.

## 1. Requirements
- Python 3.10+
- Internet connection
- Optional email account for SMTP notifications

## 2. Setup (Windows PowerShell)
```powershell
mkdir personal-job-agent
cd personal-job-agent
# Extract the project files into this folder
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
copy resume.txt.example resume.txt
```
Edit `resume.txt` and paste your actual resume text. Edit `.env` if you want email notifications. Never add your real `.env` or resume to a public Git repository.

## 3. Run once
```powershell
python job_agent.py
```
Open `daily_jobs.html` in your browser. The database `jobs.db` remembers seen URLs. Export the tracking database to CSV:
```powershell
python job_agent.py --export-csv
```

Adjust freshness and match threshold:
```powershell
python job_agent.py --days 3 --min-score 35
```

## 4. Track application status
Copy the exact listing URL from the report:
```powershell
python job_agent.py --status "https://example.com/job/123" SHORTLISTED "Meets skills; verify experience"
python job_agent.py --status "https://example.com/job/123" APPLIED "Applied on 2026-10-13"
```
Allowed statuses: `NEW`, `SHORTLISTED`, `APPROVED`, `APPLIED`, `REJECTED`, `IRRELEVANT`, `INTERVIEW`.

## 5. Daily scheduling on Windows
Open **Task Scheduler** → Create Basic Task → Daily → select a time → Start a program:
- Program: full path to `.venv\Scripts\python.exe`
- Arguments: full path to `job_agent.py`
- Start in: project folder

Keep the computer awake at the scheduled time. For a cloud schedule, use GitHub Actions only after moving secrets to repository Actions secrets and deciding where to persist the SQLite database/report; a normal ephemeral runner does not retain local files automatically.

## 6. Email notifications (optional)
For Gmail, enable 2-Step Verification and create an App Password. Put it in `.env` as `SMTP_PASSWORD`. Do not use your normal Gmail password. Run the script again to test.

## 7. What this MVP searches
It reads Google News RSS search results for configured QA/testing queries and filters by published date when the feed supplies one. RSS results may be news stories, reposts, stale pages, or links to third-party boards; therefore:
- Always open the original listing and confirm it is still open.
- Prefer the employer's official career page before applying.
- Some results may omit publication dates; those are marked as unknown.
- This is not a comprehensive job-search API and cannot guarantee every new opening.
- LinkedIn is **not scraped or automated**. LinkedIn explicitly prohibits third-party scraping and unauthorized website automation; use its built-in job alerts or any officially authorized integration instead.
- Do not bypass CAPTCHAs, OTPs, login restrictions, rate limits, or site controls.
- The approval workflow is a daily report: this version does not fill or submit web application forms.

## 8. Improve next
1. Add employer-provided career APIs/RSS feeds where permitted.
2. Add a resume parser for PDF/DOCX and explicit hard filters for years, degree, city and salary.
3. Add an approval dashboard and a status-edit UI.
4. Add Playwright only for sites where automation is permitted, and stop for human verification.
5. Add a stronger LLM-based matcher after validating the basic pipeline.


## 9. Approval dashboard (Windows)
Install the expanded dependencies:
```powershell
pip install -r requirements.txt
python -m playwright install chromium
```
Create your private profile:
```powershell
copy profile.example.json profile.json
```
Edit `profile.json` with accurate personal details. Do not share it or commit it to Git. Keep `phone`, email, LinkedIn URL and notice period blank until you fill them in yourself.

Run the job finder once, then start the dashboard:
```powershell
python job_agent.py
streamlit run app.py
```
Streamlit prints a local URL, usually `http://localhost:8501`. Open it in your browser.

## 10. Assisted form filling
1. Review the job listing on the employer's site.
2. In the dashboard, approve the job.
3. Click **Open assisted application browser**.
4. The helper opens a visible Chromium window and fills only common fields it can identify confidently.
5. Inspect and correct every value. Upload your resume manually.
6. The helper deliberately does **not** click Submit. After review, you submit manually on the employer's page.

This is intentional: job portals use different field labels and may have screening questions, declarations, legal attestations, or site-specific terms. Never automate answers to eligibility, visa, disability, criminal-history, or other sensitive/legal questions. Answer these yourself. Never bypass CAPTCHA, OTP, login, rate limits, or bot controls. Use this helper only where browser assistance is permitted by the site.

## 11. Gmail notifications
The original daily finder sends a report through Gmail SMTP if `.env` is configured. For Gmail, turn on 2-Step Verification and generate an App Password. Do not put your normal Gmail password into `.env`. The dashboard is local; the daily job finder and its scheduler must run for new email notifications to be sent.

## Public hosting
See `DEPLOYMENT.md` for the Streamlit Community Cloud deployment guide and security limitations.
