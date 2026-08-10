"""
Top comments (text + like count) for sentiment scoring, via the
official YouTube Data API v3.

Best-effort: comments disabled, missing key, quota, network issues,
or no comments at all all just return an empty list rather than
raising — sentiment silently becomes unavailable, never blocks the
core summarize flow.
"""

import os
import time

import requests

YOUTUBE_COMMENTS_API_URL = "https://www.googleapis.com/youtube/v3/commentThreads"

MAX_COMMENT_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = [1, 2]
DEFAULT_MAX_RESULTS = 25


def get_top_comments(video_id: str, max_results: int = DEFAULT_MAX_RESULTS) -> list[dict]:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return []

    for attempt in range(MAX_COMMENT_ATTEMPTS):
        try:
            response = requests.get(
                YOUTUBE_COMMENTS_API_URL,
                params={
                    "part": "snippet",
                    "videoId": video_id,
                    "order": "relevance",
                    "maxResults": max_results,
                    "textFormat": "plainText",
                    "key": api_key,
                },
                timeout=10,
            )
            response.raise_for_status()
            items = response.json().get("items", [])

            comments = []
            for item in items:
                top_level = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                text = top_level.get("textDisplay")
                if not text:
                    continue
                comments.append({
                    "text": text,
                    "like_count": top_level.get("likeCount", 0),
                })
            return comments
        except Exception:
            # Comments disabled surfaces as a 403 from the API — not worth
            # retrying, but harmless to just let the loop exhaust quickly.
            if attempt < MAX_COMMENT_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])

    return []
