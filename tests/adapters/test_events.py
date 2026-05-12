"""Tests for EventPublisher."""

from unittest.mock import AsyncMock, patch

import pytest
from event_schemas.types import EventType

from event_booking.adapters.events import EventPublisher


@pytest.fixture
def publisher() -> EventPublisher:
    return EventPublisher(
        endpoint_url="http://test:8888/event/booking",
        api_key="test-key",
        source="booking",
        timeout_seconds=5.0,
    )


class TestSendEvent:
    @pytest.mark.asyncio
    async def test_skips_when_no_endpoint(self) -> None:
        pub = EventPublisher(endpoint_url=None, api_key=None, source="booking", timeout_seconds=5.0)
        await pub.send_event("uid-1", EventType.BOOKING_CREATED, {"key": "val"})

    @pytest.mark.asyncio
    async def test_sends_cloudevent(self, publisher: EventPublisher) -> None:
        with patch("event_booking.adapters.events.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock()

            await publisher.send_event("uid-1", EventType.BOOKING_CREATED, {"key": "val"})

            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args
            assert call_kwargs[0][0] == "http://test:8888/event/booking"
            assert "Authorization" in call_kwargs[1]["headers"]


class TestSendNotificationCommand:
    @pytest.mark.asyncio
    async def test_sends_notification_command(self, publisher: EventPublisher) -> None:
        with patch.object(publisher, "send_event", new_callable=AsyncMock) as mock_send:
            await publisher.send_notification_command(
                booking_uid="uid-1",
                trigger_event="BOOKING_CREATED",
                recipients=[{"email": "org@test.com", "role": "organizer"}],
                template_data={"start_time": "2026-06-15T10:00:00Z"},
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[1]["event"] == EventType.NOTIFICATION_SEND_REQUESTED
