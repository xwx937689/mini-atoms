"""Minimal in-memory IP rate limiting. No external deps, single-process only.

IMPORTANT: counters live in process memory. With multiple workers/instances this
is per-process, not global. For a distributed deployment use Redis or an edge
WAF instead. Documented as a known limitation, not hidden.
"""
import os
import time
from collections import defaultdict, deque

# endpoint-bucket -> {ip: deque[timestamps]}
_buckets: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))

# Only trust X-Forwarded-For when running behind a proxy we control.
# Render terminates TLS at its edge and sets XFF, so enable it there via env.
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "true").lower() == "true"


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
    """Resolve client IP. XFF is only honoured when TRUST_PROXY_HEADERS is on.

    Takes the LAST XFF entry (the one appended by our own trusted proxy) rather
    than the first, because the first is client-controlled and trivially spoofed.
    """
    if TRUST_PROXY_HEADERS:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            parts = [p.strip() for p in fwd.split(",") if p.strip()]
            if parts:
                return parts[-1]
        real = request.headers.get("x-real-ip", "").strip()
        if real:
            return real
    return request.client.host if request.client else "unknown"
