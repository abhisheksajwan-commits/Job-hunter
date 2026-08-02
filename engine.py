"""
JOB SCOUT ENGINE - engine.py

The shared "brain and legs" of Job Scout: everything that actually finds and
scores jobs. Nothing in this file knows about Telegram or the web - both
bot.py (the Telegram bot) and api.py (the website's backend) import from here,
so a search behaves identically no matter which front door the student used.

What's in here:
  - The AI brain chain (tries Gemini, then Claude, then Groq - whichever keys
    are in .env - so one provider being out of quota never breaks a search)
  - parse_search(): understands a plain-English request
  - scrape_all_boards(): searches Indeed, Naukri and LinkedIn at once
  - score_all_jobs(): ONE AI call scores every opening against a resume
  - extract_pdf_text(): reads a resume out of an uploaded PDF
  - posted_ago(): "2026-07-31" -> "2d ago"
"""

import asyncio
import io
import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from jobspy import scrape_jobs
from pypdf import PdfReader

# --- Settings ---------------------------------------------------------------

# Two Gemini models: a fast one for quick decisions, a smarter (Pro) one for
# judgement calls. Pro needs BILLING enabled on the API key - this module
# checks at startup and uses it automatically if it's available.
GEMINI_FAST_MODEL = "gemini-3.1-flash-lite"   # free, ~1s - quick decisions
GEMINI_SMART_MODEL = "gemini-3.5-flash"       # free, ~3s - better judgement
GEMINI_PRO_MODEL = "gemini-3.1-pro-preview"   # paid only, smartest of all
CLAUDE_MODEL = "claude-opus-5"
GROQ_MODEL = "llama-3.1-8b-instant"
BOARDS = ["indeed", "naukri", "linkedin"]  # blocked boards are skipped
JOBS_PER_BOARD = 10
HOURS_OLD = 96
LINKEDIN_RESULTS = 15                      # LinkedIn gets a deeper scan...
LINKEDIN_HOURS = 120                       # ...over the last 5 days
GOOD_SCORE = 60                            # a genuinely good match
NEAR_SCORE = 40                            # worth a look, shown only to pad a thin day
MIN_RESULTS = 3                            # if fewer good matches than this, add near-misses
MAX_LINKEDIN = 5                           # at most this many LinkedIn posts...
MAX_OTHERS = 3                             # ...plus this many from other boards
MAX_PDF_BYTES = 5 * 1024 * 1024            # 5 MB - a resume is never bigger than this
MIN_PDF_TEXT_CHARS = 40                    # below this, treat as an unreadable/scanned PDF

FOLDER = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(FOLDER, ".env"))

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("jobscout.engine")

# --- The AI brains --------------------------------------------------------
# Every key in .env becomes a brain. There are two chains:
#   FAST    - quick decisions (understanding what the student typed)
#   QUALITY - judgement calls (scoring jobs against a resume)
# Each chain is tried in order, so if one brain is out of quota or down, the
# next one answers and the caller never sees a failure.


def _friendly(error):
    """Turn a provider error into something worth logging."""
    text = str(error)
    if "limit: 0" in text:
        return "not included in this key's plan (needs billing enabled)"
    if "429" in text or "RESOURCE_EXHAUSTED" in text or "rate_limit" in text:
        return "out of quota / rate limited"
    if "404" in text:
        return "model not available to this key"
    return text[:160]


def _make_gemini_json(model):
    """Build the JSON-calling function for one Gemini model."""
    def _json(prompt, schema, max_tokens):
        response = gemini.models.generate_content(
            model=model, contents=prompt,
            # response_json_schema actually enforces the shape (response_mime_type
            # alone only guarantees valid JSON, not the right keys/types).
            # 'low' thinking keeps replies fast; the output budget must be
            # generous because Gemini's own thinking counts against it too
            config={"response_mime_type": "application/json",
                    "response_json_schema": schema, "temperature": 0.2,
                    "max_output_tokens": max(max_tokens, 2000),
                    "thinking_config": {"thinking_level": "low"}},
        )
        return json.loads(response.text) if response.text else None
    return _json


def _gemini_pro_works():
    """One tiny call to see whether this key may use the paid Pro model.
    Free-tier keys get 'limit: 0' here; keys with billing on get a reply."""
    try:
        gemini.models.generate_content(
            model=GEMINI_PRO_MODEL, contents="hi",
            config={"max_output_tokens": 800,
                    "thinking_config": {"thinking_level": "low"}},
        )
        return True
    except Exception as error:
        log.info(f"Gemini Pro unavailable: {_friendly(error)}. "
                 f"Using {GEMINI_FAST_MODEL} instead — turn on billing in Google "
                 "AI Studio and restart to unlock Pro automatically.")
        return False


def _claude_json(prompt, schema, max_tokens):
    response = claude.messages.create(
        model=CLAUDE_MODEL, max_tokens=max(max_tokens, 4000),
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        return None
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def _groq_json(prompt, schema, max_tokens):
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL, response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt + "\nReply with ONLY the JSON."}],
        temperature=0.2, max_tokens=max_tokens,
    )
    return json.loads(response.choices[0].message.content)


