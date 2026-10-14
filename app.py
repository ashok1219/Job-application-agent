from __future__ import annotations
import sqlite3
import subprocess
import sys
from pathlib import Path
from datetime import datetime
import streamlit as st

from job_agent import (
    fetch_jobs,
    infer_resume_roles,
    infer_resume_skills,
    load_profile,
    profile_experience_years,
)
from resume_parser import extract_resume_text

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "jobs.db"
SCRIPT = ROOT / "assisted_apply.py"

st.set_page_config(page_title="Personal Job Agent", page_icon="🧭", layout="wide")

def configured_password():
    # Streamlit Community Cloud: configure APP_PASSWORD under App settings > Secrets.
    # Local development: optionally set APP_PASSWORD in environment/.env.
    try:
        value = st.secrets.get("APP_PASSWORD", "")
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv("APP_PASSWORD", "")

import os
required_password = configured_password()
if required_password:
    if not st.session_state.get("authenticated", False):
        st.title("🔐 Personal Job Agent")
        candidate = st.text_input("Enter dashboard password", type="password")
        if st.button("Unlock dashboard", type="primary"):
            if candidate == required_password:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.caption("This dashboard is password-protected.")
        st.stop()

st.title("🧭 Personal Job Application Agent")
st.caption("Find recent profile-matched jobs, approve each application, and review before submitting.")

st.subheader("Search using a resume")
uploaded_resume = st.file_uploader(
    "Upload your resume",
    type=["pdf", "docx", "txt"],
    help="PDF, DOCX, or TXT; up to 10 MB. The uploaded file is processed in memory and is not saved.",
)
if uploaded_resume:
    try:
        resume_content = extract_resume_text(uploaded_resume.name, uploaded_resume.getvalue())
    except ValueError as exc:
        st.error(str(exc))
    else:
        saved_profile = load_profile()
        detected_skills = infer_resume_skills(resume_content)
        detected_roles = infer_resume_roles(resume_content)
        configured_skills = saved_profile.get("skills", [])
        if isinstance(configured_skills, str):
            configured_skills = configured_skills.split(",")
        if not isinstance(configured_skills, list):
            configured_skills = []
        skill_defaults = list(dict.fromkeys([
            *detected_skills,
            *(str(skill).strip().lower() for skill in configured_skills if str(skill).strip()),
        ]))
        roles_default = detected_roles or saved_profile.get("target_roles", [])
        if isinstance(roles_default, str):
            roles_default = roles_default.split(",")
        if not isinstance(roles_default, list):
            roles_default = []
        location_default = saved_profile.get(
            "preferred_locations", saved_profile.get("location", "")
        )
        if isinstance(location_default, list):
            location_default = ", ".join(str(location) for location in location_default)
        experience_default = profile_experience_years(saved_profile) or 0.0

        st.caption(f"Extracted {len(resume_content):,} characters. Resume content is not stored.")
        st.caption("Only your selected role, skill, and location search terms are sent to Google News RSS; the resume text itself is not.")
        if detected_skills:
            st.write("Skills found in resume:", ", ".join(detected_skills))
        else:
            st.info("No skills from the built-in keyword list were detected. Enter relevant skills below.")
        if detected_roles:
            st.write("Possible target roles found in resume:", ", ".join(detected_roles))

        with st.form("resume_job_search"):
            role_input = st.text_input(
                "Target job titles",
                value=", ".join(str(role) for role in roles_default),
                help="Comma-separated. Defaults to target roles in your private profile.",
            )
            skill_input = st.text_input(
                "Skills to match",
                value=", ".join(skill_defaults),
                help="Review the automatically detected skills and edit as needed.",
            )
            location_input = st.text_input(
                "Preferred locations",
                value=str(location_default),
                help="Comma-separated; leave blank to use the default job-search locations.",
            )
            experience_input = st.number_input(
                "Years of experience (0 if unknown)",
                min_value=0.0,
                max_value=60.0,
                value=float(experience_default),
                step=0.5,
            )
            run_resume_search = st.form_submit_button(
                "Find highly matched jobs", type="primary"
            )

        if run_resume_search:
            target_roles = [role.strip() for role in role_input.split(",") if role.strip()]
            target_skills = [skill.strip() for skill in skill_input.split(",") if skill.strip()]
            if not target_roles and not target_skills:
                st.error("Enter at least one target role or skill before searching.")
            else:
                search_profile = dict(saved_profile)
                search_profile["target_roles"] = target_roles
                search_profile["skills"] = target_skills
                search_profile["preferred_locations"] = [
                    location.strip() for location in location_input.split(",") if location.strip()
                ]
                search_profile.pop("location", None)
                if experience_input:
                    search_profile["experience_years"] = experience_input
                    search_profile.pop("experience", None)
                else:
                    search_profile.pop("experience_years", None)
                    search_profile.pop("experience", None)
                with st.spinner("Searching recent listings and scoring them against this resume..."):
                    found, errors = fetch_jobs(
                        days=7,
                        resume=resume_content,
                        profile=search_profile,
                    )
                st.success(f"Search complete. Processed {found} feed entries; results are ranked below.")
                if errors:
                    st.warning(f"{len(errors)} search feed(s) could not be reached.")

