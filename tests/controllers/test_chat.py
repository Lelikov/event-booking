"""Tests for the chat controller."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from event_schemas.types import EventType

from event_booking.controllers.chat import ChatController


@pytest.fixture
def mock_chat_client():
    client = MagicMock()
    client.create_chat = AsyncMock()
    client.delete_chat = AsyncMock()
    client.send_message = AsyncMock()
    client.has_messages = AsyncMock(return_value=False)
    return client


@pytest.fixture
def mock_events():
    return AsyncMock()


@pytest.fixture
def controller(mock_chat_client, mock_events):
    return ChatController(chat_client=mock_chat_client, events=mock_events)


async def test_creates_channel_and_sends_event(controller, mock_chat_client, mock_events):
    await controller.create_chat("channel-1", "organizer-1", "client-1", dedupe_key="ce-1:chat.created")

    mock_chat_client.create_chat.assert_called_once_with(
        channel_id="channel-1",
        organizer_id="organizer-1",
        client_id="client-1",
    )
    mock_events.send_event.assert_called_once()
    call_kwargs = mock_events.send_event.call_args.kwargs
    assert call_kwargs["event"] == EventType.CHAT_CREATED
    assert call_kwargs["dedupe_key"] == "ce-1:chat.created"


async def test_deletes_channel_and_sends_event(controller, mock_chat_client, mock_events):
    await controller.delete_chat("channel-1", "booking-uid-1", hard=True)

    mock_chat_client.delete_chat.assert_called_once_with(channel_id="channel-1", hard=True)
    mock_events.send_event.assert_called_once()
    call_kwargs = mock_events.send_event.call_args.kwargs
    assert call_kwargs["event"] == EventType.CHAT_DELETED


async def test_create_chat_failure_propagates(controller, mock_chat_client, mock_events):
    """No silent swallow: failures must bubble up so the broker redelivers the message."""
    mock_chat_client.create_chat.side_effect = RuntimeError("getstream down")

    with pytest.raises(RuntimeError, match="getstream down"):
        await controller.create_chat("channel-1", "organizer-1", "client-1")

    mock_events.send_event.assert_not_called()


async def test_delete_chat_failure_propagates(controller, mock_chat_client):
    mock_chat_client.delete_chat.side_effect = RuntimeError("getstream down")

    with pytest.raises(RuntimeError, match="getstream down"):
        await controller.delete_chat("channel-1", "booking-uid-1")


async def test_has_messages_passthrough(controller, mock_chat_client):
    mock_chat_client.has_messages = AsyncMock(return_value=True)
    assert await controller.has_messages("channel-1") is True
    mock_chat_client.has_messages.assert_called_once_with(channel_id="channel-1")
