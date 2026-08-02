"""
JOB SCOUT API - api.py

THIS is the one process you actually deploy (e.g. to Render). It runs a single
web server that does two jobs at once:

  1. Telegram bot, via WEBHOOK instead of polling - Telegram pushes messages
     to us at /telegram-webhook instead of us constantly asking "anything
     new?". This matters for free hosting: a polling bot looks "idle" to
     free web-service tiers and gets put to sleep; a webhook bot only wakes
     up when a real message arrives, which counts as normal web traffic.

  2. The website's backend - a small REST API the frontend (in static/) calls
     to run a search and get results, using the exact same engine.py that
     powers the Telegram bot, so results are identical either way.

It also serves the frontend itself (the static/ folder) from the same
process, so the whole product is ONE deployable thing, ONE free Render
service, ONE dashboard.

Run it locally for testing:   uvicorn api:app --reload --port 8000
(The Telegram webhook only gets registered if PUBLIC_URL and
TELEGRAM_WEBHOOK_SECRET are set in .env - without them this still runs fine
for testing the website's search API on its own.)
"""

import asyncio
import hashlib
import hmac
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from http import HTTPStatus

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, HTTPException, Form, UploadFile, File
from fastapi.staticfiles import StaticFiles
from telegram import Update

import bot
import engine

FOLDER = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(FOLDER, "static")
load_dotenv(os.path.join(FOLDER, ".env"))  # engine.py also loads this; harmless twice

log = logging.getLogger("jobscout.api")

WEBHOOK_PATH = "/telegram-webhook"
# Render's generated secret is base64, whereas Telegram accepts only
# [A-Za-z0-9_-] in a webhook secret token. Hashing produces a fixed, safe
# 64-character token without exposing the stored secret.
_webhook_secret_value = os.getenv("TELEGRAM_WEBHOOK_SECRET")
WEBHOOK_SECRET = (
    hashlib.sha256(_webhook_secret_value.encode()).hexdigest()
    if _webhook_secret_value else None
)
# Render supplies this automatically for web services. PUBLIC_URL remains an
# optional override for a custom domain or another hosting platform.
PUBLIC_URL = os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL")

