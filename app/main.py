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

from app.auth import get_current_user, require_user, router as auth_router
from app.db import get_db, init_db
from app.models import Summary, User
from app.transcript import get_transcript, TranscriptError
from app.summarizer import summarize, SummarizerError
from app.ratelimit import check_and_record, FREE_TIER_DAILY_LIMIT
from app.youtube_metadata import get_video_metadata

app = FastAPI(title="YT Summarizer", version="0.1.0")


@app.on_event("startup")
def _on_startup():
    init_db()


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


class SummarizeResponse(BaseModel):
    video_id: str
    summary: str
    category: str
    language: str
    remaining_today: int | None = None
    saved: bool = False
    title: str | None = None
    channel: str | None = None
    view_count: int | None = None
    comment_count: int | None = None
    duration_seconds: int | None = None


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
    view_count: int | None = None
    comment_count: int | None = None
    duration_seconds: int | None = None


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

    # Bring-your-own-key callers skip the shared rate limit entirely.
    if x_anthropic_key:
        client = Anthropic(api_key=x_anthropic_key)
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

    try:
        result = summarize(transcript_data["text"], client=client)
    except SummarizerError as e:
        raise HTTPException(status_code=502, detail=str(e))

    metadata = get_video_metadata(transcript_data["video_id"]) or {}

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
            view_count=metadata.get("view_count"),
            comment_count=metadata.get("comment_count"),
            duration_seconds=metadata.get("duration_seconds"),
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
        view_count=metadata.get("view_count"),
        comment_count=metadata.get("comment_count"),
        duration_seconds=metadata.get("duration_seconds"),
    )


@app.get("/history", response_model=list[HistoryItem])
def get_history(
    date_filter: date | None = Query(default=None, alias="date"),
    category: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = select(Summary).where(Summary.user_id == user.id)
    if date_filter is not None:
        day_start = datetime.combine(date_filter, time.min, tzinfo=timezone.utc)
        day_end = datetime.combine(date_filter, time.max, tzinfo=timezone.utc)
        query = query.where(Summary.created_at >= day_start, Summary.created_at <= day_end)
    if category:
        query = query.where(Summary.category == category)
    query = query.order_by(Summary.created_at.desc())

    rows = db.scalars(query).all()
    return [
        HistoryItem(
            id=r.id,
            video_id=r.video_id,
            url=r.url,
            summary=r.summary_text,
            category=r.category,
            language=r.language,
            created_at=r.created_at,
            title=r.title,
            channel=r.channel,
            view_count=r.view_count,
            comment_count=r.comment_count,
            duration_seconds=r.duration_seconds,
        )
        for r in rows
    ]


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
                    "category": r.category,
                    "language": r.language,
                    "view_count": r.view_count,
                    "comment_count": r.comment_count,
                    "duration_seconds": r.duration_seconds,
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
                "created_at", "video_id", "title", "channel", "category", "language",
                "view_count", "comment_count", "duration_seconds", "url", "summary",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.created_at.isoformat(), r.video_id, r.title, r.channel, r.category, r.language,
                    r.view_count, r.comment_count, r.duration_seconds, r.url, r.summary_text,
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
            lines.append(r.summary_text)
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