READY = {}  # key -> (label, json_function) for every usable brain

if os.getenv("GEMINI_API_KEY"):
    from google import genai
    gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    READY["gemini_fast"] = (f"gemini({GEMINI_FAST_MODEL})", _make_gemini_json(GEMINI_FAST_MODEL))
    READY["gemini_smart"] = (f"gemini({GEMINI_SMART_MODEL})", _make_gemini_json(GEMINI_SMART_MODEL))
    if _gemini_pro_works():
        READY["gemini_pro"] = (f"gemini({GEMINI_PRO_MODEL})", _make_gemini_json(GEMINI_PRO_MODEL))

if os.getenv("ANTHROPIC_API_KEY"):
    from anthropic import Anthropic
    claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    READY["claude"] = (f"claude({CLAUDE_MODEL})", _claude_json)

if os.getenv("GROQ_API_KEY"):
    from groq import Groq
    groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    READY["groq"] = (f"groq({GROQ_MODEL})", _groq_json)

# Quick decisions: cheapest/fastest first. Judgement calls: smartest first.
BRAINS_FAST = [READY[k] for k in
               ("gemini_fast", "groq", "gemini_smart", "claude", "gemini_pro") if k in READY]
BRAINS_QUALITY = [READY[k] for k in
                  ("gemini_pro", "claude", "gemini_smart", "gemini_fast", "groq") if k in READY]
BRAINS = BRAINS_FAST  # callers check this to confirm at least one key exists


def ask_json(prompt, schema, max_tokens=1500, quality=False):
    """Ask each brain in turn for JSON until one answers. None if all fail."""
    for name, json_fn in (BRAINS_QUALITY if quality else BRAINS_FAST):
        try:
            result = json_fn(prompt, schema, max_tokens)
            if result is not None:
                return result
            log.warning(f"{name} returned nothing, trying next brain")
        except Exception as error:
            log.warning(f"{name} failed ({_friendly(error)}), trying next brain")
    log.error("All brains failed on a JSON call")
    return None


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "is_job_search": {"type": "boolean"},
        "search_term": {"type": "string"},
        "location": {"type": "string"},
    },
    "required": ["is_job_search", "search_term", "location"],
    "additionalProperties": False,
}

SCORES_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "score": {"type": "integer"},
                    "requirements": {"type": "string"},
                    "fit_points": {"type": "string"},
                },
                "required": ["i", "score", "requirements", "fit_points"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scores"],
    "additionalProperties": False,
}


# --- Small helpers ------------------------------------------------------------


def posted_ago(date_str):
    """'2026-07-31' -> 'today' / 'yesterday' / '3 days ago'. '' if unparseable."""
    try:
        posted = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return ""
    days = (datetime.now().date() - posted).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days}d ago"


def extract_pdf_text(file_bytes):
    """PDF bytes -> extracted text, or '' if nothing readable (e.g. the PDF
    is a scanned image with no text layer, or the file is corrupt)."""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as error:
        log.warning(f"PDF extraction failed: {error}")
        return ""


# --- The search pipeline -------------------------------------------------


def parse_search(text):
    """Brain: does this look like a job/internship request? If so, extract
    job-board search settings from it. Used for free-text input (Telegram);
    the web form collects search_term/location directly and can skip this."""
    data = ask_json(f"""This app ONLY finds jobs and internships. A user just
sent this message. Decide whether it describes a job/internship they want
(is_job_search=true) or is something else, like a greeting or an unrelated
question (is_job_search=false). If true, extract a short job-board search
query and a location.

search_term: 2-5 word job board query ("" if is_job_search is false).
location: city name, or "India" if unspecified ("" if is_job_search is false).

User's message: {text[:500]}

JSON with keys is_job_search, search_term, location.""", SEARCH_SCHEMA, max_tokens=200)
    if not isinstance(data, dict):
        return {"is_job_search": False, "search_term": "", "location": ""}
    # job boards want a short query - keep at most 5 words, drop stray commas
    data["search_term"] = " ".join(
        str(data.get("search_term", "")).replace(",", " ").split()[:5])[:60]
    data["is_job_search"] = bool(data.get("is_job_search"))
    return data


