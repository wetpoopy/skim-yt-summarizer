"""
YT Summarizer API — MVP.

POST /summarize {"url": "..."} -> {"summary": "...", ...}

Run locally:
    export ANTHROPIC_API_KEY=sk-ant-...
    uvicorn app.main:app --reload

Free tier: rate-limited by IP. "Bring your own key" support is stubbed
in via the optional x-anthropic-key header — if present, it bypasses
the rate limit and uses the caller's own key.
"""

import csv
import io
import json
import logging
import os
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Literal

from anthropic import Anthropic
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.auth import get_current_user, require_user, router as auth_router
from app.db import SessionLocal, get_db, init_db
from app.digest import send_daily_digests
from app.models import CustomGlossaryTerm, PendingSummary, Summary, User
from app.transcript import extract_video_id, get_transcript, TranscriptError
from app.summarizer import define_terms, normalize_category, summarize, QuotaExceededError, SummarizerError
from app.ratelimit import check_and_record, FREE_TIER_DAILY_LIMIT
from app.youtube_metadata import get_channel_stats, get_video_metadata
from app.youtube_comments import get_top_comments
from app.playlist import estimate_batch_cost, extract_playlist_id, get_playlist_preview

app = FastAPI(title="YT Summarizer", version="0.1.0")
logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler()


def _run_daily_digests():
    db = SessionLocal()
    try:
        send_daily_digests(db)
    finally:
        db.close()


@app.on_event("startup")
def _on_startup():
    init_db()
    if not _scheduler.running:
        _scheduler.add_job(_run_daily_digests, CronTrigger(hour=13, minute=0), id="daily_digest", replace_existing=True)
        _scheduler.start()


# The web frontend is served by this same app, so normal browser use is
# same-origin and never consults CORS at all. Native callers (the Expo
# app, iOS Shortcuts) aren't browsers and don't enforce CORS either. So
# this allowlist exists purely to stop a random third-party site from
# making authenticated cross-origin calls on a logged-in visitor's
# behalf — it was previously "*", which is fine for an unpublicized MVP
# but not for a public repo with a public URL.
#
# Override with a comma-separated ALLOWED_ORIGINS env var to add a
# staging domain or a different local port without touching code.
_DEFAULT_ALLOWED_ORIGINS = [
    "https://www.toolazydidntwatch.com",
    "https://toolazydidntwatch.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", ",".join(_DEFAULT_ALLOWED_ORIGINS)).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # The previous list was just POST/GET, which quietly understated what
    # the API actually uses (PATCH on history items, PUT on preferences,
    # DELETE on summaries/tokens/account). Same-origin requests never hit
    # this, which is why the gap never surfaced.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth_router)


class SummarizeRequest(BaseModel):
    url: str
    playlist_id: str | None = None
    playlist_title: str | None = None
    provider: str | None = None

    @field_validator("url")
    @classmethod
    def looks_like_youtube(cls, v: str) -> str:
        if "youtube.com" not in v and "youtu.be" not in v:
            raise ValueError("That doesn't look like a YouTube URL.")
        return v


class Chapter(BaseModel):
    label: str
    seconds: int


class KeyPoint(BaseModel):
    point: str
    detail: str


class GlossaryEntry(BaseModel):
    term: str
    definition: str
    example: str


class SummarizeResponse(BaseModel):
    id: int | None = None          # the saved Summary row, when logged in
    video_id: str
    summary: str
    category: str
    language: str
    remaining_today: int | None = None
    saved: bool = False
    title: str | None = None
    channel: str | None = None
    channel_id: str | None = None
    published_at: str | None = None
    view_count: int | None = None
    comment_count: int | None = None
    like_count: int | None = None
    duration_seconds: int | None = None
    subscriber_count: int | None = None
    channel_stats: dict | None = None
    sentiment_label: str | None = None
    sentiment_blurb: str | None = None
    comment_tally: dict | None = None
    highlight: str | None = None
    counterpoint: str | None = None
    title_answer: str | None = None
    true_title: str | None = None
    tags: list[str] = []
    chapters: list[Chapter] = []
    key_points: list[KeyPoint] = []
    glossary: list[GlossaryEntry] = []
    playlist_id: str | None = None
    playlist_title: str | None = None
    already_summarized: bool = False


