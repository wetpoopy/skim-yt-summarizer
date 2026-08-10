"""
Playlist expansion + per-provider cost estimate for batch mode.

Same best-effort pattern as youtube_metadata.py: plain requests, a
YOUTUBE_API_KEY-gated call, light retry on transient failures. Cost
estimates are informational only (published per-token pricing, not a
live balance check — no provider exposes that via a plain API key).
"""

import os
import re
import time

import requests

from app.youtube_metadata import _parse_iso8601_duration

YOUTUBE_PLAYLIST_ITEMS_API_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
YOUTUBE_PLAYLISTS_API_URL = "https://www.googleapis.com/youtube/v3/playlists"
YOUTUBE_VIDEOS_API_URL = "https://www.googleapis.com/youtube/v3/videos"

MAX_PLAYLIST_VIDEOS = 50

MAX_METADATA_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = [1, 2]

_PLAYLIST_ID_RE = re.compile(r"[?&]list=([A-Za-z0-9_-]+)")

# Approximate published per-1M-token pricing (input, output) in USD.
# Informational only — not fetched live, not billing-accurate.
PROVIDER_PRICING = {
    "anthropic": (3.00, 15.00),   # Claude Sonnet
    "openai": (2.50, 10.00),      # GPT-4o
    "gemini": (0.10, 0.40),       # Gemini 2.0 Flash
}
WORDS_PER_MINUTE = 150
TOKENS_PER_WORD = 1.3
ESTIMATED_OUTPUT_TOKENS = 600


def extract_playlist_id(url: str) -> str | None:
    match = _PLAYLIST_ID_RE.search(url)
    return match.group(1) if match else None


def _get_with_retry(url: str, params: dict) -> dict | None:
    for attempt in range(MAX_METADATA_ATTEMPTS):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception:
            if attempt < MAX_METADATA_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])
    return None


def get_playlist_preview(playlist_id: str) -> dict | None:
    """
    Returns {"playlist_id", "playlist_title", "videos": [{"video_id",
    "title", "thumbnail_url", "duration_seconds"}, ...]} for up to the
    first MAX_PLAYLIST_VIDEOS videos, or None on missing key/failure.
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return None

    items_data = _get_with_retry(
        YOUTUBE_PLAYLIST_ITEMS_API_URL,
        {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": MAX_PLAYLIST_VIDEOS,
            "key": api_key,
        },
    )
    if items_data is None:
        return None

    items = items_data.get("items", [])
    if not items:
        return None

    videos = []
    for item in items:
        snippet = item.get("snippet", {})
        video_id = snippet.get("resourceId", {}).get("videoId")
        if not video_id:
            continue
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (thumbnails.get("medium") or thumbnails.get("default") or {}).get("url")
        videos.append({
            "video_id": video_id,
            "title": snippet.get("title"),
            "thumbnail_url": thumbnail,
            "duration_seconds": None,
        })

    if not videos:
        return None

    playlist_title = None
    playlists_data = _get_with_retry(
        YOUTUBE_PLAYLISTS_API_URL, {"part": "snippet", "id": playlist_id, "key": api_key}
    )
    if playlists_data:
        playlist_items = playlists_data.get("items", [])
        if playlist_items:
            playlist_title = playlist_items[0].get("snippet", {}).get("title")

    video_ids = ",".join(v["video_id"] for v in videos)
    videos_data = _get_with_retry(
        YOUTUBE_VIDEOS_API_URL, {"part": "contentDetails", "id": video_ids, "key": api_key}
    )
    if videos_data:
        durations = {
            v["id"]: _parse_iso8601_duration(v.get("contentDetails", {}).get("duration", ""))
            for v in videos_data.get("items", [])
        }
        for video in videos:
            video["duration_seconds"] = durations.get(video["video_id"])

    return {
        "playlist_id": playlist_id,
        "playlist_title": playlist_title or "Playlist",
        "videos": videos,
    }


def estimate_batch_cost(videos: list[dict]) -> dict[str, float]:
    """
    Rough estimated $ cost per provider for summarizing all given videos,
    based on published per-token pricing and duration-derived transcript
    length. Not a live balance check — informational only.
    """
    totals = {provider: 0.0 for provider in PROVIDER_PRICING}
    for video in videos:
        duration = video.get("duration_seconds") or 0
        words = (duration / 60) * WORDS_PER_MINUTE
        input_tokens = words * TOKENS_PER_WORD
        for provider, (input_price, output_price) in PROVIDER_PRICING.items():
            cost = (input_tokens / 1_000_000) * input_price
            cost += (ESTIMATED_OUTPUT_TOKENS / 1_000_000) * output_price
            totals[provider] += cost

    return {provider: round(total, 2) for provider, total in totals.items()}
