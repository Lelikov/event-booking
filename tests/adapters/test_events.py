"""Tests for EventPublisher."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from event_schemas.types import EventType

from event_booking.adapters.events import EventPublisher, EventPublishError


@pytest.fixture
def publisher() -> EventPublisher:
    return EventPublisher(
        endpoint_url="http://test:8888/event/booking",
        api_key="test-key",
        source="booking",
        timeout_seconds=5.0,
    )


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    return response


def _client_patch(mock_client: AsyncMock):
    patcher = patch("event_booking.adapters.events.httpx.AsyncClient")
    mock_client_cls = patcher.start()
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return patcher


class TestSendEvent:
    def test_requires_endpoint_url(self) -> None:
        with pytest.raises(ValueError, match="EVENTS_ENDPOINT_URL"):
            EventPublisher(endpoint_url="", api_key=None, source="booking", timeout_seconds=5.0)

    async def test_sends_cloudevent(self, publisher: EventPublisher) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_ok_response())
        patcher = _client_patch(mock_client)
        try:
            await publisher.send_event("uid-1", EventType.BOOKING_CREATED, {"key": "val"})
        finally:
            patcher.stop()

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[0][0] == "http://test:8888/event/booking"
        assert "Authorization" in call_kwargs[1]["headers"]

    async def test_raises_on_http_error_status(self, publisher: EventPublisher) -> None:
        response = MagicMock()
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("503", request=MagicMock(), response=MagicMock())
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)
        patcher = _client_patch(mock_client)
        try:
            with pytest.raises(EventPublishError):
                await publisher.send_event("uid-1", EventType.BOOKING_CREATED, {"key": "val"})
        finally:
            patcher.stop()

    async def test_raises_on_transport_error(self, publisher: EventPublisher) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        patcher = _client_patch(mock_client)
        try:
            with pytest.raises(EventPublishError):
                await publisher.send_event("uid-1", EventType.BOOKING_CREATED, {"key": "val"})
        finally:
            patcher.stop()

    async def test_dedupe_key_makes_event_id_deterministic(self, publisher: EventPublisher) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_ok_response())
        patcher = _client_patch(mock_client)
        try:
            await publisher.send_event("uid-1", EventType.CHAT_CREATED, {}, dedupe_key="ce-1:chat.created")
            await publisher.send_event("uid-1", EventType.CHAT_CREATED, {}, dedupe_key="ce-1:chat.created")
            await publisher.send_event("uid-1", EventType.CHAT_CREATED, {}, dedupe_key="ce-2:chat.created")
        finally:
            patcher.stop()

        ids = [call.kwargs["headers"]["ce-id"] for call in mock_client.post.call_args_list]
        assert ids[0] == ids[1]
        assert ids[2] != ids[0]


class TestSendNotificationCommand:
    async def test_sends_notification_command(self, publisher: EventPublisher) -> None:
        with patch.object(publisher, "send_event", new_callable=AsyncMock) as mock_send:
            await publisher.send_notification_command(
                booking_uid="uid-1",
                trigger_event="BOOKING_CREATED",
                recipients=[{"email": "org@test.com", "role": "organizer"}],
                template_data={"start_time": "2026-06-15T10:00:00Z"},
                dedupe_key="ce-1:notification:BOOKING_CREATED:organizer",
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[1]["event"] == EventType.NOTIFICATION_SEND_REQUESTED
            assert call_args[1]["dedupe_key"] == "ce-1:notification:BOOKING_CREATED:organizer"
