"""Request middleware: rate limiting and safe error handling."""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window limiter.

    This protects the *provider quotas* as much as the server: one scripted
    client hammering /recommendations could exhaust a day of a free geocoding
    tier. Health and docs are exempt so probes are never throttled.

    In-process, so it limits per worker. Behind several workers or replicas,
    put a shared limiter (nginx, an API gateway, or Redis) in front.
    """

    EXEMPT_PATHS = frozenset({"/api/health", "/docs", "/redoc", "/openapi.json", "/"})

    def __init__(self, app, *, max_requests: int, window_seconds: int) -> None:
        super().__init__(app)
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _client_key(self, request: Request) -> str:
        # Trust the first X-Forwarded-For hop only; behind a proxy that is the
        # real client, and direct connections fall back to the socket peer.
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        key = self._client_key(request)
        now = time.monotonic()
        bucket = self._hits[key]
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self._max:
            retry_after = max(1, int(self._window - (now - bucket[0])))
            logger.warning("rate limit hit for %s on %s", key, request.url.path)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": (
                        f"Too many requests. You may make {self._max} requests per "
                        f"{self._window} seconds."
                    )
                },
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)
        # Keep the map from growing without bound on a long-lived process.
        if len(self._hits) > 10_000:
            for stale_key in [k for k, v in self._hits.items() if not v]:
                self._hits.pop(stale_key, None)
        return await call_next(request)


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Convert unhandled exceptions into a safe response.

    The traceback goes to the log with a correlation id; the client gets the
    id and nothing else. Stack traces and provider URLs (which can carry
    credentials) must never reach a browser.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except Exception:
            incident = uuid.uuid4().hex[:12]
            logger.exception(
                "unhandled error incident=%s path=%s", incident, request.url.path
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "Something went wrong on our side.",
                    "incident": incident,
                },
            )
