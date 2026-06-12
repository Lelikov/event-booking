"""Tests for /metrics exposition, consumer RED counters and business counters."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from prometheus_client import REGISTRY

from event_booking import main
from event_booking.consumer import BookingConsumer
from event_booking.controllers.booking import BookingController
from event_booking.controllers.chat import ChatController
from event_booking.controllers.meeting import MeetingController
from event_booking.scheduler import ReminderScheduler
from tests.adapters.test_blacklist import FakeAdminApi, FakeClock
from tests.adapters.test_blacklist import make_client as make_blacklist_client
from tests.conftest import FakeContainer
from tests.factories import make_booking

QUEUE = "events.booking.lifecycle.booking"


def _sample(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


class FakeMessage:
    def __init__(self, headers: dict, body: bytes) -> None:
        self.headers = headers
        self.body = body


def _cloud_event_message(event_type: str = "booking.created") -> FakeMessage:
    headers = {
        "ce-specversion": "1.0",
        "ce-id": "evt-001",
        "ce-type": event_type,
        "ce-source": "booking",
        "ce-time": "2026-01-15T10:00:00Z",
        "ce-bookingid": "book-123",
        "content-type": "application/json",
    }
    return FakeMessage(headers=headers, body=json.dumps({"original": {}, "normalized": {}}).encode())


def _registered_handler(controller: AsyncMock):
    """Register the consumer on a mock broker and return the captured message handler."""
    from event_schemas.queues import BOOKING_LIFECYCLE_BOOKING_QUEUE

    consumer = BookingConsumer(FakeContainer(controller))
    broker = MagicMock()
    consumer.register(broker, MagicMock(), BOOKING_LIFECYCLE_BOOKING_QUEUE)
    return broker.subscriber.return_value.call_args.args[0]


class TestMetricsEndpoint:
    def test_metrics_route_registered(self) -> None:
        paths = {route.path for route in main.app.routes}

        assert "/metrics" in paths

    async def test_metrics_returns_prometheus_exposition(self) -> None:
        response = await main.metrics_endpoint()

        assert response.status_code == 200  # noqa: PLR2004
        assert response.media_type.startswith("text/plain")
        assert b"messages_processed_total" in response.body


class TestConsumerRedMetrics:
    async def test_dispatched_message_counts_ok_and_duration(self) -> None:
        handler = _registered_handler(AsyncMock())
        labels = {"queue": QUEUE, "event_type": "booking.created", "outcome": "ok"}
        before = _sample("messages_processed_total", labels)
        duration_before = _sample("message_processing_seconds_count", {"queue": QUEUE})

        await handler(_cloud_event_message())

        assert _sample("messages_processed_total", labels) == before + 1
        assert _sample("message_processing_seconds_count", {"queue": QUEUE}) == duration_before + 1

    async def test_unparseable_message_counts_rejected_unknown(self) -> None:
        handler = _registered_handler(AsyncMock())
        labels = {"queue": QUEUE, "event_type": "unknown", "outcome": "rejected"}
        before = _sample("messages_processed_total", labels)

        await handler(FakeMessage(headers={}, body=b"garbage"))

        assert _sample("messages_processed_total", labels) == before + 1

    async def test_dispatch_failure_counts_rejected(self) -> None:
        controller = AsyncMock()
        controller.handle_created.side_effect = RuntimeError("boom")
        handler = _registered_handler(controller)
        labels = {"queue": QUEUE, "event_type": "booking.created", "outcome": "rejected"}
        before = _sample("messages_processed_total", labels)

        with pytest.raises(RuntimeError):
            await handler(_cloud_event_message())

        assert _sample("messages_processed_total", labels) == before + 1


class TestBlacklistCheckCounters:
    async def test_hit_and_miss(self) -> None:
        client = make_blacklist_client(FakeAdminApi(values=["spam@example.com"]), FakeClock())
        hit_before = _sample("booking_blacklist_checks_total", {"result": "hit"})
        miss_before = _sample("booking_blacklist_checks_total", {"result": "miss"})

        assert await client.is_blacklisted("spam@example.com") is True
        assert await client.is_blacklisted("ok@example.com") is False

        assert _sample("booking_blacklist_checks_total", {"result": "hit"}) == hit_before + 1
        assert _sample("booking_blacklist_checks_total", {"result": "miss"}) == miss_before + 1

    async def test_fail_open(self) -> None:
        api = FakeAdminApi()
        api.fail = True
        client = make_blacklist_client(api, FakeClock())
        before = _sample("booking_blacklist_checks_total", {"result": "fail_open"})

        assert await client.is_blacklisted("any@example.com") is False

        assert _sample("booking_blacklist_checks_total", {"result": "fail_open"}) == before + 1


class TestRejectionCounter:
    async def test_blacklisted_creation_counts_rejection(self) -> None:
        booking = make_booking()
        db = AsyncMock()
        db.get_booking = AsyncMock(return_value=booking)
        blacklist = AsyncMock()
        blacklist.is_blacklisted = AsyncMock(return_value=True)
        controller = BookingController(
            db=db,
            events=AsyncMock(),
            chat_controller=AsyncMock(),
            meeting_controller=AsyncMock(),
            constraints_analyzer=MagicMock(),
            blacklist_checker=blacklist,
        )
        labels = {"rejection_type": "blacklisted"}
        before = _sample("booking_rejections_total", labels)

        await controller.handle_created(booking.uid, ce_id="ce-1")

        assert _sample("booking_rejections_total", labels) == before + 1


class TestSideEffectCounters:
    async def test_chat_created_counter(self) -> None:
        controller = ChatController(chat_client=AsyncMock(), events=AsyncMock())
        before = _sample("booking_chats_created_total")

        await controller.create_chat("uid-1", "org@test.com", "cli@test.com")

        assert _sample("booking_chats_created_total") == before + 1

    async def test_meeting_url_created_counter(self) -> None:
        shortener = AsyncMock()
        shortener.create_url.return_value = "https://short.test/abc"
        chat_client = MagicMock()
        chat_client.create_token = AsyncMock(return_value="chat-jwt")
        controller = MeetingController(
            shortener=shortener,
            chat_client=chat_client,
            events=AsyncMock(),
            jitsi_jwt_secret="secret",
            jitsi_jwt_aud="aud",
            jitsi_jwt_iss="iss",
            jitsi_jwt_sub="sub",
            meeting_host_url="https://meet.test",
        )
        booking = make_booking()
        before = _sample("booking_meeting_urls_created_total", {"role": "organizer"})

        await controller.create_meeting_url(
            booking=booking,
            participant_name="Org",
            participant_email="org@test.com",
        )

        assert _sample("booking_meeting_urls_created_total", {"role": "organizer"}) == before + 1

    async def test_reminders_sent_counter(self) -> None:
        db = AsyncMock()
        db.get_bookings = AsyncMock(return_value=[make_booking()])
        scheduler = ReminderScheduler(
            container=FakeContainer(db),
            events=AsyncMock(),
            interval_seconds=300,
            shift_from_minutes=55,
            shift_to_minutes=65,
        )
        before = _sample("booking_reminders_sent_total")

        sent = await scheduler.send_reminders()

        assert sent == 1
        assert _sample("booking_reminders_sent_total") == before + 1
