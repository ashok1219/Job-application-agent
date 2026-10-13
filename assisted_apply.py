"""
Human-in-the-loop Playwright form assistant.
It opens a job page, fills only obvious fields from profile.json, then pauses.
It does not click Submit, bypass login, solve CAPTCHA, or handle OTP.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
PROFILE_FILE = ROOT / "profile.json"
RESUME_FILE = ROOT / "resume.pdf"

def load_profile():
    if not PROFILE_FILE.exists():
        raise SystemExit("profile.json not found. Copy profile.example.json to profile.json and edit it.")
    return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))

def label_for(locator, page):
    try:
        aria = locator.get_attribute("aria-label")
        if aria: return aria.lower()
        placeholder = locator.get_attribute("placeholder")
        if placeholder: return placeholder.lower()
        name = locator.get_attribute("name")
        if name: return name.lower()
        ident = locator.get_attribute("id")
        if ident:
            label = page.locator(f'label[for="{ident}"]')
            if label.count():
                return label.first.inner_text().lower()
    except Exception:
        pass
    return ""

def field_value(label, profile):
    label = re.sub(r"[_\-]+", " ", label.lower())
    mappings = [
        (["first name", "given name"], profile.get("first_name", "")),
        (["last name", "surname", "family name"], profile.get("last_name", "")),
        (["full name", "candidate name", "your name"], profile.get("full_name", "")),
        (["email", "e-mail"], profile.get("email", "")),
        (["phone", "mobile", "contact number"], profile.get("phone", "")),
        (["current company", "employer"], profile.get("current_company", "Cognizant")),
        (["current title", "job title", "designation"], profile.get("current_title", "Automation and Manual Tester")),
        (["linkedin"], profile.get("linkedin_url", "")),
        (["city", "current location", "location"], profile.get("location", "Hyderabad, India")),
        (["years of experience", "total experience", "experience"], profile.get("experience", "1.7 years")),
        (["expected ctc", "expected salary", "salary expectation", "expected compensation"], profile.get("expected_ctc", "750000")),
        (["notice period"], profile.get("notice_period", "")),
    ]
    for terms, value in mappings:
        if value and any(term in label for term in terms):
            return str(value)
    return None

def main(url):
    profile = load_profile()
    if not url.startswith(("http://", "https://")):
        raise SystemExit("Please supply a valid http(s) job URL.")
    print("\nHUMAN-IN-THE-LOOP MODE")
    print("This tool will not submit the application or bypass any site security.")
    print("Do not continue on a site whose terms prohibit this kind of browser assistance.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print("\nIf login, CAPTCHA, OTP, or identity verification appears, complete it yourself.")
        print("The helper will inspect common visible form fields and fill only clear matches.")
        page.wait_for_timeout(1500)
        filled = []
        fields = page.locator("input:not([type=hidden]):not([type=submit]):not([type=button]), textarea")
        for i in range(min(fields.count(), 100)):
            field = fields.nth(i)
            try:
                if not field.is_visible() or not field.is_enabled():
                    continue
                typ = (field.get_attribute("type") or "text").lower()
                if typ in ("password", "file", "checkbox", "radio", "submit", "button", "hidden"):
                    continue
                label = label_for(field, page)
                value = field_value(label, profile)
                if value:
                    field.fill(value, timeout=1500)
                    filled.append((label or f"field #{i+1}", value))
            except Exception:
                continue

        # Resume upload is deliberately left to the user. File controls can have site-specific
        # requirements and should be reviewed before selecting a document.
        print("\nFields filled:")
        if filled:
            for label, value in filled:
                masked = value if "email" not in label else value[:2] + "***"
                print(f" - {label}: {masked}")
        else:
            print(" - No confidently identifiable fields were filled.")
        print("\nPlease inspect every field in the browser, correct errors, and attach the right resume manually.")
        print("Do not submit unless the job is genuine, still open, and all answers are accurate.")
        input("\nAfter you have reviewed the form, press Enter to finish this helper (it will NOT submit): ")
        browser.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python assisted_apply.py <job-url>")
    main(sys.argv[1])
