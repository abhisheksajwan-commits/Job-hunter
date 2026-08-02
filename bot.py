"""
JOB SCOUT BOT - bot.py  (v4 - shares engine.py with the website)

The user journey, in plain English:
  1. Student sends /start -> bot greets them and asks what they're looking for.
  2. Student describes it in plain words (e.g. "product manager internship,
     remote or Bangalore, paid").
  3. Bot asks for their resume - paste as text OR upload a PDF (or /skip).
     /resume lets them update it anytime, the same way.
  4. Bot searches Indeed + Naukri + LinkedIn, AI-scores every opening against
     the student's resume, and replies with ONE message: the best matches as
     compact, scannable cards (role, company, location, how long ago posted,
     salary if listed, key requirements, AI score + why, and the link).
  5. Student can just type a new description anytime to search again.

All the actual searching/scoring lives in engine.py, shared with the website's
backend (api.py) - this file is just the Telegram conversation on top of it.

Two ways to run this:
  - In PRODUCTION, api.py imports build_application(for_webhook=True) and
    drives it via Telegram webhooks (see api.py) - that's the deployed setup.
  - For LOCAL TESTING ONLY, run this file directly for old-fashioned polling,
    no webhook/public URL needed:   python bot.py
"""

import asyncio
import json
import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import engine

# --- Settings ---------------------------------------------------------------

MAX_MSG = 4096                             # Telegram's hard message limit
FOLDER = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(FOLDER, "users.json")

log = logging.getLogger("jobscout.bot")

# --- Remembering each user ---------------------------------------------------


def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f:
            return json.load(f)
    return {}


def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(USERS, f, indent=2)


USERS = load_users()
# Two migrations applied on every startup:
#  1. A crash mid-job (process killed, power cut) can leave "busy": true
#     persisted to disk. Nothing is actually running on a fresh start, so
#     clear it - otherwise that user would be told "still working" forever.
#  2. v2 had extra stages (letter/feedback flows, now removed) - map any of
#     those onto the v3 stage names so nobody gets stuck mid-conversation.
_STAGE_MIGRATION = {
    "menu": "idle", "js_need_requirement": "idle", "js_need_resume": "need_resume",
    "cl_need_resume": "idle", "cl_need_job": "idle", "rf_need_resume": "idle",
}
for _profile in USERS.values():
    _profile["busy"] = False
    _profile["stage"] = _STAGE_MIGRATION.get(_profile.get("stage"), _profile.get("stage", "idle"))


def user(chat_id):
    key = str(chat_id)
    if key not in USERS:
        USERS[key] = {"stage": "idle", "requirement": "", "search_term": "",
                      "location": "India", "resume": ""}
    # older profiles (from before a field was added) may be missing any of these
    USERS[key].setdefault("stage", "idle")
    USERS[key].setdefault("requirement", "")
    USERS[key].setdefault("search_term", "")
    USERS[key].setdefault("location", "India")
    USERS[key].setdefault("resume", "")
    USERS[key].setdefault("busy", False)
    return USERS[key]


def claim(profile):
    """True if we may start a long-running job for this user right now.
    No 'await' happens between the read and the write below, so on a single
    event loop this check-then-set can't race with another task."""
    if profile.get("busy"):
        return False
    profile["busy"] = True
    return True


# --- Small helpers ------------------------------------------------------------


def escape_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip(text, n):
    text = str(text).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


RESUME_HINT = "paste it as text, or upload it as a PDF file"
RESUME_PROMPT = f"📄 Send me your resume — <b>{RESUME_HINT}</b>."

WELCOME = ("👋 Hi, I'm <b>Job Scout</b>!\n\n"
           "I hunt jobs and internships for Indian students across Indeed, "
           "Naukri &amp; LinkedIn, and score every opening against YOUR resume "
           "so you don't have to read fifty postings to find the five that matter.\n\n"
           "<b>What are you looking for?</b> Say it in plain words, e.g.\n"
           "<i>product manager internship, remote or Bangalore, paid</i>\n\n"
           "<i>(You can update your resume anytime with /resume — text or PDF.)</i>")

NEXT_PROMPT = "🔁 Type a new description anytime to search again."


# --- Telegram-formatted results message ---------------------------------------


def job_lines(rank, job, show_board=False):
    """One opening as a compact, scannable card - keyword-dense, not prose."""
    lines = [f"<b>{rank}. {escape_html(clip(job['title'], 60))}</b> — {escape_html(clip(job['company'], 35))}"]
    meta = " · ".join(x for x in [clip(job["location"], 30),
                                  job["board"] if show_board else "", job["posted_ago"]] if x)
    if meta:
        lines.append(f"📍 {escape_html(meta)}")
    if job["pay"]:
        lines.append(f"💰 {escape_html(job['pay'])}")
    if job["requirements"]:
        lines.append(f"🔑 {escape_html(clip(job['requirements'], 150))}")
    lines.append(f"🎯 <b>{job['score']}/100</b> — {escape_html(clip(job['fit_points'], 150))}")
    lines.append(f'🔗 <a href="{job["url"]}">Open &amp; apply</a>')
    lines.append("")
    return lines


