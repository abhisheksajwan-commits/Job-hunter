"""
JOB SCOUT - main.py

What this script does, in plain English:
  1. Asks Indeed India for 20 'product manager intern' jobs (last 72 hours).
  2. Sends each job to Groq (free AI) with a resume, and gets back a
     0-100 fit score plus a one-line reason.
  3. Sends the top 5 jobs to your Telegram: title, company, score,
     reason, and a clickable link.

How to run it:
  Practice mode (no Telegram message, prints on screen instead):
      python main.py --dry-run
  Real run (sends the Telegram message):
      python main.py

You only ever need to edit ONE thing below: the RESUME text.
"""

import json
import os
import sys
import time

import requests
from dotenv import load_dotenv
from groq import Groq
from jobspy import scrape_jobs

# --- Settings you might want to change later -------------------------------

SEARCH_TERM = "product manager intern"
LOCATION = "India"
JOBS_TO_FETCH = 20        # how many jobs to scrape
HOURS_OLD = 72            # only jobs posted in the last 3 days
TOP_N = 5                 # how many jobs to send to Telegram
GROQ_MODEL = "llama-3.1-8b-instant"

# PLACEHOLDER RESUME - swap this for your real background any time.
# The AI scores every job against this text, so the better it describes
# you, the better the scores.
RESUME = """
Final-year B.Tech student (Computer Science) at a tier-2 Indian college,
graduating 2027. Aiming for product manager / associate PM internships.

Experience:
- Led a 4-person student team that built a campus food-ordering app
  (500+ users). Owned the feature roadmap and user interviews.
- 3-month marketing-analytics internship at a D2C startup: built
  dashboards in Excel/Google Sheets, ran A/B tests on Instagram ads.

Skills: user research, wireframing in Figma, SQL basics, Excel,
agile/scrum, competitor analysis, writing PRDs.
Languages: English, Hindi.
Location: open to remote or relocation anywhere in India.
"""

# ---------------------------------------------------------------------------

FOLDER = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(FOLDER, ".env"))

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID")

DRY_RUN = "--dry-run" in sys.argv


def check_keys():
    """Stop early with a clear message if any key is missing."""
    missing = [
        name
        for name, value in [
            ("GROQ_API_KEY", GROQ_API_KEY),
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("TELEGRAM_USER_ID", TELEGRAM_USER_ID),
        ]
        if not value
    ]
    if missing:
        print(f"[STOP] Missing keys in .env: {', '.join(missing)}")
        sys.exit(1)


def fetch_jobs():
    """Step 1: scrape Indeed India. Returns a list of plain dicts."""
    print(f"[1/3] Asking Indeed for {JOBS_TO_FETCH} '{SEARCH_TERM}' jobs "
          f"in {LOCATION} (last {HOURS_OLD}h)...")
    df = scrape_jobs(
        site_name=["indeed"],
        search_term=SEARCH_TERM,
        location=LOCATION,
        results_wanted=JOBS_TO_FETCH,
        hours_old=HOURS_OLD,
        country_indeed="India",
    )
    if df is None or len(df) == 0:
        print("[STOP] Indeed returned 0 jobs. Try widening the search.")
        sys.exit(1)

    jobs = []
    for _, row in df.iterrows():
        def clean(field):
            value = row.get(field)
            # pandas uses NaN (a float) for empty cells - turn those into ""
            return "" if value is None or str(value) == "nan" else str(value)

        jobs.append({
            "title": clean("title") or "(no title)",
            "company": clean("company") or "(unknown company)",
            "location": clean("location"),
            "url": clean("job_url"),
            "description": clean("description")[:1500],  # keep AI cost tiny
        })
    print(f"      Got {len(jobs)} jobs.")
    return jobs


def score_job(client, job):
    """Step 2: ask Groq for a 0-100 fit score + one-line reason (as JSON)."""
    prompt = f"""You are a strict recruitment assistant. Score how well this
candidate fits this job, 0-100. Relevance to product management matters most;
a great non-PM job for this candidate still scores below 40.

CANDIDATE RESUME:
{RESUME}

JOB:
Title: {job['title']}
Company: {job['company']}
Location: {job['location']}
Description: {job['description']}

Reply with ONLY a JSON object, no other text:
{{"score": <integer 0-100>, "reason": "<one short sentence>"}}"""

    for attempt in (1, 2):  # try twice, then give up gracefully
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=150,
            )
            data = json.loads(response.choices[0].message.content)
            score = max(0, min(100, int(round(float(data["score"])))))
            reason = str(data.get("reason", "")).strip() or "No reason given."
            return score, reason
        except Exception as error:
            if attempt == 2:
                print(f"      [skip] Could not score '{job['title'][:40]}': {error}")
                return 0, "Scoring failed - treated as 0."
            time.sleep(2)


def escape_html(text):
    """Telegram HTML mode needs these three characters escaped."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(top_jobs):
    """Format the top jobs into one Telegram message (HTML)."""
    lines = [f"<b>🎯 Job Scout — top {len(top_jobs)} matches</b>",
             f"<i>Search: {escape_html(SEARCH_TERM)} · {escape_html(LOCATION)} · last {HOURS_OLD}h</i>", ""]
    for rank, job in enumerate(top_jobs, 1):
        lines.append(f"<b>{rank}. {escape_html(job['title'])}</b> — {escape_html(job['company'])}")
        lines.append(f"Score: <b>{job['score']}/100</b>")
        lines.append(f"{escape_html(job['reason'])}")
        lines.append(f'<a href="{job["url"]}">Open job posting</a>')
        lines.append("")
    return "\n".join(lines).strip()


def send_telegram(message):
    """Step 3: send the message to your own Telegram chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_USER_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    body = response.json()
    if not body.get("ok"):
        print(f"[STOP] Telegram rejected the message: {body}")
        sys.exit(1)
    print("      Telegram message sent ✔")


def main():
    check_keys()
    jobs = fetch_jobs()

    print(f"[2/3] Scoring {len(jobs)} jobs with Groq ({GROQ_MODEL})...")
    client = Groq(api_key=GROQ_API_KEY)
    for i, job in enumerate(jobs, 1):
        job["score"], job["reason"] = score_job(client, job)
        print(f"      {i:2d}. [{job['score']:3d}] {job['title'][:45]}")
        time.sleep(0.5)  # stay well under Groq's free-tier rate limit

    top_jobs = sorted(jobs, key=lambda j: j["score"], reverse=True)[:TOP_N]
    message = build_message(top_jobs)

    if DRY_RUN:
        print("[3/3] DRY RUN - would have sent this to Telegram:\n")
        print(message)
    else:
        print(f"[3/3] Sending top {len(top_jobs)} to Telegram...")
        send_telegram(message)

    print("\nDone.")


if __name__ == "__main__":
    main()
