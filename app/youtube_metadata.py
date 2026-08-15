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
                "description": snippet.get("description"),
                "channel": snippet.get("channelTitle"),
                "channel_id": snippet.get("channelId"),
                "published_at": snippet.get("publishedAt"),
                "view_count": int(view_count) if view_count is not None else None,
                "comment_count": int(comment_count) if comment_count is not None else None,
                "like_count": int(like_count) if like_count is not None else None,
                "duration_seconds": _parse_iso8601_duration(duration) if duration else None,
            }
        except Exception:
            if attempt < MAX_METADATA_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])

    return None


YOUTUBE_PLAYLIST_ITEMS_API_URL = "https://www.googleapis.com/youtube/v3/playlistItems"

# How many recent uploads to average likes over. Lifetime average likes
# isn't available from the API at all, and a recent window is the more
# useful number anyway — it reflects the channel as it is now.
RECENT_UPLOADS_SAMPLE = 20


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _get_recent_avg_likes(uploads_playlist_id: str, api_key: str) -> tuple[int | None, int | None]:
    """
    Average likes and views across the channel's most recent uploads.
    Returns (avg_likes, sample_size). Two cheap calls (1 quota unit each):
    the uploads playlist for recent video ids, then a single batched
    statistics lookup for those ids.
    """
    try:
        listing = requests.get(
            YOUTUBE_PLAYLIST_ITEMS_API_URL,
            params={
                "part": "contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": RECENT_UPLOADS_SAMPLE,
                "key": api_key,
            },
            timeout=10,
        )
        listing.raise_for_status()
        video_ids = [
            it.get("contentDetails", {}).get("videoId")
            for it in listing.json().get("items", [])
        ]
        video_ids = [v for v in video_ids if v]
        if not video_ids:
            return None, None

        stats = requests.get(
            YOUTUBE_VIDEOS_API_URL,
            params={"part": "statistics", "id": ",".join(video_ids), "key": api_key},
            timeout=10,
        )
        stats.raise_for_status()
        likes = [
            _int_or_none(it.get("statistics", {}).get("likeCount"))
            for it in stats.json().get("items", [])
        ]
        likes = [n for n in likes if n is not None]
        if not likes:
            return None, None
        return round(sum(likes) / len(likes)), len(likes)
    except Exception:
        # Likes can be hidden per-video, playlists can be empty — none of
        # that should cost us the rest of the channel stats.
        return None, None


def get_channel_stats(channel_id: str) -> dict | None:
    """
    Best-effort channel-level stats: subscribers, lifetime views, upload
    count, and averages. Returns None on missing key or total failure;
    individual fields come back None when YouTube withholds them (e.g. a
    channel hiding its subscriber count, which is a setting rather than
    an error).
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key or not channel_id:
        return None

    for attempt in range(MAX_METADATA_ATTEMPTS):
        try:
            response = requests.get(
                YOUTUBE_CHANNELS_API_URL,
                params={"part": "statistics,contentDetails", "id": channel_id, "key": api_key},
                timeout=10,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            if not items:
                return None

            stats = items[0].get("statistics", {})
            subscriber_count = _int_or_none(stats.get("subscriberCount"))
            total_views = _int_or_none(stats.get("viewCount"))
            video_count = _int_or_none(stats.get("videoCount"))

            avg_views = None
            if total_views is not None and video_count:
                avg_views = round(total_views / video_count)

            uploads = (
                items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            )
            avg_likes, sample = _get_recent_avg_likes(uploads, api_key) if uploads else (None, None)

            return {
                "subscriber_count": subscriber_count,
                "total_views": total_views,
                "video_count": video_count,
                "avg_views_per_video": avg_views,
                "avg_likes_per_video": avg_likes,
                "avg_likes_sample": sample,
            }
        except Exception:
            if attempt < MAX_METADATA_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])

    return None


def get_channel_subscriber_count(channel_id: str) -> int | None:
    """Kept for callers that only need the one number (e.g. the mobile app's
    older endpoint shape)."""
    stats = get_channel_stats(channel_id)
    return stats.get("subscriber_count") if stats else None
