"""Blacklist client: reads the currently-effective blacklist from event-admin.

Availability model (per the 2026-06-12 design):
- The full active set for ``client_email`` is cached in memory with a TTL
  (``BLACKLIST_CACHE_TTL``, default 300 s); refreshed lazily on expiry.
- On refresh failure the stale cache keeps serving (logged as a warning).
- With no cache and the API down the check FAILS OPEN: the email is treated
  as not blacklisted and an error is logged. Blocking all bookings because
  event-admin is down would be worse than letting a blacklisted one through.
- When disabled (``BLACKLIST_ENABLED=false`` or no ``EVENT_ADMIN_API_URL``),
  every check returns False without any network activity.
"""

import time
from collections.abc import Callable

import httpx
import structlog

from event_booking import metrics

logger = structlog.get_logger(__name__)

_ACTIVE_PATH = "/api/blacklist/active"
_FIELD = "client_email"


class BlacklistClient:
    def __init__(  # noqa: PLR0913
        self,
        *,
        api_url: str | None,
        service_token: str | None,
        cache_ttl_seconds: float,
        timeout_seconds: float,
        enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_url = (api_url or "").rstrip("/")
        self._service_token = service_token or ""
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._transport = transport
        self._enabled = enabled and bool(self._api_url) and bool(self._service_token)
        self._cached_values: frozenset[str] | None = None
        self._fetched_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def is_blacklisted(self, email: str) -> bool:
        """Exact case-insensitive match against the cached active set; fail-open on outage."""
        if not self._enabled:
            return False
        values = await self._get_values()
        if values is None:
            metrics.BLACKLIST_CHECKS_TOTAL.labels(result="fail_open").inc()
            return False
        if email.strip().lower() in values:
            metrics.BLACKLIST_CHECKS_TOTAL.labels(result="hit").inc()
            return True
        metrics.BLACKLIST_CHECKS_TOTAL.labels(result="miss").inc()
        return False

    async def _get_values(self) -> frozenset[str] | None:
        now = self._clock()
        if self._cached_values is not None and now - self._fetched_at < self._cache_ttl_seconds:
            return self._cached_values
        try:
            fetched = await self._fetch_active_values()
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            if self._cached_values is not None:
                logger.warning(
                    "Blacklist refresh failed; serving stale cache",
                    api_url=self._api_url,
                    cached_count=len(self._cached_values),
                    cache_age_seconds=round(now - self._fetched_at, 1),
                )
                return self._cached_values
            logger.exception(
                "Blacklist fetch failed with no cache available; failing open (treating emails as not blacklisted)",
                api_url=self._api_url,
            )
            return None
        self._cached_values = fetched
        self._fetched_at = now
        return self._cached_values

    async def _fetch_active_values(self) -> frozenset[str]:
        kwargs: dict = {"timeout": self._timeout_seconds}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.get(
                f"{self._api_url}{_ACTIVE_PATH}",
                params={"field": _FIELD},
                headers={"Authorization": f"Bearer {self._service_token}"},
            )
            response.raise_for_status()
            payload = response.json()
        values = frozenset(str(value).lower() for value in payload["values"])
        logger.debug("Blacklist refreshed", count=len(values))
        return values
