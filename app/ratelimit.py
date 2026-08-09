"""
Minimal in-memory rate limiter, keyed by client IP.

Good enough for a single-process MVP deploy. Once you run more than
one instance (or want limits to survive restarts), swap the dict for
Redis (INCR + EXPIRE) — the interface below won't need to change.
"""

import time
from collections import defaultdict
from threading import Lock

FREE_TIER_DAILY_LIMIT = 8
_WINDOW_SECONDS = 24 * 60 * 60

_lock = Lock()
_hits: dict[str, list[float]] = defaultdict(list)


def check_and_record(client_id: str, limit: int = FREE_TIER_DAILY_LIMIT) -> tuple[bool, int]:
    """
    Returns (allowed: bool, remaining: int).
    Records the hit if allowed.
    """
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
