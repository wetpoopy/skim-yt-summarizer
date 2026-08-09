"""
Transcript extraction for YouTube videos.

Uses youtube-transcript-api (no video download, no API key required —
it reads the caption tracks YouTube already serves).
"""

import os
import re
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


def get_transcript(url: str, languages: Optional[list[str]] = None) -> dict:
    """
    Fetch the transcript for a YouTube URL.

    Returns:
        {
            "video_id": str,
            "text": str,          # full transcript, whitespace-joined
            "duration_seconds": float,
            "language": str,
        }

    Raises:
        TranscriptError with a human-readable reason if no transcript
        is available (disabled, video unavailable, no captions in any
        requested language, etc).
    """
    video_id = extract_video_id(url)
    languages = languages or ["en", "en-US", "en-GB"]

    try:
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
            "duration_seconds": duration,
            "language": transcript.language_code,
        }

    except TranscriptsDisabled:
        raise TranscriptError("Captions are disabled for this video.")
    except VideoUnavailable:
        raise TranscriptError("This video is unavailable (private, deleted, or region-locked).")
    except NoTranscriptFound:
        raise TranscriptError("No transcript/captions found for this video in any language.")
    except (IpBlocked, RequestBlocked):
        raise TranscriptError(
            "YouTube is blocking requests from this server's IP. This is common on "
            "cloud hosting — set WEBSHARE_PROXY_USERNAME/PASSWORD to route through "
            "a residential proxy."
        )
    except TranscriptError:
        raise
    except Exception as e:
        raise TranscriptError(f"Couldn't retrieve a transcript for this video ({e.__class__.__name__}).")
