"""
Rate limiter, keyed by client IP (or any string key — also reused for
per-email password-reset throttling).

Backed by Redis (sorted-set sliding window) when REDIS_URL is set, so
counts survive restarts and work correctly if this ever runs as more
than one instance. Falls back to the original in-memory dict when
REDIS_URL is absent (local dev needs no Redis install) — same public
interface either way, callers don't need to know which backend is live.
"""

import os
import secrets
import time
from collections import defaultdict
from threading import Lock

FREE_TIER_DAILY_LIMIT = 8
_WINDOW_SECONDS = 24 * 60 * 60

# ---------- in-memory fallback ----------
_lock = Lock()
_hits: dict[str, list[float]] = defaultdict(list)


def _check_and_record_memory(client_id: str, limit: int) -> tuple[bool, int]:
    now = time.time()
    with _lock:
        window_start = now - _WINDOW_SECONDS
        recent = [t for t in _hits[client_id] if t > window_start]
        _hits[client_id] = recent

        if len(recent) >= limit:
            return False, 0

        recent.append(now)
        _hits[client_id] = recent
        return True, limit - len(recent)


# ---------- Redis backend ----------
def _build_redis_client():
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return None
    try:
        import redis
        client = redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
        client.ping()
        return client
    except Exception:
        return None


_redis_client = _build_redis_client()


def _check_and_record_redis(client_id: str, limit: int) -> tuple[bool, int]:
    key = f"ratelimit:{client_id}"
    now = time.time()
    window_start = now - _WINDOW_SECONDS

    _redis_client.zremrangebyscore(key, 0, window_start)
    count = _redis_client.zcard(key)

    if count >= limit:
        return False, 0

    # Unique member per hit (score alone isn't guaranteed unique under
    # concurrent requests at the same timestamp).
    member = f"{now}-{secrets.token_hex(4)}"
    pipe = _redis_client.pipeline()
    pipe.zadd(key, {member: now})
    pipe.expire(key, _WINDOW_SECONDS)
    pipe.execute()
    return True, limit - count - 1


def check_and_record(client_id: str, limit: int = FREE_TIER_DAILY_LIMIT) -> tuple[bool, int]:
    """
    Returns (allowed: bool, remaining: int).
    Records the hit if allowed.
    """
    if _redis_client is not None:
        try:
            return _check_and_record_redis(client_id, limit)
        except Exception:
            pass  # Redis hiccup — fail open to the in-memory limiter rather than 500ing
    return _check_and_record_memory(client_id, limit)
