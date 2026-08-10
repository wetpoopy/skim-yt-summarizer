"""
Transcript extraction for YouTube videos.

Uses youtube-transcript-api (no video download, no API key required —
it reads the caption tracks YouTube already serves).
"""

import os
import re
import time
from typing import Optional

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    IpBlocked,
    RequestBlocked,
)

# The residential proxy rotates IPs per request, so a failed attempt is
# often just a bad-luck IP — retrying gives it fresh ones. Only transient
# failures (blocked/network) are retried; deterministic ones (disabled,
# unavailable, no captions) fail fast since retrying won't change them.
MAX_TRANSCRIPT_ATTEMPTS = 6
RETRY_BACKOFF_SECONDS = [1, 2, 4, 8, 16]


class TranscriptError(Exception):
    """Raised when a transcript can't be retrieved, with a user-facing reason."""
    pass


def _build_api() -> YouTubeTranscriptApi:
    """
    Build the API client, routing through a residential proxy if configured.

    IMPORTANT for deployment: YouTube blocks requests from most cloud/
    datacenter IP ranges (Vercel, Railway, Fly, AWS, GCP, etc). This works
    fine locally/on a home connection, but will very likely fail with
    IpBlocked once deployed unless you configure a residential proxy.
    Webshare has a supported integration (a few $/mo residential proxy
    plan is enough for this volume) — set WEBSHARE_PROXY_USERNAME and
    WEBSHARE_PROXY_PASSWORD env vars to enable it automatically.
    """
    proxy_user = os.environ.get("WEBSHARE_PROXY_USERNAME")
    proxy_pass = os.environ.get("WEBSHARE_PROXY_PASSWORD")
    if proxy_user and proxy_pass:
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=proxy_user,
                proxy_password=proxy_pass,
            )
        )
    return YouTubeTranscriptApi()


def extract_video_id(url: str) -> str:
    """
    Pull the 11-character video ID out of any common YouTube URL shape:
    - https://www.youtube.com/watch?v=VIDEOID
    - https://youtu.be/VIDEOID
    - https://www.youtube.com/shorts/VIDEOID
    - https://m.youtube.com/watch?v=VIDEOID
    """
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"shorts\/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise TranscriptError("Couldn't find a valid YouTube video ID in that URL.")


def _fetch_transcript_once(video_id: str, languages: list[str]) -> dict:
    """One attempt at fetching the transcript. Raises the underlying
    youtube_transcript_api exceptions (or TranscriptError) untranslated —
    get_transcript() decides what's worth retrying."""
    api = _build_api()
    transcript_list = api.list(video_id)

    # Prefer a manually created transcript in the requested languages,
    # fall back to auto-generated, fall back to translating whatever exists.
    try:
        transcript = transcript_list.find_transcript(languages)
    except NoTranscriptFound:
        try:
            transcript = transcript_list.find_generated_transcript(languages)
        except NoTranscriptFound:
            # last resort: grab the first available transcript and translate it
            available = next(iter(transcript_list), None)
            if available is None:
                raise
            transcript = available.translate("en") if available.is_translatable else available

    fetched = transcript.fetch()  # FetchedTranscript: iterable of snippets w/ .text/.start/.duration
    snippets = [s for s in fetched if s.text.strip()]
    full_text = " ".join(s.text.strip() for s in snippets)
    duration = (snippets[-1].start + snippets[-1].duration) if snippets else 0

    if not full_text:
        raise TranscriptError("This video's transcript came back empty.")

    return {
        "video_id": video_id,
        "text": full_text,
        "segments": [{"text": s.text.strip(), "start": s.start} for s in snippets],
        "duration_seconds": duration,
        "language": transcript.language_code,
    }


def get_transcript(url: str, languages: Optional[list[str]] = None) -> dict:
    """
    Fetch the transcript for a YouTube URL, retrying transient failures
    (proxy/IP blocks, network hiccups) since the rotating proxy often
    just needs a fresh IP. Deterministic failures (captions disabled,
    video unavailable, no transcript in any language) fail immediately —
    retrying them can't change the outcome.

    Returns:
        {
            "video_id": str,
            "text": str,          # full transcript, whitespace-joined
            "segments": list[dict],  # [{"text": str, "start": float}, ...]
            "duration_seconds": float,
            "language": str,
        }

    Raises:
        TranscriptError with a human-readable reason if no transcript
        could be retrieved after all attempts.
    """
    video_id = extract_video_id(url)
    languages = languages or ["en", "en-US", "en-GB"]

    last_error: TranscriptError | None = None
    for attempt in range(MAX_TRANSCRIPT_ATTEMPTS):
        try:
            return _fetch_transcript_once(video_id, languages)
        except TranscriptsDisabled:
            raise TranscriptError("Captions are disabled for this video.")
        except VideoUnavailable:
            raise TranscriptError("This video is unavailable (private, deleted, or region-locked).")
        except NoTranscriptFound:
            raise TranscriptError("No transcript/captions found for this video in any language.")
        except (IpBlocked, RequestBlocked):
            last_error = TranscriptError(
                "YouTube is blocking requests from this server's IP. This is common on "
                "cloud hosting — set WEBSHARE_PROXY_USERNAME/PASSWORD to route through "
                "a residential proxy."
            )
        except TranscriptError as e:
            last_error = e
        except Exception as e:
            last_error = TranscriptError(f"Couldn't retrieve a transcript for this video ({e.__class__.__name__}).")

        if attempt < MAX_TRANSCRIPT_ATTEMPTS - 1:
            time.sleep(RETRY_BACKOFF_SECONDS[attempt])

    raise last_error