class HistoryItem(BaseModel):
    id: int
    video_id: str
    url: str
    summary: str
    category: str
    language: str
    created_at: datetime
    title: str | None = None
    channel: str | None = None
    channel_id: str | None = None
    published_at: str | None = None
    view_count: int | None = None
    comment_count: int | None = None
    like_count: int | None = None
    duration_seconds: int | None = None
    subscriber_count: int | None = None
    channel_stats: dict | None = None
    sentiment_label: str | None = None
    sentiment_blurb: str | None = None
    comment_tally: dict | None = None
    highlight: str | None = None
    counterpoint: str | None = None
    title_answer: str | None = None
    true_title: str | None = None
    tags: list[str] = []
    chapters: list[Chapter] = []
    key_points: list[KeyPoint] = []
    glossary: list[GlossaryEntry] = []
    status: str = "unread"
    feedback: dict | None = None
    playlist_id: str | None = None
    playlist_title: str | None = None


class StatusUpdate(BaseModel):
    status: Literal["unread", "read", "archived"]


class PlaylistVideo(BaseModel):
    video_id: str
    title: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    already_summarized: bool = False


class PlaylistPreviewResponse(BaseModel):
    playlist_id: str
    playlist_title: str
    videos: list[PlaylistVideo]
    estimated_cost: dict[str, float]


@app.get("/health")
def health():
    return {"status": "ok"}


def _summary_to_response(r: Summary, remaining: int | None = None) -> SummarizeResponse:
    """Builds a SummarizeResponse from an already-saved Summary row, for
    the dedup short-circuit — skips re-fetching the transcript/metadata
    and re-running the AI call entirely for a video the user already has."""
    chapters = json.loads(r.chapters_json) if r.chapters_json else []
    key_points = json.loads(r.key_points_json) if r.key_points_json else []
    glossary = json.loads(r.glossary_json) if r.glossary_json else []
    return SummarizeResponse(
        id=r.id,
        video_id=r.video_id,
        summary=r.summary_text,
        category=r.category,
        language=r.language,
        remaining_today=remaining,
        saved=True,
        title=r.title,
        channel=r.channel,
        channel_id=r.channel_id,
        published_at=r.published_at,
        view_count=r.view_count,
        comment_count=r.comment_count,
        like_count=r.like_count,
        duration_seconds=r.duration_seconds,
        subscriber_count=r.subscriber_count,
        channel_stats=json.loads(r.channel_stats_json) if r.channel_stats_json else None,
        sentiment_label=r.sentiment_label,
        sentiment_blurb=r.sentiment_blurb,
        comment_tally=json.loads(r.comment_tally_json) if r.comment_tally_json else None,
        highlight=r.highlight,
        counterpoint=r.counterpoint,
        title_answer=r.title_answer,
        true_title=r.true_title,
        tags=json.loads(r.tags_json) if r.tags_json else [],
        chapters=[Chapter(**c) for c in chapters],
        key_points=[KeyPoint(**k) for k in key_points],
        glossary=[GlossaryEntry(**g) for g in glossary],
        playlist_id=r.playlist_id,
        playlist_title=r.playlist_title,
        already_summarized=True,
    )


