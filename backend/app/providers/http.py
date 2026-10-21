"""Shared outbound HTTP: timeouts, bounded retries, backoff and a fair-use
rate limiter.

Every provider call in TripWise goes through `ProviderClient.get_json`. That
gives one place to guarantee the things that keep a third-party integration
from becoming a liability:

* a hard timeout, so a slow provider cannot hold a request open;
* retries only on transient failures, with exponential backoff and jitter;
* no retry on 4xx, because resending a bad request just burns quota;
* a client-side rate limiter for keyless fair-use endpoints;
* logging that never includes credentials.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

# Query parameters whose values must never reach the logs.
_SECRET_PARAMS = {
    "key",
    "apikey",
    "api_key",
    "access_token",
    "token",
    "appid",
    "subscription-key",
}

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def redact_url(url: str) -> str:
    """Strip credential-bearing query parameters from a URL for logging."""
    parts = urlsplit(str(url))
    if not parts.query:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    kept: list[str] = []
    for chunk in parts.query.split("&"):
        name, _, _ = chunk.partition("=")
        kept.append(f"{name}=REDACTED" if name.lower() in _SECRET_PARAMS else chunk)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), ""))


class ProviderError(RuntimeError):
    """A provider call failed after exhausting retries.

    Carries a user-safe message: callers surface this in `provider_failures`
    so the UI can say which provider degraded without leaking internals.
    """

    def __init__(self, provider: str, detail: str, status_code: int | None = None) -> None:
        super().__init__(f"{provider}: {detail}")
        self.provider = provider
        self.detail = detail
        self.status_code = status_code


@dataclass
class RateLimiter:
    """Simple async token-bucket, one per host.

    Keyless OSM services ask for roughly one request per second. Honouring
    that client-side is the difference between a working integration and a
    blocked IP.
    """

    min_interval_seconds: float
    _last_call: float = field(default=0.0, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def acquire(self) -> None:
        async with self._lock:
            wait = self.min_interval_seconds - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


@dataclass
class CircuitBreaker:
    """Stops calling a provider that is clearly down.

    Without this, a provider that is unreachable costs its full timeout and
    retry budget on *every* candidate destination in a request -- turning one
    dead dependency into a 20-second response. After a few consecutive
    failures the circuit opens and further calls fail instantly until the
    cooldown expires, so the request degrades to estimates in milliseconds.
    """

    failure_threshold: int = 2
    cooldown_seconds: float = 60.0
    consecutive_failures: int = 0
    opened_at: float = 0.0

    @property
    def is_open(self) -> bool:
        if self.consecutive_failures < self.failure_threshold:
            return False
        if time.monotonic() - self.opened_at >= self.cooldown_seconds:
            # Cooldown elapsed: allow one probe through.
            self.consecutive_failures = 0
            return False
        return True

    def record_failure(self, *, decisive: bool = False) -> None:
        """Count a failure. `decisive` opens the circuit immediately.

        A transport-level failure (DNS, connect, proxy, TLS) means the host
        could not be reached at all, which retrying inside the same request
        will not fix. Treating it as decisive cuts the cost of a dead
        dependency from several seconds per candidate to one failed call.
        """
        if decisive:
            self.consecutive_failures = max(self.consecutive_failures + 1, self.failure_threshold)
            self.opened_at = time.monotonic()
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_at = time.monotonic()

    def record_success(self) -> None:
        self.consecutive_failures = 0


class ProviderClient:
    """An httpx wrapper with retry, backoff, rate limiting and circuit breaking."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.4,
        user_agent: str = "TripWise/1.0 (+https://github.com/tripwise)",
    ) -> None:
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff = backoff_base_seconds
        self._user_agent = user_agent
        self._client: httpx.AsyncClient | None = None
        self._limiters: dict[str, RateLimiter] = {}
        self._breakers: dict[str, CircuitBreaker] = {}

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={"User-Agent": self._user_agent, "Accept": "application/json"},
                follow_redirects=True,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def register_rate_limit(self, host: str, min_interval_seconds: float) -> None:
        """Throttle a host to at most one request per `min_interval_seconds`."""
        self._limiters[host] = RateLimiter(min_interval_seconds=min_interval_seconds)

    def breaker_for(self, host: str) -> CircuitBreaker:
        if host not in self._breakers:
            self._breakers[host] = CircuitBreaker()
        return self._breakers[host]

    def open_circuits(self) -> list[str]:
        """Hosts currently being skipped. Useful for diagnostics."""
        return [host for host, breaker in self._breakers.items() if breaker.is_open]

    def reset_circuits(self) -> None:
        """Close every circuit. Used by tests."""
        self._breakers.clear()

    # ---- requests --------------------------------------------------------
    async def get_json(
        self,
        provider: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request_json(provider, "GET", url, params=params, headers=headers)

    async def post_json(
        self,
        provider: str,
        url: str,
        *,
        json_body: Any = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self._request_json(
            provider, "POST", url, json_body=json_body, data=data, headers=headers, params=params
        )

    async def _request_json(
        self,
        provider: str,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        await self.start()
        assert self._client is not None

        host = httpx.URL(url).host or ""
        limiter = self._limiters.get(host)
        breaker = self.breaker_for(host)
        last_error: str = "no attempt made"
        last_status: int | None = None

        if breaker.is_open:
            # Fail fast: this host has just failed repeatedly.
            raise ProviderError(
                provider,
                f"skipped -- {host} failed {breaker.consecutive_failures} times in a row; "
                f"retrying in up to {breaker.cooldown_seconds:.0f}s",
            )

        for attempt in range(self._max_retries + 1):
            if limiter is not None:
                await limiter.acquire()
            try:
                response = await self._client.request(
                    method, url, params=params, json=json_body, data=data, headers=headers
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}"
                logger.warning(
                    "provider=%s attempt=%d/%d transport error on %s: %s",
                    provider, attempt + 1, self._max_retries + 1, redact_url(url), last_error,
                )
            else:
                if response.is_success:
                    breaker.record_success()
                    try:
                        return response.json()
                    except ValueError:
                        raise ProviderError(
                            provider, "response was not valid JSON", response.status_code
                        ) from None

                last_status = response.status_code
                last_error = f"HTTP {response.status_code}"
                # 4xx other than throttling will not change on a retry.
                if response.status_code not in RETRYABLE_STATUS:
                    logger.warning(
                        "provider=%s non-retryable %s on %s",
                        provider, last_error, redact_url(url),
                    )
                    # A 4xx is our fault, not the provider being down, so it
                    # must not trip the breaker for everyone else.
                    raise ProviderError(provider, last_error, response.status_code)
                logger.warning(
                    "provider=%s attempt=%d/%d %s on %s",
                    provider, attempt + 1, self._max_retries + 1, last_error, redact_url(url),
                )

            if attempt < self._max_retries:
                delay = self._backoff * (2**attempt) + random.uniform(0, self._backoff)
                await asyncio.sleep(delay)

        # `last_status is None` means we never got an HTTP response at all.
        breaker.record_failure(decisive=last_status is None)
        raise ProviderError(provider, f"failed after retries ({last_error})", last_status)


_client: ProviderClient | None = None


def get_client(
    *,
    timeout_seconds: float = 8.0,
    max_retries: int = 2,
    backoff_base_seconds: float = 0.4,
    contact_email: str | None = None,
) -> ProviderClient:
    """Process-wide HTTP client singleton.

    Nominatim's usage policy requires an identifiable User-Agent with a way to
    reach the operator, so the contact email is folded in when configured.
    """
    global _client
    if _client is None:
        agent = "TripWise/1.0 (budget trip planner)"
        if contact_email:
            agent = f"TripWise/1.0 (budget trip planner; {contact_email})"
        _client = ProviderClient(
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            user_agent=agent,
        )
        # Fair-use throttles for the keyless OSM-based services.
        _client.register_rate_limit("nominatim.openstreetmap.org", 1.1)
        _client.register_rate_limit("router.project-osrm.org", 1.1)
        _client.register_rate_limit("overpass-api.de", 2.0)
    return _client


async def shutdown_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
