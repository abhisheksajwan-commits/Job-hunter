"""
STEP 1 - Test the free job scraper (JobSpy)

What this script does, in plain English:
  1. Checks if the JobSpy library is installed. If not, installs it (one-time).
  2. Asks Indeed for 'product manager intern' jobs in India, last 72 hours.
  3. Prints what it found on screen AND saves the full details to
     jobs_test.csv in this same folder, so Claude can read the results.

You never need to edit this file. Just run it.
"""

import subprocess
import sys
import os

FOLDER = os.path.dirname(os.path.abspath(__file__))

print("=" * 60)
print("STEP 1: Testing the free job scraper (JobSpy)")
print("=" * 60)

# --- 1. Make sure JobSpy is installed -------------------------
try:
    from jobspy import scrape_jobs
    print("[OK] JobSpy is already installed")
except ImportError:
    print("Installing JobSpy... (one-time, takes ~30-60 seconds)")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-U", "python-jobspy", "--quiet"]
    )
    from jobspy import scrape_jobs
    print("[OK] JobSpy installed successfully")

# --- 2. Scrape Indeed -----------------------------------------
print()
print("Asking Indeed for 'product manager intern' jobs in India")
print("posted in the last 72 hours... (takes ~10-20 seconds)")
print()

try:
    jobs = scrape_jobs(
        site_name=["indeed"],          # Indeed only: most reliable, no rate limits
        search_term="product manager intern",
        location="India",
        results_wanted=10,             # bring back 10 jobs
        hours_old=72,                  # posted in the last 3 days
        country_indeed="India",        # Indeed needs the country spelled out
    )
except Exception as e:
    print("[FAILED] The scrape hit an error. Copy this to Claude:")
    print()
    print(repr(e))
    sys.exit(1)

# --- 3. Show results + save them for Claude to read ----------
if jobs is None or len(jobs) == 0:
    print("[HMM] 0 jobs came back. Not a crash - the search may just be")
    print("too narrow. Tell Claude '0 jobs' and we'll widen it.")
    sys.exit(0)

print(f"[SUCCESS] Found {len(jobs)} jobs!")
print()
for i, row in jobs.reset_index(drop=True).iterrows():
    title = str(row.get("title", ""))[:50]
    company = str(row.get("company", ""))[:30]
    loc = str(row.get("location", ""))[:25]
    print(f"  {i + 1}. {title}  |  {company}  ({loc})")

out_path = os.path.join(FOLDER, "jobs_test.csv")
jobs.to_csv(out_path, index=False)
print()
print(f"[SAVED] Full details written to: {out_path}")
print("Now just tell Claude 'done' - it can read that file itself.")