def _summarize_and_save(
    body: SummarizeRequest, user: User | None, db: Session, client: Anthropic | None
) -> SummarizeResponse:
    """
    The actual summarize pipeline — transcript, metadata, comments, the LLM
    call, and (for logged-in users) saving to history. Raises TranscriptError
    / QuotaExceededError / SummarizerError on failure; callers decide how to
    surface those (HTTP error for the synchronous endpoint, silently dropped
    for the background/queued path, which has no one left to answer).
    """
    if user is not None:
        try:
            existing_video_id = extract_video_id(body.url)
        except TranscriptError:
            existing_video_id = None
        if existing_video_id:
            existing = db.scalar(
                select(Summary).where(Summary.user_id == user.id, Summary.video_id == existing_video_id)
            )
            if existing is not None:
                return _summary_to_response(existing, remaining=None)

    transcript_data = get_transcript(body.url)

    length = (user.summary_length if user else None) or "standard"
    fmt = (user.summary_format if user else None) or "mixed"
    provider = body.provider or (user.ai_provider if user else None) or "anthropic"

    metadata = get_video_metadata(transcript_data["video_id"]) or {}
    comments = get_top_comments(transcript_data["video_id"])
    channel_stats = get_channel_stats(metadata["channel_id"]) if metadata.get("channel_id") else None
    subscriber_count = channel_stats.get("subscriber_count") if channel_stats else None

    result = summarize(
        transcript_data["text"],
        client=client,
        length=length,
        format=fmt,
        comments=comments,
        title=metadata.get("title"),
        description=metadata.get("description"),
        provider=provider,
        transcript_segments=transcript_data.get("segments"),
        known_tags=_known_tags_for(db, user),
    )

    chapters = result.get("chapters") or []
    key_points = result.get("key_points") or []
    glossary = result.get("glossary") or []

    glossary_text = " ".join(f"{g['term']} {g.get('definition', '')}" for g in glossary)
    result["category"] = normalize_category(
        result["category"],
        title=metadata.get("title"),
        extra_text=f"{result.get('summary', '')} {glossary_text}",
    )

    saved = False
    saved_id = None
    if user is not None:
        row = Summary(
            user_id=user.id,
            video_id=transcript_data["video_id"],
            url=body.url,
            summary_text=result["summary"],
            category=result["category"],
            language=transcript_data["language"],
            title=metadata.get("title"),
            channel=metadata.get("channel"),
            channel_id=metadata.get("channel_id"),
            published_at=metadata.get("published_at"),
            view_count=metadata.get("view_count"),
            comment_count=metadata.get("comment_count"),
            like_count=metadata.get("like_count"),
            duration_seconds=metadata.get("duration_seconds"),
            subscriber_count=subscriber_count,
            channel_stats_json=json.dumps(channel_stats) if channel_stats else None,
            sentiment_label=result.get("sentiment_label"),
            sentiment_blurb=result.get("sentiment_blurb"),
            comment_tally_json=json.dumps(result["comment_tally"]) if result.get("comment_tally") else None,
            highlight=result.get("highlight"),
            counterpoint=result.get("counterpoint"),
            title_answer=result.get("answer"),
            true_title=result.get("true_title"),
            tags_json=json.dumps(result["tags"]) if result.get("tags") else None,
            chapters_json=json.dumps(chapters) if chapters else None,
            key_points_json=json.dumps(key_points) if key_points else None,
            glossary_json=json.dumps(glossary) if glossary else None,
            status="unread",
            playlist_id=body.playlist_id,
            playlist_title=body.playlist_title,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        saved_id = row.id
        saved = True

    return SummarizeResponse(
        id=saved_id,
        video_id=transcript_data["video_id"],
        summary=result["summary"],
        category=result["category"],
        language=transcript_data["language"],
        saved=saved,
        title=metadata.get("title"),
        channel=metadata.get("channel"),
        channel_id=metadata.get("channel_id"),
        published_at=metadata.get("published_at"),
        view_count=metadata.get("view_count"),
        comment_count=metadata.get("comment_count"),
        like_count=metadata.get("like_count"),
        duration_seconds=metadata.get("duration_seconds"),
        subscriber_count=subscriber_count,
        channel_stats=channel_stats,
        sentiment_label=result.get("sentiment_label"),
        sentiment_blurb=result.get("sentiment_blurb"),
        comment_tally=result.get("comment_tally"),
        highlight=result.get("highlight"),
        counterpoint=result.get("counterpoint"),
        title_answer=result.get("answer"),
        true_title=result.get("true_title"),
        tags=result.get("tags") or [],
        chapters=chapters,
        key_points=key_points,
        glossary=glossary,
        playlist_id=body.playlist_id,
        playlist_title=body.playlist_title,
    )


@app.post("/summarize", response_model=SummarizeResponse)
def summarize_video(
    body: SummarizeRequest,
    request: Request,
    x_anthropic_key: str | None = Header(default=None),
    user: User | None = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    remaining = None

    # Bring-your-own-key callers and logged-in users skip the shared rate
    # limit — it exists to cap cost exposure from anonymous visitors, not
    # to throttle the account owner's own usage.
    if x_anthropic_key:
        client = Anthropic(api_key=x_anthropic_key)
    elif user is not None:
        client = None  # summarize() will build the server's own client
    else:
        client_ip = request.client.host if request.client else "unknown"
        allowed, remaining = check_and_record(client_ip, FREE_TIER_DAILY_LIMIT)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Free tier limit of {FREE_TIER_DAILY_LIMIT} summaries/day reached. "
                    "Try again tomorrow, or pass your own Claude API key via the "
                    "x-anthropic-key header."
                ),
            )
        client = None  # summarize() will build the server's own client

    try:
        response = _summarize_and_save(body, user, db, client)
    except TranscriptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except QuotaExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except SummarizerError as e:
        raise HTTPException(status_code=502, detail=str(e))

    response.remaining_today = remaining
    return response


