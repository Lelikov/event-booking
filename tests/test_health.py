"""Tests for the /health (liveness) and /ready (readiness) endpoints."""

from typing import Self

from faststream.rabbit import RabbitBroker
from sqlalchemy.ext.asyncio import AsyncEngine

from event_booking import main

HTTP_OK = 200
HTTP_SERVICE_UNAVAILABLE = 503


class _FakeConnection:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def __aenter__(self) -> Self:
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, _statement: object) -> None:
        return None


class _FakeEngine:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self._error)


class _FakeBroker:
    def __init__(self, *, ping_result: bool = True, ping_error: Exception | None = None) -> None:
        self._ping_result = ping_result
        self._ping_error = ping_error

    async def ping(self, timeout: float | None) -> bool:  # noqa: ARG002 — mirrors RabbitBroker.ping
        if self._ping_error is not None:
            raise self._ping_error
        return self._ping_result


class _FakeContainer:
    """Maps DI types onto fakes the readiness checks resolve."""

    def __init__(self, engine: _FakeEngine, broker: _FakeBroker) -> None:
        self._instances = {AsyncEngine: engine, RabbitBroker: broker}

    async def get(self, dependency_type: type) -> object:
        return self._instances[dependency_type]


class _FakeRequest:
    def __init__(self, container: _FakeContainer) -> None:
        self.app = main.app
        self.app.state.container = container


class TestHealth:
    async def test_health_returns_ok(self) -> None:
        assert await main.health() == {"status": "ok"}

    def test_routes_registered(self) -> None:
        paths = {route.path for route in main.app.routes}

        assert "/health" in paths
        assert "/ready" in paths


class TestReady:
    async def test_ready_when_all_dependencies_reachable(self) -> None:
        request = _FakeRequest(_FakeContainer(_FakeEngine(), _FakeBroker()))

        response = await main.ready(request)

        assert response.status_code == HTTP_OK
        assert b'"status":"ready"' in response.body
        assert b'"database":true' in response.body
        assert b'"rabbitmq":true' in response.body

    async def test_not_ready_when_database_down(self) -> None:
        request = _FakeRequest(_FakeContainer(_FakeEngine(error=ConnectionError("db down")), _FakeBroker()))

        response = await main.ready(request)

        assert response.status_code == HTTP_SERVICE_UNAVAILABLE
        assert b'"status":"not_ready"' in response.body
        assert b'"database":false' in response.body

    async def test_not_ready_when_rabbit_down(self) -> None:
        request = _FakeRequest(_FakeContainer(_FakeEngine(), _FakeBroker(ping_result=False)))

        response = await main.ready(request)

        assert response.status_code == HTTP_SERVICE_UNAVAILABLE
        assert b'"rabbitmq":false' in response.body

    async def test_not_ready_when_rabbit_ping_raises(self) -> None:
        request = _FakeRequest(_FakeContainer(_FakeEngine(), _FakeBroker(ping_error=ConnectionError("amqp down"))))

        response = await main.ready(request)

        assert response.status_code == HTTP_SERVICE_UNAVAILABLE
        assert b'"rabbitmq":false' in response.body
