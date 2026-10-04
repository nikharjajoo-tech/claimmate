"""HTTP hardening: security headers, request size caps, and a per-client rate limiter."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

# The UI loads only its own scripts and styles; media comes from our API or in-page blobs;
# the live call is a WebSocket to the same host. Nothing may frame the app (clickjacking).
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "connect-src 'self' ws: wss:",
    "worker-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
])
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(self), microphone=(self), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

MAX_EVIDENCE_BODY = 2_200_000  # base64 JPEG under 1.5 MB, plus JSON overhead
MAX_EVIDENCE_BATCH_BODY = 12_000_000  # several browser-resized photos; the client splits larger sets
MAX_API_BODY = 64_000


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH"}:
            path = request.url.path
            if path.endswith("/evidence/batch"):
                limit = MAX_EVIDENCE_BATCH_BODY
            elif path.endswith("/evidence"):
                limit = MAX_EVIDENCE_BODY
            else:
                limit = MAX_API_BODY
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return PlainTextResponse("A Content-Length header is required.", status_code=411)
            if int(length) > limit:
                return PlainTextResponse("Request too large.", status_code=413)
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class RateLimiter:
    """Sliding-window limit per key (usually the client IP)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True
