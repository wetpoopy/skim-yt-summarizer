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
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Literal

from anthropic import Anthropic
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
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
from app.youtube_metadata import get_channel_subscriber_count, get_video_metadata
from app.youtube_comments import get_top_comments
from app.playlist import estimate_batch_cost, extract_playlist_id, get_playlist_preview

app = FastAPI(title="YT Summarizer", version="0.1.0")

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


# Wide open for MVP; tighten to your actual frontend domain before you
# publicize this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
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
    sentiment_label: str | None = None
    sentiment_blurb: str | None = None
    highlight: str | None = None
    counterpoint: str | None = None
    title_answer: str | None = None
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
    sentiment_label: str | None = None
    sentiment_blurb: str | None = None
    highlight: str | None = None
    counterpoint: str | None = None
    title_answer: str | None = None
    chapters: list[Chapter] = []
    key_points: list[KeyPoint] = []
    glossary: list[GlossaryEntry] = []
    status: str = "unread"
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
        sentiment_label=r.sentiment_label,
        sentiment_blurb=r.sentiment_blurb,
        highlight=r.highlight,
        counterpoint=r.counterpoint,
        title_answer=r.title_answer,
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
    subscriber_count = (
        get_channel_subscriber_count(metadata["channel_id"]) if metadata.get("channel_id") else None
    )

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
            sentiment_label=result.get("sentiment_label"),
            sentiment_blurb=result.get("sentiment_blurb"),
            highlight=result.get("highlight"),
            counterpoint=result.get("counterpoint"),
            title_answer=result.get("answer"),
            chapters_json=json.dumps(chapters) if chapters else None,
            key_points_json=json.dumps(key_points) if key_points else None,
            glossary_json=json.dumps(glossary) if glossary else None,
            status="unread",
            playlist_id=body.playlist_id,
            playlist_title=body.playlist_title,
        )
        db.add(row)
        db.commit()
        saved = True

    return SummarizeResponse(
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
        sentiment_label=result.get("sentiment_label"),
        sentiment_blurb=result.get("sentiment_blurb"),
        highlight=result.get("highlight"),
        counterpoint=result.get("counterpoint"),
        title_answer=result.get("answer"),
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
        sentiment_label=r.sentiment_label,
        sentiment_blurb=r.sentiment_blurb,
        highlight=r.highlight,
        counterpoint=r.counterpoint,
        title_answer=r.title_answer,
        chapters=chapters,
        key_points=key_points,
        glossary=glossary,
        status=r.status or "unread",
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


@app.get("/", include_in_schema=False)
def serve_index():
    """
    Explicit route (checked before the StaticFiles mount below) so the
    app shell always revalidates instead of being cached indefinitely.
    StaticFiles sends no Cache-Control header at all, which browsers —
    especially mobile Safari for a home-screen/standalone PWA, which
    this app is set up as — can and do treat as "cache this and don't
    bother checking again," silently serving an old index.html (old
    CSS/JS, since everything is inline in this one file) after a fix
    has already shipped.
    """
    return FileResponse(_static_dir / "index.html", headers={"Cache-Control": "no-cache, must-revalidate"})


# Mounted last and at "/" so it only catches requests that don't match
# an API route above (e.g. static assets other than index.html).
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
