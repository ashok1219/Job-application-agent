# Public deployment guide (Streamlit Community Cloud)

## Important privacy warning
This is a personal job tracker. Do not publish your resume, profile.json, `.env`, SQLite database, or Gmail App Password in a public repository. Keep the dashboard password-protected. Anyone with an unprotected public URL could see application notes and job-tracking data.

## Current limitation
This starter stores jobs in a local SQLite file (`jobs.db`). A hosted app's local filesystem is not a reliable long-term database and the web dashboard does not automatically run the daily search. Before relying on this for real applications, connect a persistent hosted database (for example, Supabase/Postgres) and a separate scheduled worker. The current starter is suitable for a deployment smoke test, not yet a durable multi-user production service.

## Deploy the dashboard
1. Create a private GitHub repository for this project. Do not commit `.env`, `profile.json`, `resume.txt`, `jobs.db`, or any personal data. `.gitignore` excludes these.
2. Push the source files (`app.py`, `job_agent.py`, `assisted_apply.py`, `requirements.txt`, `.streamlit/config.toml`, and this README) to the repository.
3. Sign in to Streamlit Community Cloud and choose **Create app**.
4. Select your repository, branch, and `app.py` as the main file.
5. In the app's **Settings → Secrets**, add a TOML secret:
   ```toml
   APP_PASSWORD = "use-a-long-unique-password-here"
   ```
   If you also plan to send email from the hosted app/worker, store SMTP values as secrets; never commit them.
6. Deploy. Streamlit will provide a URL. Share it only with people who should access your dashboard; they will need the dashboard password.

## Daily job search and Gmail digest
The dashboard and daily search are separate processes:
- `streamlit run app.py` serves the dashboard.
- `python job_agent.py` discovers jobs and sends the email digest.

For a real hosted deployment, configure a scheduled worker (for example, GitHub Actions or a small server) to run `python job_agent.py` daily. Put SMTP credentials in the scheduler's encrypted secrets. Note that the worker and dashboard must use the same persistent database to share job/application status. A temporary runner's local SQLite file will not be shared with Streamlit. Do not assume separate instances share files.

## Before using for real applications
- Add persistent Postgres/Supabase storage and migrate the `jobs` table.
- Add per-user authentication if more than one person will use it. The included password gate is a simple shared-password barrier, not enterprise authentication.
- Keep Playwright assisted form filling on your own Windows machine. The hosted Streamlit environment does not launch a browser on your personal computer, and automated application sites may prohibit browser automation.
- Never bypass CAPTCHA, OTP, login controls, or site automation restrictions.
