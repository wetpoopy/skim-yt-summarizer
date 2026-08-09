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

from pathlib import Path

from anthropic import Anthropic
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from app.transcript import get_transcript, TranscriptError
from app.summarizer import summarize, SummarizerError
from app.ratelimit import check_and_record, FREE_TIER_DAILY_LIMIT

app = FastAPI(title="YT Summarizer", version="0.1.0")

# Wide open for MVP; tighten to your actual frontend domain before you
# publicize this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


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
    language: str
    remaining_today: int | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/summarize", response_model=SummarizeResponse)
def summarize_video(
    body: SummarizeRequest,
    request: Request,
    x_anthropic_key: str | None = Header(default=None),
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
        summary_text = summarize(transcript_data["text"], client=client)
    except SummarizerError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return SummarizeResponse(
        video_id=transcript_data["video_id"],
        summary=summary_text,
        language=transcript_data["language"],
        remaining_today=remaining,
    )


# Mounted last and at "/" so it only catches requests that don't match
# an API route above (StaticFiles serves index.html for "/" by default).
_static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
