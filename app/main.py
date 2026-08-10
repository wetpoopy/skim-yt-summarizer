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
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
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
from app.models import Summary, User
from app.transcript import get_transcript, TranscriptError
from app.summarizer import summarize, SummarizerError
from app.ratelimit import check_and_record, FREE_TIER_DAILY_LIMIT
from app.youtube_metadata import get_channel_subscriber_count, get_video_metadata
from app.youtube_comments import get_top_comments

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

    @field_validator("url")
    @classmethod
    def looks_like_youtube(cls, v: str) -> str:
        if "youtube.com" not in v and "youtu.be" not in v:
            raise ValueError("That doesn't look like a YouTube URL.")
        return v


class Chapter(BaseModel):
    label: str
    seconds: int


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
    view_count: int | None = None
    comment_count: int | None = None
    like_count: int | None = None
    duration_seconds: int | None = None
    subscriber_count: int | None = None
    sentiment_label: str | None = None
    sentiment_blurb: str | None = None
    title_answer: str | None = None
    chapters: list[Chapter] = []


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
    view_count: int | None = None
    comment_count: int | None = None
    like_count: int | None = None
    duration_seconds: int | None = None
    subscriber_count: int | None = None
    sentiment_label: str | None = None
    sentiment_blurb: str | None = None
    title_answer: str | None = None
    chapters: list[Chapter] = []
    status: str = "unread"


class StatusUpdate(BaseModel):
    status: Literal["unread", "read", "archived"]


@app.get("/health")
def health():
    return {"status": "ok"}


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
        transcript_data = get_transcript(body.url)
    except TranscriptError as e:
        raise HTTPException(status_code=422, detail=str(e))

    length = (user.summary_length if user else None) or "standard"
    fmt = (user.summary_format if user else None) or "mixed"
    provider = (user.ai_provider if user else None) or "anthropic"

    metadata = get_video_metadata(transcript_data["video_id"]) or {}
    comments = get_top_comments(transcript_data["video_id"])
    subscriber_count = (
        get_channel_subscriber_count(metadata["channel_id"]) if metadata.get("channel_id") else None
    )

    try:
        result = summarize(
            transcript_data["text"],
            client=client,
            length=length,
            format=fmt,
            comments=comments,
            title=metadata.get("title"),
            description=metadata.get("description"),
            provider=provider,
        )
    except SummarizerError as e:
        raise HTTPException(status_code=502, detail=str(e))

    chapters = result.get("chapters") or []

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
            view_count=metadata.get("view_count"),
            comment_count=metadata.get("comment_count"),
            like_count=metadata.get("like_count"),
            duration_seconds=metadata.get("duration_seconds"),
            subscriber_count=subscriber_count,
            sentiment_label=result.get("sentiment_label"),
            sentiment_blurb=result.get("sentiment_blurb"),
            title_answer=result.get("answer"),
            chapters_json=json.dumps(chapters) if chapters else None,
            status="unread",
        )
        db.add(row)
        db.commit()
        saved = True

    return SummarizeResponse(
        video_id=transcript_data["video_id"],
        summary=result["summary"],
        category=result["category"],
        language=transcript_data["language"],
        remaining_today=remaining,
        saved=saved,
        title=metadata.get("title"),
        channel=metadata.get("channel"),
        channel_id=metadata.get("channel_id"),
        view_count=metadata.get("view_count"),
        comment_count=metadata.get("comment_count"),
        like_count=metadata.get("like_count"),
        duration_seconds=metadata.get("duration_seconds"),
        subscriber_count=subscriber_count,
        sentiment_label=result.get("sentiment_label"),
        sentiment_blurb=result.get("sentiment_blurb"),
        title_answer=result.get("answer"),
        chapters=chapters,
    )


def _row_to_history_item(r: Summary) -> HistoryItem:
    chapters = json.loads(r.chapters_json) if r.chapters_json else []
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
        view_count=r.view_count,
        comment_count=r.comment_count,
        like_count=r.like_count,
        duration_seconds=r.duration_seconds,
        subscriber_count=r.subscriber_count,
        sentiment_label=r.sentiment_label,
        sentiment_blurb=r.sentiment_blurb,
        title_answer=r.title_answer,
        chapters=chapters,
        status=r.status or "unread",
    )


@app.get("/history", response_model=list[HistoryItem])
def get_history(
    date_filter: date | None = Query(default=None, alias="date"),
    category: str | None = None,
    status: Literal["unread", "read", "archived"] = "unread",
    q: str | None = None,
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
        query = query.where(Summary.category == category)
    if q:
        like = f"%{q}%"
        query = query.where(
            Summary.title.ilike(like) | Summary.summary_text.ilike(like) | Summary.channel.ilike(like)
        )
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


@app.delete("/history/{summary_id}")
def delete_one_history_item(
    summary_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    row = db.scalar(select(Summary).where(Summary.id == summary_id, Summary.user_id == user.id))
    if not row:
        raise HTTPException(status_code=404, detail="Summary not found.")
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.get("/history/categories", response_model=list[str])
def get_history_categories(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Summary.category).where(Summary.user_id == user.id).distinct()
    ).all()
    return sorted(rows)


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
                    "subscriber_count": r.subscriber_count,
                    "category": r.category,
                    "language": r.language,
                    "view_count": r.view_count,
                    "comment_count": r.comment_count,
                    "like_count": r.like_count,
                    "duration_seconds": r.duration_seconds,
                    "sentiment_label": r.sentiment_label,
                    "sentiment_blurb": r.sentiment_blurb,
                    "title_answer": r.title_answer,
                    "chapters": json.loads(r.chapters_json) if r.chapters_json else [],
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
                "sentiment_label", "sentiment_blurb", "status", "url", "summary",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.created_at.isoformat(), r.video_id, r.title, r.title_answer, r.channel,
                    r.subscriber_count, r.category, r.language, r.view_count, r.like_count,
                    r.comment_count, r.duration_seconds, r.sentiment_label, r.sentiment_blurb,
                    r.status or "unread", r.url, r.summary_text,
                ]
            )
        content = buf.getvalue()
        media_type = "text/csv"
        filename = "skim-history.csv"

    else:
        lines = ["# Skim history", ""]
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
            if r.chapters_json:
                lines.append("")
                lines.append("**Chapters:**")
                for chapter in json.loads(r.chapters_json):
                    seconds = chapter["seconds"]
                    lines.append(f"- [{chapter['label']}]({r.url}&t={seconds}s)")
            if r.sentiment_label:
                lines.append("")
                lines.append(f"**Comment sentiment:** {r.sentiment_label} — {r.sentiment_blurb or ''}")
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
    result = db.execute(delete(Summary).where(Summary.user_id == user.id))
    db.commit()
    return {"deleted": result.rowcount}


# Mounted last and at "/" so it only catches requests that don't match
# an API route above (StaticFiles serves index.html for "/" by default).
_static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
