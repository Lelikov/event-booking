"""Tests for the meeting controller."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from event_schemas.types import EventType

from event_booking.controllers.meeting import MeetingController
from event_booking.dtos import BookingDTO, UserDTO

JITSI_SECRET = "test-secret"
JITSI_AUD = "test-aud"
JITSI_ISS = "test-iss"
MEETING_HOST_URL = "https://meet.test"


@pytest.fixture
def booking():
    now = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    return BookingDTO(
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        end_time=now + timedelta(hours=1),
        id=1,
        start_time=now,
        status="accepted",
        title="Test Meeting",
        uid="booking-uid-123",
        user=UserDTO(
            id=1,
            name="Test User",
            email="user@test.com",
            locked=False,
            time_zone="UTC",
        ),
    )


@pytest.fixture
def mock_shortener():
    shortener = AsyncMock()
    shortener.create_url.return_value = "https://short.test/abc"
    shortener.delete_url.return_value = "booking-uid-123"
    shortener.update_url_data.return_value = "https://short.test/abc"
    return shortener


@pytest.fixture
def mock_chat_client():
    client = MagicMock()
    client.create_token.return_value = "chat-jwt-token"
    return client


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_events():
    return AsyncMock()


@pytest.fixture
def controller(mock_shortener, mock_chat_client, mock_db, mock_events):
    return MeetingController(
        shortener=mock_shortener,
        chat_client=mock_chat_client,
        db=mock_db,
        events=mock_events,
        jitsi_jwt_secret=JITSI_SECRET,
        jitsi_jwt_aud=JITSI_AUD,
        jitsi_jwt_iss=JITSI_ISS,
        meeting_host_url=MEETING_HOST_URL,
    )


async def test_returns_shortened_url(controller, booking, mock_shortener):
    url = await controller.create_meeting_url(
        booking=booking,
        participant_name="Test User",
        participant_email="user@test.com",
    )

    assert url == "https://short.test/abc"
    mock_shortener.create_url.assert_called_once()


async def test_falls_back_to_long_url(controller, booking, mock_shortener):
    mock_shortener.create_url.return_value = None

    url = await controller.create_meeting_url(
        booking=booking,
        participant_name="Test User",
        participant_email="user@test.com",
    )

    assert MEETING_HOST_URL in url
    assert booking.uid in url


async def test_sends_meeting_url_created_event(controller, booking, mock_events):
    await controller.create_meeting_url(
        booking=booking,
        participant_name="Test User",
        participant_email="user@test.com",
    )

    mock_events.send_event.assert_called_once()
    call_kwargs = mock_events.send_event.call_args.kwargs
    assert call_kwargs["event"] == EventType.MEETING_URL_CREATED
    assert call_kwargs["booking_uid"] == booking.uid
    assert call_kwargs["data"] == {
        "email": "user@test.com",
        "recipient_role": "organizer",
        "meeting_url": "https://short.test/abc",
    }


async def test_deletes_and_sends_event(controller, booking, mock_shortener, mock_events):
    await controller.delete_meeting_url(booking=booking)

    mock_shortener.delete_url.assert_called_once_with(external_id=booking.uid)
    mock_events.send_event.assert_called_once()
    call_kwargs = mock_events.send_event.call_args.kwargs
    assert call_kwargs["event"] == EventType.MEETING_URL_DELETED
    assert call_kwargs["booking_uid"] == booking.uid
    assert call_kwargs["data"] == {"email": "user@test.com", "recipient_role": "organizer"}