def build_results_message(linkedin, others, near, boards_ok, term, loc, note=""):
    """ALL results in ONE Telegram message: LinkedIn section first, then other
    boards, then near-misses only if the good matches were thin."""
    total = len(linkedin) + len(others)
    lines = [f"<b>🎯 {total} good match{'es' if total != 1 else ''} — “{escape_html(clip(term, 40))}” in {escape_html(clip(loc, 25))}</b>",
             f"<i>Searched {escape_html(', '.join(boards_ok))} · scored against your resume</i>", ""]
    if note:
        lines += [f"<i>{escape_html(note)}</i>", ""]
    rank = 0
    if linkedin:
        lines.append(f"<b>💼 LinkedIn posts (last {engine.LINKEDIN_HOURS // 24} days)</b>")
        lines.append("")
        for job in linkedin:
            rank += 1
            lines += job_lines(rank, job)
    if others:
        lines.append("<b>🌐 From other boards</b>")
        lines.append("")
        for job in others:
            rank += 1
            lines += job_lines(rank, job, show_board=True)
    if near:
        lines.append("<b>🔍 Also worth a look</b> <i>(weaker fit, thin day)</i>")
        lines.append("")
        for job in near:
            rank += 1
            lines += job_lines(rank, job, show_board=True)
    message = "\n".join(lines).strip()
    return message[:MAX_MSG - 10] if len(message) > MAX_MSG else message


async def run_job_search(message, profile):
    term, loc = profile["search_term"], profile["location"]
    await message.reply_text(
        f"🔎 On it! Searching Indeed, Naukri & LinkedIn for “{term}” in {loc}… (~30s)")
    jobs, boards_ok = await engine.scrape_all_boards(term, loc)
    if not jobs:
        profile["stage"] = "idle"
        save_users()
        await message.reply_text(
            "😕 No board returned openings just now. Try a broader description "
            "(e.g. “marketing intern” instead of a very specific title).")
        return
    await message.reply_text(
        f"✅ Found {len(jobs)} openings on {', '.join(boards_ok)}. "
        "Scoring them against your profile…")
    jobs = await asyncio.to_thread(engine.score_all_jobs, jobs, profile["resume"], profile["requirement"])
    profile["stage"] = "idle"

    # If every score came back 0 the AI never actually answered - say so plainly
    # rather than pretending these are "the closest matches".
    if not any(j["score"] for j in jobs):
        save_users()
        await message.reply_text(
            "⚠️ I found the openings but the AI couldn't score them right now "
            "(it's out of free quota for the moment). Here are the newest ones "
            "unscored — or try again in a few minutes for proper matching.")
        newest = jobs[:5]
        plain = "\n".join(
            f"• <b>{escape_html(clip(j['title'], 55))}</b> — {escape_html(clip(j['company'], 30))}\n"
            f'  <a href="{j["url"]}">Open</a>' for j in newest)
        await message.reply_text(plain, parse_mode=ParseMode.HTML,
                                 disable_web_page_preview=True)
        return

    linkedin, others, near, note = engine.bucket_results(jobs)
    save_users()
    await message.reply_text(build_results_message(linkedin, others, near, boards_ok, term, loc, note),
                             parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    await message.reply_text(NEXT_PROMPT)


# --- Conversation wiring --------------------------------------------------------

BUSY_REPLY = "⏳ Still working on your last search — I'll reply here as soon as it's done."


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = user(update.effective_chat.id)
    profile["stage"] = "idle"
    profile["busy"] = False  # /start is the escape hatch if something got stuck
    save_users()
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Just describe the job or internship you want, e.g. \"data analyst "
        "internship in Delhi\".\n\n/resume – update your resume (text or PDF) "
        "anytime\n/skip – skip the resume step\n"
        "/reset – forget my details and start over")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = user(update.effective_chat.id)
    if not claim(profile):
        await update.message.reply_text(BUSY_REPLY)
        return
    try:
        profile["stage"] = "resume_only"
        save_users()
        await update.message.reply_text(RESUME_PROMPT, parse_mode=ParseMode.HTML)
    finally:
        profile["busy"] = False
        save_users()


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    USERS.pop(str(update.effective_chat.id), None)
    save_users()
    await cmd_start(update, context)


async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = user(update.effective_chat.id)
    if profile["stage"] != "need_resume":
        await update.message.reply_text("Nothing to skip right now.")
        return
    if not claim(profile):
        await update.message.reply_text(BUSY_REPLY)
        return
    try:
        await update.message.reply_text("No problem — matching on your requirement alone.")
        await run_job_search(update.message, profile)
    finally:
        profile["busy"] = False
        save_users()