application = bot.build_application(for_webhook=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with application:
        await application.start()
        if PUBLIC_URL and WEBHOOK_SECRET:
            await application.bot.set_webhook(
                url=PUBLIC_URL.rstrip("/") + WEBHOOK_PATH,
                secret_token=WEBHOOK_SECRET,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            log.info(f"Telegram webhook registered at {PUBLIC_URL}{WEBHOOK_PATH}")
        else:
            log.warning("PUBLIC_URL / TELEGRAM_WEBHOOK_SECRET not set in .env - "
                        "Telegram webhook NOT registered (fine for local API-only testing).")
        yield
        await application.stop()


app = FastAPI(title="Job Scout API", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# --- Telegram webhook -------------------------------------------------------


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    if WEBHOOK_SECRET:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not token or not hmac.compare_digest(token, WEBHOOK_SECRET):
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="invalid secret token")
    data = await request.json()
    update = Update.de_json(data=data, bot=application.bot)
    await application.update_queue.put(update)
    return Response(status_code=HTTPStatus.OK)


# --- Website search API -------------------------------------------------

MAX_CONCURRENT_JOBS = 5          # protects the free tier's thin CPU/RAM
JOB_TTL_SECONDS = 15 * 60        # stop remembering a search after 15 minutes
SEARCH_WINDOW_SECONDS = 10 * 60  # basic quota protection for the public API
MAX_SEARCHES_PER_IP = 3

JOBS = {}  # job_id -> {"status", "result", "error", "created_at"}
SEARCH_REQUESTS = defaultdict(deque)  # client IP -> monotonic request times


def _prune_old_jobs():
    cutoff = time.monotonic() - JOB_TTL_SECONDS
    for job_id in [j for j, v in JOBS.items() if v["created_at"] < cutoff]:
        del JOBS[job_id]


def _check_search_rate_limit(request: Request):
    """Reject bursts that could exhaust job-board or AI-provider quotas.

    Render forwards the originating address in X-Forwarded-For. Falling back
    to Request.client also keeps this useful during local development.
    """
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded_for.split(",", 1)[0].strip()
    if not client_ip and request.client:
        client_ip = request.client.host
    client_ip = client_ip or "unknown"

    now = time.monotonic()
    timestamps = SEARCH_REQUESTS[client_ip]
    while timestamps and timestamps[0] <= now - SEARCH_WINDOW_SECONDS:
        timestamps.popleft()
    if len(timestamps) >= MAX_SEARCHES_PER_IP:
        raise HTTPException(
            status_code=429,
            detail="Search limit reached. Please wait a few minutes and try again.",
        )
    timestamps.append(now)


async def _run_search_job(job_id, search_term, location, resume, requirement):
    try:
        JOBS[job_id]["status"] = "scraping"
        jobs, boards_ok = await engine.scrape_all_boards(search_term, location)
        if not jobs:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["result"] = {
                "boards_ok": boards_ok, "linkedin": [], "others": [], "near": [],
                "note": "No board returned openings just now. Try a broader search.",
            }
            return

        JOBS[job_id]["status"] = "scoring"
        jobs = await asyncio.to_thread(engine.score_all_jobs, jobs, resume, requirement)

        if not any(j["score"] for j in jobs):
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["result"] = {
                "boards_ok": boards_ok, "linkedin": [], "others": jobs[:5], "near": [],
                "note": ("The AI couldn't score these right now (out of free quota "
                        "for the moment) - here are the newest ones, unscored."),
            }
            return

        linkedin, others, near, note = engine.bucket_results(jobs)
        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["result"] = {
            "boards_ok": boards_ok, "linkedin": linkedin, "others": others,
            "near": near, "note": note,
        }
    except Exception as error:
        log.exception(f"search job {job_id} failed")
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(error)[:300]


@app.post("/api/search")
async def api_search(
    request: Request,
    role: str = Form(..., description="e.g. 'product manager internship'"),
    location: str = Form("India"),
    resume_text: str = Form(""),
    resume_file: UploadFile | None = File(None),
):
    role = role.strip()[:200]
    if not role:
        raise HTTPException(status_code=422, detail="Tell us what role/internship you're looking for.")
    location = (location or "India").strip()[:100] or "India"

    resume = resume_text.strip()
    if len(resume) > 6000:
        raise HTTPException(status_code=422, detail="Pasted resume text must be 6,000 characters or fewer.")
    if resume_file is not None and resume_file.filename:
        if resume_file.content_type != "application/pdf" and not resume_file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=422, detail="Resume file must be a PDF.")
        file_bytes = await resume_file.read()
        if len(file_bytes) > engine.MAX_PDF_BYTES:
            raise HTTPException(status_code=422,
                                detail=f"That PDF is too large - keep it under "
                                       f"{engine.MAX_PDF_BYTES // (1024 * 1024)} MB.")
        extracted = await asyncio.to_thread(engine.extract_pdf_text, file_bytes)
        if len(extracted) < engine.MIN_PDF_TEXT_CHARS:
            raise HTTPException(status_code=422,
                                detail="Couldn't read text from that PDF - it might be a "
                                       "scanned image. Try pasting your resume as text instead.")
        resume = extracted

    _prune_old_jobs()
    _check_search_rate_limit(request)
    in_progress = sum(1 for j in JOBS.values() if j["status"] in ("scraping", "scoring"))
    if in_progress >= MAX_CONCURRENT_JOBS:
        raise HTTPException(status_code=429,
                            detail="Lots of searches running right now - try again in a moment.")

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "queued", "result": None, "error": None,
                    "created_at": time.monotonic()}
    asyncio.create_task(_run_search_job(job_id, role, location, resume, role))
    return {"job_id": job_id}


@app.get("/api/search/{job_id}")
async def api_search_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired search.")
    return {"status": job["status"], "result": job["result"], "error": job["error"]}


# --- Frontend (static site) ---------------------------------------------
# Must be mounted LAST - it's a catch-all for any path not already matched
# by a route above, which is exactly what we want for a single-page app.

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