def _run_summarize_in_background(
    body: SummarizeRequest, user_id: int, pending_id: int, client: Anthropic | None
) -> None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        pending = db.get(PendingSummary, pending_id)
        if user is None or pending is None:
            return
        try:
            _summarize_and_save(body, user, db, client)
        except (TranscriptError, QuotaExceededError, SummarizerError) as e:
            pending.status = "failed"
            pending.error = str(e)
            db.commit()
            return
        except Exception:
            # Anything NOT one of the three expected summarizer errors
            # (a network blip, an unexpected API response shape, a DB
            # hiccup) used to fall straight through uncaught — Starlette
            # just logs it and the task ends, leaving this row stuck at
            # status="processing" forever with no way to ever clear it.
            # Catch broadly so the row always resolves to something the
            # user can see and dismiss.
            logger.exception("Unexpected error in background summarize job (pending_id=%s)", pending_id)
            db.rollback()
            pending = db.get(PendingSummary, pending_id)
            if pending is not None:
                pending.status = "failed"
                pending.error = "Something went wrong while summarizing this video. Try again."
                db.commit()
            return
        # Succeeded — the real Summary row exists now, so the placeholder
        # can go away.
        db.delete(pending)
        db.commit()
    finally:
        db.close()


class PendingSummaryOut(BaseModel):
    id: int
    video_id: str
    url: str
    status: str
    error: str | None = None
    created_at: datetime


