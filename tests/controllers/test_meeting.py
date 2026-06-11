"""Tests for the meeting controller."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from event_schemas.types import EventType

from event_booking.controllers.meeting import MeetingController
from event_booking.dtos import BookingDTO, UserDTO

JITSI_SECRET = "test-secret-which-is-long-enough-for-hs256"
JITSI_AUD = "test-aud"
JITSI_ISS = "test-iss"
JITSI_SUB = "meet.example.org"
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
    client.create_token = AsyncMock(return_value="chat-jwt-token")
    return client


@pytest.fixture
def mock_events():
    return AsyncMock()


@pytest.fixture
def controller(mock_shortener, mock_chat_client, mock_events):
    return MeetingController(
        shortener=mock_shortener,
        chat_client=mock_chat_client,
        events=mock_events,
        jitsi_jwt_secret=JITSI_SECRET,
        jitsi_jwt_aud=JITSI_AUD,
        jitsi_jwt_iss=JITSI_ISS,
        jitsi_jwt_sub=JITSI_SUB,
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


async def test_jitsi_token_has_fixed_sub_and_moderator_for_organizer(controller, booking, mock_shortener):
    mock_shortener.create_url.return_value = None

    url = await controller.create_meeting_url(
        booking=booking,
        participant_name="Test User",
        participant_email="user@test.com",
    )

    jwt_video = url.split("jwt_video=")[1].split("&")[0]
    claims = jwt.decode(
        jwt_video, JITSI_SECRET, algorithms=["HS256"], audience=JITSI_AUD, options={"verify_nbf": False}
    )
    assert claims["sub"] == JITSI_SUB
    assert claims["sub"] != "*"
    assert claims["context"]["user"]["moderator"] is True


async def test_jitsi_token_client_is_not_moderator(controller, booking, mock_shortener):
    mock_shortener.create_url.return_value = None

    url = await controller.create_meeting_url(
        booking=booking,
        participant_name="Client",
        participant_email="client@test.com",
        external_id_prefix="client_",
    )

    jwt_video = url.split("jwt_video=")[1].split("&")[0]
    claims = jwt.decode(
        jwt_video, JITSI_SECRET, algorithms=["HS256"], audience=JITSI_AUD, options={"verify_nbf": False}
    )
    assert claims["context"]["user"]["moderator"] is False


async def test_reschedule_moves_short_url_from_previous_uid(controller, booking, mock_shortener):
    await controller.create_meeting_url(
        booking=booking,
        participant_name="Test User",
        participant_email="user@test.com",
        previous_booking_uid="old-uid",
    )

    mock_shortener.update_url_data.assert_called_once()
    kwargs = mock_shortener.update_url_data.call_args.kwargs
    assert kwargs["old_external_id"] == "old-uid"
    assert kwargs["new_external_id"] == booking.uid
    mock_shortener.create_url.assert_not_called()


async def test_reschedule_falls_back_to_create_when_old_url_missing(controller, booking, mock_shortener):
    mock_shortener.update_url_data.return_value = None

    url = await controller.create_meeting_url(
        booking=booking,
        participant_name="Test User",
        participant_email="user@test.com",
        previous_booking_uid="old-uid",
    )

    mock_shortener.create_url.assert_called_once()
    assert url == "https://short.test/abc"


async def test_reassign_replaces_url_in_place(controller, booking, mock_shortener):
    await controller.create_meeting_url(
        booking=booking,
        participant_name="Test User",
        participant_email="user@test.com",
        replace_existing=True,
    )

    kwargs = mock_shortener.update_url_data.call_args.kwargs
    assert kwargs["old_external_id"] == booking.uid
    assert kwargs["new_external_id"] == booking.uid


async def test_sends_meeting_url_created_event(controller, booking, mock_events):
    await controller.create_meeting_url(
        booking=booking,
        participant_name="Test User",
        participant_email="user@test.com",
        dedupe_key="ce-1:meeting.url_created:organizer",
    )

    mock_events.send_event.assert_called_once()
    call_kwargs = mock_events.send_event.call_args.kwargs
    assert call_kwargs["event"] == EventType.MEETING_URL_CREATED
    assert call_kwargs["booking_uid"] == booking.uid
    assert call_kwargs["dedupe_key"] == "ce-1:meeting.url_created:organizer"
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
