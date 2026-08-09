# Task: Deploy "Skim" (YouTube summarizer) to Railway

## Context
I have a working FastAPI app (backend + a static HTML frontend served from
the same app) called "Skim" — paste a YouTube link, get a transcript-based
summary back. It's fully built and tested, just needs to go from local
files to a live Railway deployment.

The project folder is at: `~/Downloads/yt-summarizer` (adjust path if I
put it somewhere else — I downloaded it from a Claude.ai chat).

## What I need you to do

1. **Verify the project runs locally first.**
   - `cd` into the project folder, create a virtualenv, `pip install -r requirements.txt`
   - I'll provide my Anthropic API key — set it as `ANTHROPIC_API_KEY` in
     a local `.env` (there's a `.env.example` showing the format) or export it
   - Run `uvicorn app.main:app --reload` and confirm `http://127.0.0.1:8000`
     loads the frontend and `http://127.0.0.1:8000/health` returns `{"status":"ok"}`
   - Fix anything that doesn't work before moving on — don't push broken code.

2. **Push it to GitHub.**
   - Init a git repo in the project folder if one doesn't exist
   - Create a new **private** GitHub repo (use `gh repo create` if the
     GitHub CLI is available and I'm authenticated; otherwise walk me
     through creating one on github.com and give me the exact commands
     to add it as a remote)
   - Commit and push. The `.gitignore` already excludes `.env` and
     `__pycache__` — do NOT commit my API key.

3. **Deploy to Railway.**
   - I have a Railway account (signed up already). Check if the Railway
     CLI is installed; if not, help me install it (`npm i -g @railway/cli`
     or the platform equivalent) and log in (`railway login`).
   - Link this project to a new Railway project (`railway init` or via
     the GitHub-connected deploy flow — whichever is more reliable).
   - Set the `ANTHROPIC_API_KEY` environment variable in Railway to my key
     (ask me for it, don't guess or reuse a placeholder).
   - Deploy and confirm the build succeeds — the project has a `Procfile`
     and `runtime.txt` already set up for this, so it should auto-detect
     correctly as a Python app.
   - Generate a public domain for the service (Railway: Settings →
     Networking → Generate Domain) and give me the live URL.

4. **Smoke-test the live deployment.**
   - Hit `<url>/health` and confirm it returns `{"status":"ok"}`
   - Open `<url>/` and confirm the frontend loads
   - Try submitting a real YouTube URL through the form and see what
     happens. If it fails with an IP-blocked/RequestBlocked error, that's
     expected — YouTube blocks most cloud IPs by default. Tell me it
     happened and stop there; I'll decide separately whether to add a
     residential proxy (Webshare env vars are already wired up for this
     in `app/transcript.py`, documented in the README).

5. **Tell me when it's done**, with:
   - The GitHub repo URL
   - The live Railway URL
   - Whether the real-video test worked or hit the IP-blocking issue

## Notes
- Don't modify the app code unless something is actually broken — I want
  to review any logic changes myself before they go live.
- If you hit a decision point that affects cost (e.g. Railway plan tier,
  proxy service signup), ask me first rather than picking for me.