def scrape_one_board(board, search_term, location):
    """One board -> list of job dicts. Empty list if blocked or nothing found."""
    jobs = []
    try:
        # LinkedIn is the priority source: deeper scan, longer window, and we
        # fetch full descriptions so the AI can score those posts properly
        df = scrape_jobs(
            site_name=[board], search_term=search_term, location=location,
            results_wanted=LINKEDIN_RESULTS if board == "linkedin" else JOBS_PER_BOARD,
            hours_old=LINKEDIN_HOURS if board == "linkedin" else HOURS_OLD,
            country_indeed="India",
            linkedin_fetch_description=(board == "linkedin"),
        )
        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                def clean(field):
                    v = row.get(field)
                    return "" if v is None or str(v) == "nan" else str(v)
                def pay():
                    lo, hi = row.get("min_amount"), row.get("max_amount")
                    lo = None if lo is None or str(lo) == "nan" else lo
                    hi = None if hi is None or str(hi) == "nan" else hi
                    if lo is None and hi is None:
                        return ""
                    fmt = lambda x: f"{int(float(x)):,}"
                    amount = (f"{fmt(lo)}–{fmt(hi)}" if lo is not None and hi is not None
                              else fmt(lo if lo is not None else hi))
                    per = {"yearly": "/yr", "monthly": "/mo", "weekly": "/wk",
                           "daily": "/day", "hourly": "/hr"}.get(
                        str(row.get("interval") or "").lower(), "")
                    currency = clean("currency")
                    return f"{currency} {amount}{per}".strip()
                jobs.append({
                    "title": clean("title") or "(no title)",
                    "company": clean("company") or "(company not named)",
                    "location": clean("location"), "url": clean("job_url"),
                    "board": board, "pay": pay(), "date": clean("date_posted"),
                    "description": clean("description")[:700],
                })
    except Exception as error:
        log.warning(f"{board} failed: {error}")
    return jobs


async def scrape_all_boards(search_term, location):
    """All boards AT THE SAME TIME - total wait = slowest board, not the sum."""
    per_board = await asyncio.gather(
        *[asyncio.to_thread(scrape_one_board, b, search_term, location) for b in BOARDS])
    boards_ok = [board for board, jobs in zip(BOARDS, per_board) if jobs]
    seen, unique = set(), []
    for j in (job for jobs in per_board for job in jobs):
        key = (j["title"].lower(), j["company"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique, boards_ok


def score_all_jobs(jobs, resume, requirement):
    """ONE brain call scores every job (this is what fixed the rate-limit bug).
    Asks for KEYWORD-style fields (not prose) so results read fast on a phone."""
    profile = f"What the student wants: {requirement}\n"
    profile += f"Their resume:\n{resume[:3000]}" if resume else "(No resume - judge by the stated requirement.)"
    listing = "\n\n".join(
        f"[{i}] {j['title']} | {j['company']} | {j['location']} | {j['pay'] or 'pay not listed'}\n"
        f"{j['description'] or '(no description)'}"
        for i, j in enumerate(jobs)
    )
    data = ask_json(f"""You are a strict recruitment assistant for an Indian student.
Score EVERY opening below 0-100 for how well it fits this student. Openings
unrelated to what they asked for must score below 40.

STUDENT:
{profile}

OPENINGS:
{listing}

For each opening also extract short comma-separated TAGS (1-3 words each, like
hashtags without the #) so a student can scan it in 2 seconds. Never write full
sentences or clauses here - single words or tiny phrases only.
- requirements: 3-6 tags for the skills/tools/experience-level this role needs.
  Example: "SQL, Figma, 2-4 yrs exp, Agile, stakeholder mgmt"
- fit_points: 3-5 tags on why it scores this way, from the student's own
  background. Example: "PM skills match, wrong seniority, no fintech exp"

JSON: {{"scores": [{{"i": <index>, "score": <0-100>,
"requirements": "<tag1, tag2, tag3>",
"fit_points": "<tag1, tag2, tag3>"}} , ... one per opening]}}""",
                    SCORES_SCHEMA, max_tokens=4000, quality=True)
    scored = {}
    if isinstance(data, dict) and isinstance(data.get("scores"), list):
        for s in data["scores"]:
            try:
                scored[int(s["i"])] = s
            except (KeyError, TypeError, ValueError):
                continue
    for i, job in enumerate(jobs):
        s = scored.get(i, {})
        try:
            job["score"] = max(0, min(100, int(float(s.get("score", 0)))))
        except (TypeError, ValueError):
            job["score"] = 0
        job["requirements"] = str(s.get("requirements", "")).strip()
        job["fit_points"] = str(s.get("fit_points", "")).strip() or "Could not score this one."
        job["posted_ago"] = posted_ago(job["date"])
    return jobs


def bucket_results(jobs):
    """Split scored jobs into LinkedIn / other-board / near-miss buckets,
    the same triage logic both the bot and the API use to build a results
    view. Returns (linkedin, others, near, note)."""
    by_score = lambda js: sorted(js, key=lambda j: j["score"], reverse=True)
    good = [j for j in jobs if j["score"] >= GOOD_SCORE]
    linkedin = by_score([j for j in good if j["board"] == "linkedin"])[:MAX_LINKEDIN]
    others = by_score([j for j in good if j["board"] != "linkedin"])[:MAX_OTHERS]

    near, note = [], ""
    shortfall = MIN_RESULTS - (len(linkedin) + len(others))
    if shortfall > 0:
        near = by_score([j for j in jobs
                         if NEAR_SCORE <= j["score"] < GOOD_SCORE])[:shortfall]
    if not linkedin and not others:
        if near:
            note = (f"Nothing scored {GOOD_SCORE}+ today — here are the closest ones. "
                    "Try again tomorrow or broaden your search.")
        else:
            note = ("Nothing matched well today, not even loosely. Try a broader "
                    "description (e.g. drop a specific tool or location) or check back tomorrow.")
    return linkedin, others, near, note