if st.button("🔎 Search for latest jobs now", type="primary"):
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "job_agent.py"), "--no-email"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        st.error("Job search timed out after 3 minutes. Check your connection and try again.")
    except OSError as exc:
        st.error(f"Could not start the job search: {exc}")
    else:
        if result.returncode:
            st.error("Job search failed.")
            if result.stderr:
                st.code(result.stderr)
            if result.stdout:
                st.code(result.stdout)
        else:
            st.success(result.stdout.strip() or "Job search completed.")
            if result.stderr:
                st.warning(result.stderr)

if not DB_PATH.exists():
    st.info("Run a job search above to discover recent openings.")
    st.stop()

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def update_status(url, status, notes=""):
    with get_conn() as conn:
        conn.execute("UPDATE jobs SET status=?, notes=?, last_seen=? WHERE url=?",
                     (status, notes, datetime.now().astimezone().isoformat(), url))
        conn.commit()

def all_jobs():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM jobs ORDER BY score DESC, published DESC, first_seen DESC").fetchall()

rows = all_jobs()
statuses = ["ALL", "NEW", "SHORTLISTED", "APPROVED", "APPLIED", "INTERVIEW", "REJECTED", "IRRELEVANT"]
col1, col2, col3 = st.columns([1.2, 1.2, 1])
with col1:
    status_filter = st.selectbox("Status", statuses)
with col2:
    min_score = st.slider("Minimum match score", 0, 100, 50, 5)
with col3:
    search = st.text_input("Search company / title", placeholder="e.g. QA Engineer")

filtered = []
for row in rows:
    if status_filter != "ALL" and row["status"] != status_filter:
        continue
    if (row["score"] or 0) < min_score:
        continue
    haystack = f'{row["title"]} {row["source"] or ""} {row["summary"] or ""}'.lower()
    if search and search.lower() not in haystack:
        continue
    filtered.append(row)

st.write(f"**{len(filtered)} jobs** match the current filters.")
if not filtered:
    st.info("No jobs match these filters. Run the daily search again or lower the score threshold.")
    st.stop()

titles = [f'{r["score"]:>3}/100 · {r["title"]} · {r["status"]}' for r in filtered]
selected_index = st.selectbox("Choose a job to review", range(len(filtered)),
                              format_func=lambda i: titles[i])
job = filtered[selected_index]

left, right = st.columns([1.6, 1])
with left:
    st.subheader(job["title"])
    st.markdown(f'**Match score:** {job["score"]}/100')
    st.markdown(f'**Source:** {job["source"] or "Not identified"}')
    st.markdown(f'**Published:** {job["published"] or "Date not provided"}')
    st.markdown(f'**Current status:** `{job["status"]}`')
    st.markdown(f'[Open original listing in browser]({job["url"]})')
    st.markdown("#### Why it matched")
    terms = [x.strip() for x in (job["matched_terms"] or "").split(",") if x.strip()]
    if terms:
        for term in terms:
            st.write(f"- {term}")
    else:
        st.write("No specific match terms recorded. Review the job description manually.")
    st.markdown("#### Listing summary")
    st.write(job["summary"] or "No summary supplied by the source.")
    st.caption("A high score is a relevance estimate, not proof that you meet every requirement. Confirm the original employer page, posting date, experience, location and eligibility.")
with right:
    st.subheader("Review & approval")
    notes = st.text_area("Review notes", value=job["notes"] or "",
                         placeholder="e.g. 1–2 years accepted; Selenium + Java required")
    a, b = st.columns(2)
    with a:
        if st.button("⭐ Shortlist", use_container_width=True):
            update_status(job["url"], "SHORTLISTED", notes)
            st.rerun()
        if st.button("✅ Approve for assisted application", use_container_width=True, type="primary"):
            update_status(job["url"], "APPROVED", notes)
            st.success("Approved for assisted form filling. No application has been submitted.")
            st.rerun()
    with b:
        if st.button("❌ Reject", use_container_width=True):
            update_status(job["url"], "REJECTED", notes)
            st.rerun()
        if st.button("📨 Mark applied", use_container_width=True):
            update_status(job["url"], "APPLIED", notes)
            st.rerun()

    st.divider()
    st.subheader("Browser-assisted form filling")
    st.write("For an approved job, open the original page in a controlled Playwright browser. The helper can fill obvious fields from your profile, then pauses for you to review and submit manually.")
    st.caption("Approval authorizes form preparation only. Verify every answer and click Submit yourself; the agent never submits applications.")
    can_launch = job["status"] == "APPROVED"
    if not can_launch:
        st.info("Approve this job first to enable assisted form filling.")
    if st.button("Open assisted application browser", disabled=not can_launch, use_container_width=True):
        try:
            subprocess.Popen([sys.executable, str(SCRIPT), job["url"]], cwd=str(ROOT))
            st.success("Browser helper launched. Follow its terminal instructions.")
        except Exception as exc:
            st.error(f"Could not launch browser helper: {exc}")

st.divider()
st.subheader("Daily workflow")
st.markdown("""
1. Run the daily finder (`python job_agent.py`) to update the database.
2. Or use **Search for latest jobs now** above. Jobs are ranked using profile roles, skills, location, experience and resume overlap.
3. Review the score, matching terms and original employer listing.
4. Shortlist, reject or approve each suitable job.
5. For approved jobs, use assisted form filling and verify every field.
6. **You must review the completed form and click Submit yourself.**
7. Mark the application as applied and record any confirmation details in notes.
""")
