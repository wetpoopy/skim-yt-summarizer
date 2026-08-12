# TLDW — toolazydidntwatch.com

Paste a YouTube link, get back a structured summary: a short bullet view and a
long view, key points you can expand, an inline glossary for jargon, chapters,
and a read on what the comments think. Accounts, a searchable library, and a
background queue for slow clients (like iOS Shortcuts) sit on top.

Live at **https://www.toolazydidntwatch.com**. FastAPI backend + a single-file
vanilla-JS frontend, deployed on Railway with Postgres and Redis.

---

## Quick start (local)

You need **Python 3.11+** and an Anthropic API key. Everything else is optional
— the app degrades gracefully without it (see [Environment](#environment)).

```bash
git clone https://github.com/wetpoopy/skim-yt-summarizer.git
cd skim-yt-summarizer
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); use .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env               # then fill in ANTHROPIC_API_KEY and JWT_SECRET
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. The frontend and API are the same process, so
there's no separate build step or dev server to run.

Generate a `JWT_SECRET` with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

With no `DATABASE_URL` set, it uses a local SQLite file (`dev.db`) and creates
the schema on first boot — so a fresh clone works with no database to install.

---

## Environment

Only the first two are needed to run locally. See `.env.example` for the full
annotated list.

| Variable | Required? | What breaks without it |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | **Yes** | Nothing summarizes |
| `JWT_SECRET` | **Yes** | Signup/login return a 500 |
| `DATABASE_URL` | No | Falls back to local SQLite (`dev.db`) |
| `YOUTUBE_API_KEY` | No | No title/channel/views/likes/duration metadata |
| `REDIS_URL` | No | Rate limiter is in-memory, resets on restart |
| `RESEND_API_KEY` | No | Password reset + daily digest emails don't send |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` | No | Only needed to use GPT/Gemini instead of Claude |
| `WEBSHARE_PROXY_USERNAME` / `_PASSWORD` | Prod only | Transcripts fail in production (see below) |

**Never commit `.env`** — it's gitignored. Share real keys through a password
manager, not Slack/email/commits.

### The production IP-blocking gotcha

YouTube blocks most datacenter IP ranges from fetching transcripts. It works
locally on your home connection and then fails once deployed. Fix is a
residential proxy — `youtube-transcript-api`'s Webshare integration is already
wired up, just set `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD`. A few
dollars a month covers this volume.

---

## Architecture

```
app/
  main.py              FastAPI app: /summarize, /summarize/queue, /history,
                       /glossary, /playlist, static file serving
  auth.py              Signup/login, JWT cookies, API tokens, preferences,
                       password reset endpoints
  models.py            SQLAlchemy models (User, Summary, PendingSummary,
                       ApiToken, CustomGlossaryTerm, PasswordResetCode)
  db.py                Engine/session setup + the additive auto-migration
  summarizer.py        Prompt construction, multi-provider dispatch
                       (Claude/GPT/Gemini), response parsing
  transcript.py        YouTube URL -> transcript, with retries
  youtube_metadata.py  Title/channel/views/likes/duration via YouTube Data API
  youtube_comments.py  Top comments, for the sentiment read
  playlist.py          Playlist expansion + per-provider cost estimate
  ratelimit.py         Redis-backed per-IP limiter, in-memory fallback
  digest.py            Daily digest email job (APScheduler)
  email.py             Shared Resend send helper
  static/index.html    The entire frontend — HTML, CSS, and JS in one file
```

### Two things worth knowing before you change anything

**1. The frontend is one file, on purpose.** `app/static/index.html` is ~3,800
lines of HTML + CSS + inline JS with no build step. It's served directly. That
means no bundler and no npm install, but also that two people editing it will
conflict — coordinate before you both touch it.

**2. Migrations are automatic and additive-only.** There's no Alembic. On every
boot, `_ensure_columns()` in `db.py` diffs the models against the live schema
and applies safe changes: adding missing columns, widening `INTEGER`→`BIGINT`,
and widening `VARCHAR(n)` to a longer `n`. It never drops or narrows anything,
and a failed statement is logged rather than raised so a bad migration can't
take the site down.

So: **adding a nullable column or widening one is safe — just change the model
and deploy.** Anything else (renames, drops, type changes, backfills, NOT NULL)
has to be done by hand against the database.

---

## Working on this together

### One-time setup for a new collaborator

1. **GitHub** — repo owner goes to the repo → *Settings* → *Collaborators* →
   *Add people*. Do this for both repos if they're working on mobile too:
   - `wetpoopy/skim-yt-summarizer` (this — web app + API)
   - `wetpoopy/tldw-mobile` (React Native / Expo app)
2. **Railway** — *Project Settings* → *Members* → invite by email. This is what
   grants access to production logs, environment variables, and the database.
   Note that Railway charges per seat on some plans; if you'd rather not add a
   seat, the owner can keep deploys and log access to themselves and the other
   person can still develop fully against local SQLite.
3. **Secrets** — send API keys through a password manager (1Password, Bitwarden),
   never through chat or a commit. Or have each person use their own Anthropic
   key locally so there's nothing to share at all.

### Day-to-day workflow

`master` is what's deployed. Don't commit to it directly.

```bash
git checkout master && git pull
git checkout -b your-name/short-description
# ...make changes...
git add -A && git commit -m "Describe the change"
git push -u origin your-name/short-description
```

Then open a PR on GitHub, get a quick look from the other person, and merge.

Before pushing, sanity-check what you changed:

```bash
python -m py_compile app/*.py
node -e "new Function(require('fs').readFileSync('app/static/index.html','utf8').match(/<script>([\s\S]*)<\/script>/)[1])"
```

The second one catches JS syntax errors in the frontend, which are otherwise
invisible until the page silently breaks in a browser.

### Deploying

Currently deploys are manual, from a machine with the Railway CLI linked:

```bash
railway up --service skim-yt-summarizer --detach
```

**This is the main thing to fix now that there are two of you.** With manual
deploys, whoever runs `railway up` ships *their local working tree* — including
uncommitted changes, and silently clobbering whatever the other person just
deployed. Switch to GitHub auto-deploy instead: in Railway → service →
*Settings* → *Source*, connect the GitHub repo and set the deploy branch to
`master`. After that, merging a PR deploys, and local state can never reach
production by accident.

### Verifying a deploy actually landed

Railway can report "Online" while the old container is still serving traffic,
and its edge caches HTML. So don't trust the dashboard alone:

```bash
# 1. wait until status shows Online with no Building/Deploying
railway status

# 2. confirm the new code is actually being served (bypass the edge cache)
curl -s -H "Cache-Control: no-cache" https://www.toolazydidntwatch.com/ | grep -c "some-string-you-just-added"

# 3. check the app booted without errors
railway logs --service skim-yt-summarizer --since 5m | grep -i "error\|traceback"
```

---

## Useful endpoints

| Endpoint | Notes |
| --- | --- |
| `GET /health` | Liveness check |
| `POST /summarize` | Synchronous. Takes ~20–40s. Body: `{"url": "..."}` |
| `POST /summarize/queue` | Fire-and-forget; returns instantly. For iOS Shortcuts, which iOS kills after ~30s |
| `GET /summarize/pending` | In-flight and failed queued jobs |
| `POST /summarize/pending/{id}/retry` | Re-run a failed job |
| `GET /history` | The library. Supports `?status=`, `?q=`, `?category=`, `?sort=` |
| `GET /history/export?format=json\|csv\|markdown` | Export everything |
| `GET /glossary` | Every term across all your summaries |

Auth is a JWT cookie from `/auth/login`, or `Authorization: Bearer <token>`
using a personal API token from *Actions → API tokens* (that's what the iOS
Shortcut uses).

---

## Gotchas that have bitten us

- **iOS auto-zooms** on any input with `font-size` under 16px, and the page can
  get stuck zoomed. There's a global 16px rule on inputs — don't lower it.
- **Native `<button>`s show an un-suppressible amber press state on iOS.** The
  key-point and SHORT/LONG toggles are deliberately `<div role="button">`
  instead. Don't "fix" them back into buttons.
- **No regex lookbehind in the frontend.** WebKit only supports it from iOS
  16.4, and older versions throw at `new RegExp` — which took down summary
  rendering entirely once.
- **`Summary.category` holds a comma-joined *list*** ("AI & Machine Learning,
  Courses & Tutorials, Science"), not one label. It outgrew its column once and
  broke every save; it's `String(255)` now and capped in `normalize_category()`.
- **Background jobs need their own DB session.** The request-scoped session is
  closed by the time a `BackgroundTasks` function runs.
