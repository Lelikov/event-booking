"""Tests for BlacklistClient: cache TTL, stale-on-error, fail-open, disabled mode."""

import json

import httpx

from event_booking.adapters.blacklist import BlacklistClient


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeAdminApi:
    """httpx.MockTransport handler with switchable failure mode and request counting."""

    def __init__(self, values: list[str] | None = None) -> None:
        self.values = values if values is not None else ["spam@example.com"]
        self.requests: list[httpx.Request] = []
        self.fail = False
        self.payload_override: dict | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(503, content=b"unavailable")
        payload = self.payload_override
        if payload is None:
            payload = {"field": "client_email", "values": self.values}
        return httpx.Response(200, content=json.dumps(payload).encode())


def make_client(  # noqa: PLR0913
    api: FakeAdminApi,
    clock: FakeClock,
    *,
    ttl: float = 300.0,
    enabled: bool = True,
    api_url: str | None = "http://admin.test",
    token: str | None = "service-token",  # noqa: S107
) -> BlacklistClient:
    return BlacklistClient(
        api_url=api_url,
        service_token=token,
        cache_ttl_seconds=ttl,
        timeout_seconds=1.0,
        enabled=enabled,
        clock=clock,
        transport=httpx.MockTransport(api),
    )


class TestMatching:
    async def test_listed_email_matches_case_insensitively(self) -> None:
        client = make_client(FakeAdminApi(["spam@example.com"]), FakeClock())

        assert await client.is_blacklisted("  Spam@Example.COM ") is True

    async def test_unlisted_email_does_not_match(self) -> None:
        client = make_client(FakeAdminApi(["spam@example.com"]), FakeClock())

        assert await client.is_blacklisted("ok@example.com") is False

    async def test_request_carries_service_token_and_field(self) -> None:
        api = FakeAdminApi()
        client = make_client(api, FakeClock())

        await client.is_blacklisted("any@example.com")

        request = api.requests[0]
        assert request.headers["Authorization"] == "Bearer service-token"
        assert request.url.path == "/api/blacklist/active"
        assert request.url.params["field"] == "client_email"

    async def test_server_values_are_lowercased_defensively(self) -> None:
        client = make_client(FakeAdminApi(["MiXeD@Example.com"]), FakeClock())

        assert await client.is_blacklisted("mixed@example.com") is True


class TestCacheTtl:
    async def test_checks_within_ttl_reuse_the_cache(self) -> None:
        api = FakeAdminApi()
        clock = FakeClock()
        client = make_client(api, clock, ttl=300.0)

        await client.is_blacklisted("a@example.com")
        clock.advance(299.0)
        await client.is_blacklisted("b@example.com")

        assert len(api.requests) == 1

    async def test_cache_refreshes_after_ttl_expiry(self) -> None:
        api = FakeAdminApi()
        clock = FakeClock()
        client = make_client(api, clock, ttl=300.0)

        await client.is_blacklisted("a@example.com")
        clock.advance(301.0)
        await client.is_blacklisted("a@example.com")

        assert len(api.requests) == 2  # noqa: PLR2004

    async def test_refresh_picks_up_new_values(self) -> None:
        api = FakeAdminApi(["old@example.com"])
        clock = FakeClock()
        client = make_client(api, clock, ttl=300.0)

        assert await client.is_blacklisted("new@example.com") is False
        api.values = ["new@example.com"]
        clock.advance(301.0)
        assert await client.is_blacklisted("new@example.com") is True


class TestFailureModes:
    async def test_stale_cache_served_when_refresh_fails(self) -> None:
        api = FakeAdminApi(["spam@example.com"])
        clock = FakeClock()
        client = make_client(api, clock, ttl=300.0)

        assert await client.is_blacklisted("spam@example.com") is True
        api.fail = True
        clock.advance(301.0)

        assert await client.is_blacklisted("spam@example.com") is True
        assert await client.is_blacklisted("ok@example.com") is False

    async def test_fails_open_when_api_down_and_no_cache(self) -> None:
        api = FakeAdminApi(["spam@example.com"])
        api.fail = True
        client = make_client(api, FakeClock())

        assert await client.is_blacklisted("spam@example.com") is False

    async def test_fails_open_on_malformed_payload(self) -> None:
        api = FakeAdminApi()
        api.payload_override = {"unexpected": "shape"}
        client = make_client(api, FakeClock())

        assert await client.is_blacklisted("spam@example.com") is False

    async def test_recovers_after_outage(self) -> None:
        api = FakeAdminApi(["spam@example.com"])
        api.fail = True
        clock = FakeClock()
        client = make_client(api, clock)

        assert await client.is_blacklisted("spam@example.com") is False
        api.fail = False

        assert await client.is_blacklisted("spam@example.com") is True


class TestDisabledMode:
    async def test_disabled_flag_short_circuits_without_network(self) -> None:
        api = FakeAdminApi()
        client = make_client(api, FakeClock(), enabled=False)

        assert await client.is_blacklisted("spam@example.com") is False
        assert api.requests == []
        assert client.enabled is False

    async def test_missing_api_url_disables_the_client(self) -> None:
        api = FakeAdminApi()
        client = make_client(api, FakeClock(), api_url=None)

        assert await client.is_blacklisted("spam@example.com") is False
        assert api.requests == []

    async def test_missing_token_disables_the_client(self) -> None:
        api = FakeAdminApi()
        client = make_client(api, FakeClock(), token=None)

        assert await client.is_blacklisted("spam@example.com") is False
        assert api.requests == []
