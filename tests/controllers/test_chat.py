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
    return client


@pytest.fixture
def mock_events():
    return AsyncMock()


@pytest.fixture
def controller(mock_chat_client, mock_events):
    return ChatController(chat_client=mock_chat_client, events=mock_events)


async def test_creates_channel_and_sends_event(controller, mock_chat_client, mock_events):
    await controller.create_chat("channel-1", "organizer-1", "client-1")

    mock_chat_client.create_chat.assert_called_once_with(
        channel_id="channel-1",
        organizer_id="organizer-1",
        client_id="client-1",
    )
    mock_events.send_event.assert_called_once()
    call_kwargs = mock_events.send_event.call_args.kwargs
    assert call_kwargs["event"] == EventType.CHAT_CREATED


async def test_deletes_channel_and_sends_event(controller, mock_chat_client, mock_events):
    await controller.delete_chat("channel-1", "booking-uid-1")

    mock_chat_client.delete_chat.assert_called_once_with(channel_id="channel-1")
    mock_events.send_event.assert_called_once()
    call_kwargs = mock_events.send_event.call_args.kwargs
    assert call_kwargs["event"] == EventType.CHAT_DELETED
