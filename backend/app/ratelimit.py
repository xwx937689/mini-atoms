"""Minimal in-memory IP rate limiting. No external deps, single-process only."""
import time
from collections import defaultdict, deque

# endpoint-bucket -> {ip: deque[timestamps]}
_buckets: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))


def check(bucket: str, ip: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds). Slides a time window per IP per bucket."""
    now = time.time()
    q = _buckets[bucket][ip]
    while q and now - q[0] > window_seconds:
        q.popleft()
    if len(q) >= limit:
        retry = int(window_seconds - (now - q[0])) + 1
        return False, max(retry, 1)
    q.append(now)
    return True, 0


def client_ip(request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