@app.post("/summarize/queue", response_model=PendingSummaryOut)
def queue_summarize_video(
    body: SummarizeRequest,
    background_tasks: BackgroundTasks,
    x_anthropic_key: str | None = Header(default=None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Fire-and-forget variant for callers that can't wait out a slow request
    (e.g. an iOS Shortcut run from the Share Sheet, which iOS kills after
    roughly 30 seconds — well under how long a real summarize call takes).
    Returns almost immediately; the result lands in the account's history
    once the background job finishes, same as a normal /summarize call.
    """
    try:
        video_id = extract_video_id(body.url)
    except TranscriptError as e:
        raise HTTPException(status_code=422, detail=str(e))

    pending = PendingSummary(user_id=user.id, video_id=video_id, url=body.url, status="processing")
    db.add(pending)
    db.commit()
    db.refresh(pending)

    client = Anthropic(api_key=x_anthropic_key) if x_anthropic_key else None
    background_tasks.add_task(_run_summarize_in_background, body, user.id, pending.id, client)
    return pending


@app.get("/summarize/pending", response_model=list[PendingSummaryOut])
def list_pending_summaries(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(PendingSummary).where(PendingSummary.user_id == user.id).order_by(PendingSummary.created_at.desc())
    ).all()
    return rows


@app.delete("/summarize/pending/{pending_id}")
def dismiss_pending_summary(pending_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row = db.scalar(select(PendingSummary).where(PendingSummary.id == pending_id, PendingSummary.user_id == user.id))
    if not row:
        raise HTTPException(status_code=404, detail="Not found.")
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.post("/summarize/pending/{pending_id}/retry", response_model=PendingSummaryOut)
def retry_pending_summary(
    pending_id: int,
    background_tasks: BackgroundTasks,
    x_anthropic_key: str | None = Header(default=None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Re-run a job that failed, reusing the same row so the queue doesn't
    accumulate a duplicate card per attempt. Some failures are transient
    (a flaky metadata/comments fetch, a provider hiccup) and just work on
    a second run; others are permanent for that video (captions
    disabled), and retrying those simply fails the same way again, which
    is fine — the user can see that and dismiss it.
    """
    row = db.scalar(select(PendingSummary).where(PendingSummary.id == pending_id, PendingSummary.user_id == user.id))
    if not row:
        raise HTTPException(status_code=404, detail="Not found.")
    if row.status == "processing":
        raise HTTPException(status_code=409, detail="That job is already running.")

    row.status = "processing"
    row.error = None
    db.commit()
    db.refresh(row)

    client = Anthropic(api_key=x_anthropic_key) if x_anthropic_key else None
    background_tasks.add_task(
        _run_summarize_in_background, SummarizeRequest(url=row.url), user.id, row.id, client
    )
    return row


def _row_to_history_item(r: Summary) -> HistoryItem:
    chapters = json.loads(r.chapters_json) if r.chapters_json else []
    key_points = json.loads(r.key_points_json) if r.key_points_json else []
    glossary = json.loads(r.glossary_json) if r.glossary_json else []
    return HistoryItem(
        id=r.id,
        video_id=r.video_id,
        url=r.url,
        summary=r.summary_text,
        category=r.category,
        language=r.language,
        created_at=r.created_at,
        title=r.title,
        channel=r.channel,
        channel_id=r.channel_id,
        published_at=r.published_at,
        view_count=r.view_count,
        comment_count=r.comment_count,
        like_count=r.like_count,
        duration_seconds=r.duration_seconds,
        subscriber_count=r.subscriber_count,
        channel_stats=json.loads(r.channel_stats_json) if r.channel_stats_json else None,
        sentiment_label=r.sentiment_label,
        sentiment_blurb=r.sentiment_blurb,
        comment_tally=json.loads(r.comment_tally_json) if r.comment_tally_json else None,
        highlight=r.highlight,
        counterpoint=r.counterpoint,
        title_answer=r.title_answer,
        true_title=r.true_title,
        tags=json.loads(r.tags_json) if r.tags_json else [],
        chapters=chapters,
        key_points=key_points,
        glossary=glossary,
        status=r.status or "unread",
        feedback=json.loads(r.feedback_json) if r.feedback_json else None,
        playlist_id=r.playlist_id,
        playlist_title=r.playlist_title,
    )


@app.get("/playlist/preview", response_model=PlaylistPreviewResponse)
def playlist_preview(url: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    playlist_id = extract_playlist_id(url)
    if not playlist_id:
        raise HTTPException(status_code=422, detail="That doesn't look like a playlist URL (no ?list= found).")

    preview = get_playlist_preview(playlist_id)
    if preview is None:
        raise HTTPException(
            status_code=502,
            detail="Couldn't load that playlist. It may be private, empty, or the server is missing YOUTUBE_API_KEY.",
        )

    already_ids = set(
        db.scalars(
            select(Summary.video_id).where(
                Summary.user_id == user.id,
                Summary.video_id.in_([v["video_id"] for v in preview["videos"]]),
            )
        ).all()
    )
    for v in preview["videos"]:
        v["already_summarized"] = v["video_id"] in already_ids

    estimated_cost = estimate_batch_cost(preview["videos"])
    return PlaylistPreviewResponse(
        playlist_id=preview["playlist_id"],
        playlist_title=preview["playlist_title"],
        videos=[PlaylistVideo(**v) for v in preview["videos"]],
        estimated_cost=estimated_cost,
    )


@app.get("/history", response_model=list[HistoryItem])
def get_history(
    date_filter: date | None = Query(default=None, alias="date"),
    category: str | None = None,
    status: Literal["unread", "read", "archived"] = "unread",
    q: str | None = None,
    playlist_id: str | None = None,
    sort: Literal["newest", "oldest", "category", "channel"] = "newest",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if status == "unread":
        query = select(Summary).where(
            Summary.user_id == user.id,
            (Summary.status == "unread") | (Summary.status.is_(None)),
        )
    else:
        query = select(Summary).where(Summary.user_id == user.id, Summary.status == status)

    if date_filter is not None:
        day_start = datetime.combine(date_filter, time.min, tzinfo=timezone.utc)
        day_end = datetime.combine(date_filter, time.max, tzinfo=timezone.utc)
        query = query.where(Summary.created_at >= day_start, Summary.created_at <= day_end)
    if category:
        query = query.where(Summary.category.ilike(f"%{category}%"))
    if q:
        like = f"%{q}%"
        query = query.where(
            Summary.title.ilike(like) | Summary.summary_text.ilike(like) | Summary.channel.ilike(like)
        )
    if playlist_id:
        query = query.where(Summary.playlist_id == playlist_id)

    if sort == "oldest":
        query = query.order_by(Summary.created_at.asc())
    elif sort == "category":
        query = query.order_by(Summary.category.asc(), Summary.created_at.desc())
    elif sort == "channel":
        query = query.order_by(Summary.channel.asc(), Summary.created_at.desc())
    else:
        query = query.order_by(Summary.created_at.desc())

    rows = db.scalars(query).all()
    return [_row_to_history_item(r) for r in rows]


@app.patch("/history/{summary_id}", response_model=HistoryItem)
def update_history_status(
    summary_id: int,
    body: StatusUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Summary).where(Summary.id == summary_id, Summary.user_id == user.id))
    if not row:
        raise HTTPException(status_code=404, detail="Summary not found.")
    row.status = body.status
    db.commit()
    return _row_to_history_item(row)


# The Questions tab. Answers are explicit preference data — the one signal
# that genuinely can't be reconstructed later, so it's stored as given and
# never inferred. Keys/values are validated against QUESTIONS so a typo or
# a stale client can't quietly poison the dataset.
QUESTIONS = [
    {
        "key": "worth_it",
        "prompt": "Was this worth your time?",
        "options": [("definitely", "Definitely"), ("mostly", "Mostly"), ("not_really", "Not really"), ("no", "No")],
    },
    {
        # The core question for this product: you summarized it *instead*
        # of watching, so the useful signal is whether the summary was
        # enough or whether it earned the full watch.
        "key": "watch_plan",
        "prompt": "Still going to watch it?",
        "options": [
            ("will_watch", "Yes — worth watching"),
            ("maybe", "Maybe later"),
            ("summary_enough", "No — summary covered it"),
        ],
    },
    {
        "key": "novelty",
        "prompt": "How much was new to you?",
        "options": [("all_new", "All new"), ("some_new", "Some new"), ("knew_most", "Knew most")],
    },
    {
        "key": "channel_again",
        "prompt": "More from this channel?",
        "options": [("yes", "Yes"), ("maybe", "Maybe"), ("never", "Never")],
    },
    {
        "key": "will_act",
        "prompt": "Will you act on this?",
        "options": [("already_did", "Already did"), ("plan_to", "Plan to"), ("no", "No")],
    },
    {
        "key": "depth",
        "prompt": "Right level of depth?",
        "options": [("too_shallow", "Too shallow"), ("just_right", "Just right"), ("too_deep", "Too deep")],
    },
]

_VALID_ANSWERS = {q["key"]: {value for value, _ in q["options"]} for q in QUESTIONS}


class FeedbackUpdate(BaseModel):
    answers: dict[str, str]


@app.get("/questions", include_in_schema=False)
def list_questions():
    """The question set, so the client never hardcodes its own copy."""
    return [
        {"key": q["key"], "prompt": q["prompt"],
         "options": [{"value": v, "label": l} for v, l in q["options"]]}
        for q in QUESTIONS
    ]


@app.patch("/history/{summary_id}/feedback", response_model=HistoryItem)
def update_history_feedback(
    summary_id: int,
    body: FeedbackUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Summary).where(Summary.id == summary_id, Summary.user_id == user.id))
    if not row:
        raise HTTPException(status_code=404, detail="Summary not found.")

    current = json.loads(row.feedback_json) if row.feedback_json else {}
    for key, value in body.answers.items():
        if key not in _VALID_ANSWERS:
            raise HTTPException(status_code=422, detail=f"Unknown question: {key}")
        if value is None or value == "":
            current.pop(key, None)  # tapping the selected answer again clears it
            continue
        if value not in _VALID_ANSWERS[key]:
            raise HTTPException(status_code=422, detail=f"Invalid answer for {key}: {value}")
        current[key] = value

    row.feedback_json = json.dumps(current) if current else None
    db.commit()
    return _row_to_history_item(row)


@app.get("/tags")
def list_tags(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Every tag across the user's library with a usage count, most-used
    first. Doubles as the reuse hint fed back into the summarize prompt."""
    rows = db.scalars(
        select(Summary).where(Summary.user_id == user.id, Summary.tags_json.is_not(None))
    ).all()
    counts: dict[str, int] = {}
    for r in rows:
        try:
            for tag in json.loads(r.tags_json) or []:
                counts[tag] = counts.get(tag, 0) + 1
        except (ValueError, TypeError):
            continue
    return [
        {"tag": tag, "count": count}
        for tag, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    ]


def _known_tags_for(db: Session, user: User | None, limit: int = 60) -> list[str]:
    """Most-used existing tags, passed to the prompt so new summaries reuse
    the user's established vocabulary instead of coining near-duplicates."""
    if user is None:
        return []
    rows = db.scalars(
        select(Summary).where(Summary.user_id == user.id, Summary.tags_json.is_not(None))
        .order_by(Summary.created_at.desc()).limit(200)
    ).all()
    counts: dict[str, int] = {}
    for r in rows:
        try:
            for tag in json.loads(r.tags_json) or []:
                counts[tag] = counts.get(tag, 0) + 1
        except (ValueError, TypeError):
            continue
    return [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:limit]


def _archive_glossary_terms(db: Session, user_id: int, summaries: list[Summary]) -> None:
    """
    GET /glossary computes video-sourced terms on the fly from Summary rows
    (see get_glossary above), so deleting those rows would otherwise erase
    a term the moment its source video is deleted — undermining the whole
    point of a "growing" glossary. Copy each term into CustomGlossaryTerm
    (skipping ones already saved there) before the caller deletes the rows.
    """
    existing = {
        t.lower()
        for t in db.scalars(
            select(CustomGlossaryTerm.term).where(CustomGlossaryTerm.user_id == user_id)
        ).all()
    }
    for r in summaries:
        if not r.glossary_json:
            continue
        for entry in json.loads(r.glossary_json):
            term = (entry.get("term") or "").strip()
            if not term or term.lower() in existing:
                continue
            existing.add(term.lower())
            db.add(CustomGlossaryTerm(
                user_id=user_id,
                term=term,
                definition=entry.get("definition") or "",
                example=entry.get("example") or "",
            ))


@app.delete("/history/{summary_id}")
def delete_one_history_item(
    summary_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    row = db.scalar(select(Summary).where(Summary.id == summary_id, Summary.user_id == user.id))
    if not row:
        raise HTTPException(status_code=404, detail="Summary not found.")
    _archive_glossary_terms(db, user.id, [row])
    db.delete(row)
    db.commit()
    return {"ok": True}


class GlossarySource(BaseModel):
    video_id: str
    title: str | None = None
    url: str


class GlossaryTerm(BaseModel):
    term: str
    definition: str
    example: str
    sources: list[GlossarySource]


@app.get("/glossary", response_model=list[GlossaryTerm])
def get_glossary(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """
    Aggregates every glossary term across the user's whole history into
    one growing list, deduped case-insensitively by term, with a
    back-reference to every video that used it. Computed on read rather
    than maintained as its own table — a user's history realistically
    tops out in the hundreds of rows, so scanning it here is simpler
    than keeping a many-to-many table in sync.
    """
    rows = db.scalars(
        select(Summary)
        .where(Summary.user_id == user.id, Summary.glossary_json.is_not(None))
        .order_by(Summary.created_at.asc())
    ).all()

    terms: dict[str, GlossaryTerm] = {}
    for r in rows:
        entries = json.loads(r.glossary_json) if r.glossary_json else []
        source = GlossarySource(video_id=r.video_id, title=r.title, url=r.url)
        for entry in entries:
            key = entry["term"].strip().lower()
            if not key:
                continue
            if key in terms:
                terms[key].sources.append(source)
            else:
                terms[key] = GlossaryTerm(
                    term=entry["term"].strip(),
                    definition=entry.get("definition") or "",
                    example=entry.get("example") or "",
                    sources=[source],
                )

    custom_rows = db.scalars(
        select(CustomGlossaryTerm)
        .where(CustomGlossaryTerm.user_id == user.id)
        .order_by(CustomGlossaryTerm.created_at.asc())
    ).all()
    for r in custom_rows:
        key = r.term.strip().lower()
        if key in terms:
            continue  # a video already defined this term — keep that version (has sources)
        terms[key] = GlossaryTerm(term=r.term, definition=r.definition, example=r.example, sources=[])

    return sorted(terms.values(), key=lambda t: t.term.lower())


class DefineTermsRequest(BaseModel):
    terms: list[str]


@app.post("/glossary/custom", response_model=list[GlossaryTerm])
def add_custom_glossary_terms(
    body: DefineTermsRequest, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    """
    Lets a user add their own glossary terms directly, instead of waiting
    for a video to happen to use them. Every term in the batch is defined
    in a single LLM call, no matter how many are submitted.
    """
    terms = [t.strip() for t in body.terms if t.strip()][:20]
    if not terms:
        raise HTTPException(status_code=422, detail="Provide at least one term.")

    try:
        defined = define_terms(terms, provider=user.ai_provider or "anthropic")
    except SummarizerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not defined:
        raise HTTPException(status_code=502, detail="Couldn't define those terms. Try again.")

    rows = []
    for d in defined:
        row = CustomGlossaryTerm(user_id=user.id, term=d["term"], definition=d["definition"], example=d["example"])
        db.add(row)
        rows.append(row)
    db.commit()

    return [GlossaryTerm(term=r.term, definition=r.definition, example=r.example, sources=[]) for r in rows]


@app.get("/history/categories", response_model=list[str])
def get_history_categories(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Summary.category).where(Summary.user_id == user.id).distinct()
    ).all()
    # Category values are comma-separated (a video can carry multiple
    # labels) — split them back out so the filter dropdown offers each
    # individual label rather than raw multi-label combinations.
    labels: set[str] = set()
    for row in rows:
        for label in (row or "").split(","):
            label = label.strip()
            if label:
                labels.add(label)
    return sorted(labels)


@app.get("/history/export")
def export_history(
    format: Literal["json", "csv", "markdown"] = "json",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(Summary).where(Summary.user_id == user.id).order_by(Summary.created_at.desc())
    ).all()

    if format == "json":
        content = json.dumps(
            [
                {
                    "video_id": r.video_id,
                    "url": r.url,
                    "title": r.title,
                    "channel": r.channel,
                    "channel_id": r.channel_id,
                    "published_at": r.published_at,
                    "subscriber_count": r.subscriber_count,
                    "category": r.category,
                    "language": r.language,
                    "view_count": r.view_count,
                    "comment_count": r.comment_count,
                    "like_count": r.like_count,
                    "duration_seconds": r.duration_seconds,
                    "sentiment_label": r.sentiment_label,
                    "sentiment_blurb": r.sentiment_blurb,
                    "highlight": r.highlight,
                    "counterpoint": r.counterpoint,
                    "title_answer": r.title_answer,
                    "chapters": json.loads(r.chapters_json) if r.chapters_json else [],
                    "key_points": json.loads(r.key_points_json) if r.key_points_json else [],
                    "status": r.status or "unread",
                    "summary": r.summary_text,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ],
            indent=2,
        )
        media_type = "application/json"
        filename = "skim-history.json"

    elif format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "created_at", "video_id", "title", "title_answer", "channel", "subscriber_count",
                "category", "language", "view_count", "like_count", "comment_count", "duration_seconds",
                "sentiment_label", "sentiment_blurb", "highlight", "counterpoint", "status", "url", "summary",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.created_at.isoformat(), r.video_id, r.title, r.title_answer, r.channel,
                    r.subscriber_count, r.category, r.language, r.view_count, r.like_count,
                    r.comment_count, r.duration_seconds, r.sentiment_label, r.sentiment_blurb,
                    r.highlight, r.counterpoint, r.status or "unread", r.url, r.summary_text,
                ]
            )
        content = buf.getvalue()
        media_type = "text/csv"
        filename = "skim-history.csv"

    else:
        lines = ["# TLDW history", ""]
        last_date = None
        for r in rows:
            day = r.created_at.date().isoformat()
            if day != last_date:
                lines.append(f"## {day}")
                lines.append("")
                last_date = day
            heading = r.title or r.video_id
            lines.append(f"**{heading}** · {r.channel or 'Unknown channel'} · {r.category} · [{r.url}]({r.url})")
            lines.append("")
            if r.title_answer:
                lines.append(f"> {r.title_answer}")
                lines.append("")
            lines.append(r.summary_text)
            if r.key_points_json:
                lines.append("")
                lines.append("**Key points:**")
                for kp in json.loads(r.key_points_json):
                    lines.append(f"- {kp['point']} — {kp['detail']}")
            if r.chapters_json:
                lines.append("")
                lines.append("**Chapters:**")
                for chapter in json.loads(r.chapters_json):
                    seconds = chapter["seconds"]
                    lines.append(f"- [{chapter['label']}]({r.url}&t={seconds}s)")
            if r.sentiment_label:
                lines.append("")
                lines.append(f"**Comment sentiment:** {r.sentiment_label} — {r.sentiment_blurb or ''}")
            if r.highlight:
                lines.append("")
                lines.append(f"**The upside:** {r.highlight}")
            if r.counterpoint:
                lines.append("")
                lines.append(f"**The other side:** {r.counterpoint}")
            lines.append("")
        content = "\n".join(lines)
        media_type = "text/markdown"
        filename = "skim-history.md"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/history")
def delete_all_history(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Summary).where(Summary.user_id == user.id)).all()
    _archive_glossary_terms(db, user.id, rows)
    result = db.execute(delete(Summary).where(Summary.user_id == user.id))
    db.commit()
    return {"deleted": result.rowcount}


_static_dir = Path(__file__).parent / "static"


def _serve_revalidating(filename: str, media_type: str) -> Response:
    """
    Serve an app-shell file that must always be revalidated.

    StaticFiles sends no Cache-Control header at all, which browsers —
    especially mobile Safari for a home-screen/standalone PWA, which this
    app is set up as — and Railway's edge treat as "cache this and don't
    bother checking again." That silently serves stale code after a fix
    has shipped, which has cost real debugging time more than once.

    This matters even more now that the CSS and JS are separate files: a
    cached app.js against a fresh index.html is a broken app, not just an
    outdated one.
    """
    return Response(
        content=(_static_dir / filename).read_text(encoding="utf-8"),
        media_type=media_type,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/", include_in_schema=False)
def serve_index():
    return _serve_revalidating("index.html", "text/html")


@app.get("/styles.css", include_in_schema=False)
def serve_styles():
    return _serve_revalidating("styles.css", "text/css")


@app.get("/app.js", include_in_schema=False)
def serve_app_js():
    return _serve_revalidating("app.js", "application/javascript")


# Mounted last and at "/" so it only catches requests that don't match
# an API route above (e.g. static assets other than index.html).
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
