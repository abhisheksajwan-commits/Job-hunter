# Job Scout — MVP Spec, Roadmap & Startup Pitch

*Updated 2 Aug 2026. The MVP described in Part 1 is built (`bot.py`) and running.*

---

## Part 1 — The MVP (built ✅)

**Product in one sentence:** a Telegram bot a student talks to once, that then
hunts openings across job boards and delivers ranked, explained matches to
their feed — so they stop searching and just apply.

### The user journey

```
Student                          Job Scout bot
   │  /start                          │
   │────────────────────────────────▶│  "What are you looking for? Say it in plain words."
   │  "PM internship, remote or      │
   │   Bangalore, paid"              │
   │────────────────────────────────▶│  AI turns this into a search (term + location)
   │                                 │  "Got it. Now paste your resume (or /skip)."
   │  [pastes resume text]           │
   │────────────────────────────────▶│  Searches Indeed + LinkedIn + Naukri,
   │                                 │  AI scores EVERY opening vs THEIR profile
   │                                 │
   │◀────────────────────────────────│  Top 5 cards, each with:
   │                                 │   • title, company, location, date posted
   │                                 │   • salary/stipend (when the board lists it)
   │                                 │   • fit score /100 + one-line why
   │                                 │   • 2-line summary of the role
   │                                 │   • link → student applies THEMSELVES
   │  /search anytime for fresh      │
   │  results; new text = new search │
```

### Deliberate MVP boundaries
- **No auto-apply.** Boards ban it, accounts get flagged, and spray-and-pray
  gets worse response rates. The bot removes the *searching*, not the
  *choosing* — students approach companies themselves.
- **Resume as pasted text** (PDF parsing is a later upgrade).
- **Profiles in a local file** (`users.json`) — a real database only when there
  are real users.
- **Boards:** Indeed ✅ and LinkedIn ✅ respond today; Naukri currently blocks
  robots (captcha) — the bot skips any board that doesn't answer and uses the
  rest, so one blocked board never breaks the product.

### What exists in this folder
| File | What it is |
|------|------------|
| `bot.py` | **The MVP** — the conversational bot described above |
| `main.py` | The v0 one-shot script (scrape → score → send). Still useful for scheduled digests later |
| `users.json` | The bot's memory of each student (created on first use) |
| `STRATEGY.md` | This document |

Run the bot: `python bot.py` — stays on until stopped, and needs the laptop
awake. Cloud hosting fixes that (Part 2, step 4).

---

## Part 2 — Free upgrade ladder (what's next, in order)

1. **Instant alerts** — the bot re-checks boards a few times a day and pushes
   ONLY new-since-last-time matches (needs a small dedupe memory). This is the
   moment it becomes a *feed* rather than a search you trigger.
2. **Application packs** — per job, AI drafts a tailored cover letter + 3
   "why I fit" bullets on demand (`/letter 2`). Apply in 30 seconds instead of
   20 minutes.
3. **PDF resume upload** — most students have a PDF, not text.
4. **Cloud hosting** — free/cheap tier so nothing depends on your laptop.
5. **Naukri** via official/partner routes once volume justifies it.

All of the above stays ₹0 or nearly so (Groq free tier, JobSpy free, Telegram free).

---

## Part 3 — Scaling it into a product

| Phase | What | Cost | Goal |
|-------|------|------|------|
| 0 (now) | MVP bot — already multi-user; friends can message it today | ₹0 | Journey feels smooth |
| 1 | 10–50 beta users from your circle; weekly feedback; watch retention | ₹0 | Proof people want it |
| 2 | Cloud hosting, instant alerts, PDF resumes, referral loop ("share with 3 friends → 1 week Pro") | ~₹500–1,500/mo | 1,000+ users, retention data |
| 3 | Monetize (below); move scraping toward official/partner feeds as revenue allows | scales with revenue | Real business |

### Paid plans (Phase 3)
- **Free** — 1 search profile, daily top-5 digest. Costs ~₹5–15/user/month to
  serve — cheap enough to be the growth engine.
- **Pro — ₹149/month or ₹999/year** (student-priced): unlimited search profiles,
  instant alerts, unlimited AI cover letters, ATS resume score + fixes, per-job
  interview prep packs.
- **Placement Cell / B2B — per-college licence:** dashboards for training &
  placement officers (who's applying where, skill gaps). Colleges already pay
  for placement tooling; highest-margin lane.
- **Later, employer side:** curated candidate shortlists for startups hiring
  interns — turns it into a two-sided marketplace.

### Honest risk list
- **Board blocking/ToS** — Naukri's captcha today is the preview. Mitigation:
  multiple boards with graceful fallback (already built), official
  APIs/partnerships once revenue exists, and never auto-submitting for users.
- **Big incumbents** (Internshala, Unstop, Naukri Campus, LinkedIn) — they are
  *portals you visit*; you are an *agent in the student's pocket*.
  Personalization + zero install is the wedge, not job inventory.
- **LLM cost at scale** — currently trivial (small models score jobs well);
  reserve big models for paid features like cover letters.

---

## Part 4 — The startup pitch (one-pager draft)

**Working name:** *ScoutJi* (or JobScout India — test names with users)

**One-liner:** A Telegram agent that hunts internships for Indian students,
scores every opening against *their* resume, and delivers ranked matches with
pay and fit explained — applying takes 30 seconds instead of 30 minutes of
portal-surfing.

**Problem:** ~10 million students graduate in India every year. Internship
search means refreshing 5 portals, keyword filters that don't understand fit,
and generic mass-applications that get ignored. Placement support outside top
colleges is thin.

**Solution:** Zero-install — Telegram is already on their phone. Describe what
you want in plain words → paste resume once → ranked matches with salary,
summary, and "why you fit" arrive in chat. The student stays the applicant;
the agent does the drudgery.

**Why now:** LLMs made per-user job-fit scoring essentially free (a few paise
per job). Chat apps make distribution free. Neither was true 3 years ago.

**Business model:** Freemium (₹149/mo Pro) + per-college B2B licences.

**Go-to-market:** Campus ambassadors + placement cells + WhatsApp/Telegram
study groups. Job links get forwarded anyway — every forwarded card is an ad.

**Traction plan (first 6 months):** Month 1–2: 50 beta users, measure weekly
retention. Month 3–4: public bot, 1,000 users, first Pro subscribers. Month
5–6: 2 college placement-cell pilots. Decide seed-raise vs bootstrap on that
data.

**The ask (if raising):** angel/₹25–50L pre-seed for 12 months: 1 founder + 1
engineer + infra + campus-ambassador stipends.
