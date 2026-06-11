"""Tests for the Dishka container wiring (scopes, per-request sessions)."""

import pytest
from dishka import make_async_container
from sqlalchemy.ext.asyncio import AsyncSession

from event_booking.controllers.booking import BookingController
from event_booking.ioc import AppProvider

REQUIRED_ENV = {
    "CALCOM_POSTGRES_DSN": "postgresql+asyncpg://cal:cal@localhost:5432/calcom",
    "RABBIT_URL": "amqp://events:events@localhost:5672/",
    "EVENTS_ENDPOINT_URL": "http://localhost:8000/event/booking",
    "JITSI_JWT_SECRET": "test-secret-that-is-long-enough-0123456789",
    "JITSI_JWT_AUD": "jitsi",
    "JITSI_JWT_ISS": "booking",
    "JITSI_JWT_SUB": "meet.example.org",
    "CHAT_API_KEY": "key",
    "CHAT_API_SECRET": "secret",
    "CHAT_USER_ID_ENCRYPTION_KEY": "encryption-key",
    "SHORTENER_URL": "http://localhost:9000",
}


@pytest.fixture
def _app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.mark.usefixtures("_app_env")
class TestContainerScopes:
    async def test_each_request_scope_gets_its_own_session(self) -> None:
        container = make_async_container(AppProvider())
        try:
            async with container() as request_one:
                session_one = await request_one.get(AsyncSession)
            async with container() as request_two:
                session_two = await request_two.get(AsyncSession)
            assert session_one is not session_two
        finally:
            await container.close()

    async def test_booking_controller_resolvable_in_request_scope(self) -> None:
        container = make_async_container(AppProvider())
        try:
            async with container() as request_scope:
                controller = await request_scope.get(BookingController)
            assert isinstance(controller, BookingController)
        finally:
            await container.close()

    async def test_session_not_resolvable_at_app_scope(self) -> None:
        container = make_async_container(AppProvider())
        try:
            with pytest.raises(Exception, match="(?i)scope"):
                await container.get(AsyncSession)
        finally:
            await container.close()
