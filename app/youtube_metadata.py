"""
Video metadata (title, channel, view/comment counts, length) via the
official YouTube Data API v3 — youtube-transcript-api only gets captions.

Best-effort: any failure (missing/invalid key, quota, network, video
not found) returns None rather than raising. Metadata is a nice-to-have
and must never break the core summarize flow.
"""

import os
import re
import time

import requests

YOUTUBE_VIDEOS_API_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_CHANNELS_API_URL = "https://www.googleapis.com/youtube/v3/channels"

# This hits Google's API directly (no rotating proxy involved), so it's
# far more reliable than the transcript fetch — a light retry is enough
# to smooth over occasional network blips or transient 5xx responses.
MAX_METADATA_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = [1, 2]


def _parse_iso8601_duration(duration: str) -> int | None:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return None
    hours, minutes, seconds = (int(x) if x else 0 for x in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def get_video_metadata(video_id: str) -> dict | None:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return None

    for attempt in range(MAX_METADATA_ATTEMPTS):
        try:
            response = requests.get(
                YOUTUBE_VIDEOS_API_URL,
                params={
                    "part": "snippet,contentDetails,statistics",
                    "id": video_id,
                    "key": api_key,
                },
                timeout=10,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            if not items:
                return None  # video genuinely has no metadata to return — not worth retrying

            item = items[0]
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            content_details = item.get("contentDetails", {})

            view_count = statistics.get("viewCount")
            comment_count = statistics.get("commentCount")
            like_count = statistics.get("likeCount")
            duration = content_details.get("duration")

            return {
                "title": snippet.get("title"),
                "channel": snippet.get("channelTitle"),
                "channel_id": snippet.get("channelId"),
                "view_count": int(view_count) if view_count is not None else None,
                "comment_count": int(comment_count) if comment_count is not None else None,
                "like_count": int(like_count) if like_count is not None else None,
                "duration_seconds": _parse_iso8601_duration(duration) if duration else None,
            }
        except Exception:
            if attempt < MAX_METADATA_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])

    return None


def get_channel_subscriber_count(channel_id: str) -> int | None:
    """
    Best-effort subscriber count. Returns None if missing key, any
    failure, or the channel has subscriber count hidden (a channel
    setting YouTube honors — not an error, just not public data).
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key or not channel_id:
        return None

    for attempt in range(MAX_METADATA_ATTEMPTS):
        try:
            response = requests.get(
                YOUTUBE_CHANNELS_API_URL,
                params={"part": "statistics", "id": channel_id, "key": api_key},
                timeout=10,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            if not items:
                return None

            subscriber_count = items[0].get("statistics", {}).get("subscriberCount")
            return int(subscriber_count) if subscriber_count is not None else None
        except Exception:
            if attempt < MAX_METADATA_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])

    return None
