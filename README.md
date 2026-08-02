# Job Scout

A FastAPI backend and Telegram bot that searches job boards and scores roles
against a student's resume, plus a static frontend for the website. The two
halves are deployed separately:

- `backend/` → Render (the API + Telegram webhook)
- `frontend/` → Vercel (the website)

They talk to each other over the network (CORS + a URL you set once each side
is live), so deploy the backend first - you need its URL for the frontend.

## 1. Deploy the backend to Render

1. Rotate the API keys and Telegram bot token that were previously kept in
   your local `.env` file.
2. In [Render](https://dashboard.render.com/), choose **New** → **Blueprint**
   and connect this GitHub repository.
3. Select the `main` branch. Render reads `render.yaml`, sets the service's
   root directory to `backend/`, and creates the `job-scout` web service.
4. At the secret prompt, enter the fresh `TELEGRAM_BOT_TOKEN` and at least one
   of `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, or `GROQ_API_KEY`.
5. Create the service and wait for the deploy to finish. Open its `onrender.com`
   URL + `/healthz` and check it returns `{"ok":true}`. **Copy this URL** -
   you need it for step 2 below.

Render supplies the public URL automatically. It also generates and stores the
Telegram webhook secret; the app derives a Telegram-safe token from it and
registers the webhook on startup.

## 2. Deploy the frontend to Vercel

1. In [`frontend/config.js`](frontend/config.js), replace
   `https://YOUR-BACKEND-NAME.onrender.com` with the Render URL from step 1,
   then commit and push.
2. In [Vercel](https://vercel.com/), choose **Add New** → **Project** and
   import this GitHub repository.
3. Set **Root Directory** to `frontend` (Vercel needs this since the repo has
   both `backend/` and `frontend/` folders). No build command is needed - it's
   a static site.
4. Deploy, then open the Vercel URL and try a search.
5. Back in the Render dashboard, set the `FRONTEND_URL` env var to this Vercel
   URL (this restricts the backend to only accept requests from your site).
   Render redeploys automatically.

## Local development

Two servers, in two terminals:

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn api:app --reload --port 8000
```

```bash
# Frontend (any static file server works)
cd frontend
python3 -m http.server 5500
```

Create `backend/.env` from `backend/.env.example` first, with the required
values. Open `http://127.0.0.1:5500` - `frontend/config.js` already points it
at `http://localhost:8000`. The Telegram webhook is intentionally inactive
locally unless `PUBLIC_URL` and `TELEGRAM_WEBHOOK_SECRET` are set.

## Operations notes

- The website is usable without Telegram; a Telegram token is currently still
  required because the web server also starts the bot.
- `backend/users.json` stores bot profiles on the local filesystem. On
  Render's free plan this data is not durable across restarts. Use persistent
  storage before relying on the bot for saved profiles in production.
