# Job Scout

A FastAPI web app and Telegram bot that searches job boards and scores roles
against a student's resume. The website and API are served by the same process.

## Deploy to Render

1. Rotate the API keys and Telegram bot token that were previously kept in
   your local `.env` file.
2. In [Render](https://dashboard.render.com/), choose **New** → **Blueprint**
   and connect this GitHub repository.
3. Select the `main` branch. Render reads `render.yaml` and creates the
   `job-scout` web service.
4. At the secret prompt, enter the fresh `TELEGRAM_BOT_TOKEN` and at least one
   of `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, or `GROQ_API_KEY`.
5. Create the service and wait for the deploy to finish. Open its `onrender.com`
   URL, then verify `/healthz` returns `{\"ok\":true}`.

Render supplies the public URL automatically. It also generates and stores the
Telegram webhook secret; the app derives a Telegram-safe token from it and
registers the webhook on startup.

## Local development

Create `.env` from `.env.example`, add the required values, then run:

```bash
pip install -r requirements.txt
uvicorn api:app --reload --port 8000
```

Open `http://127.0.0.1:8000`. The Telegram webhook is intentionally inactive
locally unless `PUBLIC_URL` and `TELEGRAM_WEBHOOK_SECRET` are set.

## Operations notes

- The website is usable without Telegram; a Telegram token is currently still
  required because the web server also starts the bot.
- `users.json` stores bot profiles on the local filesystem. On Render's free
  plan this data is not durable across restarts. Use persistent storage before
  relying on the bot for saved profiles in production.
