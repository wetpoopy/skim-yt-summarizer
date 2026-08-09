# YT Summarizer — MVP API

Minimal FastAPI backend: send a YouTube URL, get a transcript-based summary
back. This is the framework — summary quality/format is intentionally basic
(`app/summarizer.py` → `build_prompt()`) and meant to be refined later.

## Setup (local)

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 — the frontend and API are served from the same
process (no CORS setup needed for normal use).

## Deploy to Railway

1. Push this folder to a GitHub repo (Railway deploys from GitHub).
2. In Railway: **New Project → Deploy from GitHub repo** → select the repo.
3. Railway auto-detects Python from `requirements.txt` and uses the
   `Procfile` to know how to start it — no extra config needed.
4. In the Railway project → **Variables** tab, add:
   - `ANTHROPIC_API_KEY` = your Anthropic API key
   - (once you hit IP-blocking — see below) `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD`
5. Railway gives you a live URL (Settings → Networking → Generate Domain)
   once it deploys. Open it — that's your live app.
6. On your phone: open that URL in Safari → Share → **Add to Home Screen**.
   You now have a home-screen icon that opens full-screen, no browser
   chrome — functionally an app for this use case.

## Endpoints

- `GET /health` — liveness check
- `POST /summarize` — body: `{"url": "https://youtube.com/watch?v=..."}`
  - Optional header `x-anthropic-key`: caller's own Anthropic key. Bypasses
    the shared free-tier rate limit (this is the "bring your own key" path).
  - Free tier (no header): rate-limited per IP, `FREE_TIER_DAILY_LIMIT` in
    `app/ratelimit.py` (currently 8/day).

Response:
```json
{
  "video_id": "dQw4w9WgXcQ",
  "summary": "...",
  "language": "en",
  "remaining_today": 7
}
```

## ⚠️ Before you deploy publicly: the IP-blocking issue

**YouTube blocks most datacenter/cloud IP ranges** from fetching transcripts
(Vercel, Railway, Fly.io, AWS, GCP, etc). This is a known, common problem —
not specific to this code. It'll work fine when you test locally on your
home connection and then fail once deployed.

Fix: route transcript requests through a residential proxy. The code already
supports this via `youtube-transcript-api`'s built-in Webshare integration —
just set these env vars once deployed:

```
WEBSHARE_PROXY_USERNAME=...
WEBSHARE_PROXY_PASSWORD=...
```

A basic residential proxy plan (a few $/month) is enough for this volume.
Without it, expect `IpBlocked`/`RequestBlocked` errors in production.

## Architecture

```
app/
  main.py         FastAPI app, /summarize endpoint, request/response models
  transcript.py   YouTube URL -> transcript (youtube-transcript-api)
  summarizer.py   transcript -> summary (Claude API)
  ratelimit.py    in-memory per-IP rate limiter (swap for Redis at scale)
```

## Known limitations (by design, for now)

- Rate limiter is in-memory — resets on restart, doesn't work across
  multiple server instances. Fine for a single-process MVP; swap for
  Redis (INCR/EXPIRE) when you need to scale horizontally.
- No auth/accounts yet — rate limiting is IP-based only.
- No transcript chunking — very long videos get truncated to
  `MAX_TRANSCRIPT_CHARS` in `summarizer.py` rather than split and
  map-reduced. Fine for most content, revisit for multi-hour videos.
- CORS is wide open (`allow_origins=["*"]`) — tighten to your actual
  frontend domain before you publicize this.
- Summary prompt is intentionally basic — refine `build_prompt()` in
  `summarizer.py` next.

## Next steps (not built yet)

- Minimal frontend (paste URL, see summary)
- iOS Shortcut that POSTs to `/summarize` and shows the result
- Prompt refinement (formats: TLDR/bullets/chapters, tone controls)