async def _finish_resume_update(message, profile, resume_text, source=""):
    """Store new resume text from either input path (pasted or PDF-extracted).
    If a search description was waiting on this resume ('need_resume'), run
    that search now. Otherwise (an explicit /resume update) just confirm."""
    profile["resume"] = resume_text[:6000]
    continuing_search = profile["stage"] == "need_resume"
    tail = "" if continuing_search else " I'll use it for your next search."
    await message.reply_text(f"Resume saved{source} ✔{tail}")
    if continuing_search:
        save_users()
        await run_job_search(message, profile)
    else:
        profile["stage"] = "idle"
        save_users()


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = user(update.effective_chat.id)
    if not claim(profile):
        await update.message.reply_text(BUSY_REPLY)
        return
    try:
        await _on_text_locked(update, context, profile)
    finally:
        profile["busy"] = False
        save_users()


async def _on_text_locked(update: Update, context: ContextTypes.DEFAULT_TYPE, profile):
    """The actual on_text logic. Only ever runs while profile['busy'] is held,
    so nothing here can be interleaved by a second message from the same user."""
    text = update.message.text.strip()
    stage = profile["stage"]
    try:  # show "typing..." so the bot never looks frozen while the AI thinks
        await context.bot.send_chat_action(update.effective_chat.id, "typing")
    except Exception:
        pass

    if stage in ("need_resume", "resume_only"):
        await _finish_resume_update(update.message, profile, text)
        return

    # idle: any text is either a new search description, or small talk
    parsed = await asyncio.to_thread(engine.parse_search, text)
    if not parsed["is_job_search"]:
        await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)
        return
    profile.update(requirement=text,
                   search_term=(parsed["search_term"] or text[:60]),
                   location=(parsed["location"] or "India"))
    if profile["resume"]:
        save_users()
        await run_job_search(update.message, profile)
    else:
        profile["stage"] = "need_resume"
        save_users()
        await update.message.reply_text(
            f"Got it — “{profile['search_term']}” in {profile['location']}.\n\n"
            f"Now <b>{RESUME_HINT}</b>, so scores match YOUR background — or /skip.",
            parse_mode=ParseMode.HTML)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = user(update.effective_chat.id)
    if not claim(profile):
        await update.message.reply_text(BUSY_REPLY)
        return
    try:
        await _on_document_locked(update, context, profile)
    finally:
        profile["busy"] = False
        save_users()


async def _on_document_locked(update: Update, context: ContextTypes.DEFAULT_TYPE, profile):
    """A PDF was uploaded. Works from any stage - a PDF is unambiguous, so it's
    always treated as 'here is my resume', same rules as _finish_resume_update."""
    doc = update.message.document
    try:
        await context.bot.send_chat_action(update.effective_chat.id, "typing")
    except Exception:
        pass

    if doc.file_size and doc.file_size > engine.MAX_PDF_BYTES:
        await update.message.reply_text(
            f"😕 That file is too large ({doc.file_size // 1024} KB) — please keep "
            f"it under {engine.MAX_PDF_BYTES // (1024 * 1024)} MB, or paste your resume as text instead.")
        return

    tg_file = await doc.get_file()
    file_bytes = bytes(await tg_file.download_as_bytearray())
    text = await asyncio.to_thread(engine.extract_pdf_text, file_bytes)

    if len(text) < engine.MIN_PDF_TEXT_CHARS:
        await update.message.reply_text(
            "😕 I couldn't read text from that PDF — it might be a scanned image "
            "rather than a real text PDF. Please paste your resume as text instead.")
        return

    await _finish_resume_update(update.message, profile, text, source=" from your PDF")


async def on_non_pdf_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "I can only read <b>PDF</b> resumes right now — please upload a PDF, "
        "or paste your resume as text.", parse_mode=ParseMode.HTML)


def build_application(for_webhook=False):
    """Build (but don't start) the Telegram Application, with every handler
    registered. api.py calls this with for_webhook=True and drives it via
    Telegram webhooks; run_locally() below calls it with the default (False)
    for old-fashioned polling during local testing."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("[STOP] TELEGRAM_BOT_TOKEN missing in .env")
    if not engine.BRAINS:
        raise SystemExit("[STOP] No AI key in .env — add GEMINI_API_KEY, "
                         "ANTHROPIC_API_KEY or GROQ_API_KEY")
    builder = Application.builder().token(token).concurrent_updates(True)
    if for_webhook:
        # Tell PTB we'll feed it updates ourselves (via api.py's webhook route)
        # instead of it running its own polling/webhook server.
        builder = builder.updater(None)
    app = builder.build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(MessageHandler(filters.Document.PDF, on_document))
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.Document.PDF, on_non_pdf_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def run_locally():
    """LOCAL TESTING ONLY: run the bot standalone via polling - no webhook,
    no public URL, no api.py needed. Not used in production; Ctrl+C to stop."""
    app = build_application(for_webhook=False)
    log.info("Job Scout bot running LOCALLY via polling (dev mode).")
    log.info("  quick decisions: " + " -> ".join(n for n, _ in engine.BRAINS_FAST))
    log.info("  scoring:         " + " -> ".join(n for n, _ in engine.BRAINS_QUALITY))
    app.run_polling()


if __name__ == "__main__":
    run_locally()
