# Setup

## 1. Local prerequisites

- Python 3.11+
- Docker (for Postgres + Redis)
- A Groq API key: https://console.groq.com (free)

## 2. Clone and install

```bash
git clone <this-repo>
cd hr-copilot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit GROQ_API_KEY
```

## 3. Start infra

```bash
docker compose -f docker/docker-compose.yml up -d
# Wait ~10s for postgres health
```

## 4. Seed data + ingest

```bash
# Create tables and extensions (idempotent — init.sql ran on container start)
psql "$POSTGRES_URL" -f data/seed/employees.sql

# Ingest sample policies
python -m app.ingestion.run
```

## 5. Run tests + evals

```bash
pytest tests/           # unit tests — no Postgres/Groq needed
python -m evals.run     # full eval (uses Groq judges)
python -m evals.run --no-judges   # string checks only, zero LLM cost
```

## 6. Slack app configuration

Create a Slack app at https://api.slack.com/apps:

- **Scopes (bot)**: `app_mentions:read`, `chat:write`, `commands`, `im:history`, `im:read`, `users:read`
- **Event subscriptions**:
  - `app_mention`
  - `message.im`
- **Slash command**: `/ask` → request URL = `https://your-host/slack/commands`
- **Interactivity**: request URL = `https://your-host/slack/actions`

For local dev, use ngrok or Cloudflare Tunnel:
```bash
cloudflared tunnel --url http://localhost:3000
```

Set the three Slack secrets in `.env`:
- `SLACK_BOT_TOKEN` (xoxb-…)
- `SLACK_SIGNING_SECRET`
- `SLACK_APP_TOKEN` (xapp-…) — only needed if using Socket Mode

## 7. Run

```bash
uvicorn app.main:app --reload --port 3000
```

DM the bot in Slack, or `@mention` it in a channel.

## 8. Deploy to Render (free)

Push to GitHub, create a Render Web Service pointing at the repo. Build:
`pip install -r requirements.txt`
Start:
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Add env vars from `.env`. Render spins down after 15min idle — mention this
in the demo video and bring it back with a warming ping.
