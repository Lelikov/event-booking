"""Tests for BookingConsumer dispatch routing."""

from unittest.mock import AsyncMock

from event_schemas.types import EventType

from event_booking.consumer import BookingConsumer
from tests.conftest import FakeContainer


def make_consumer() -> tuple[BookingConsumer, AsyncMock]:
    mock_controller = AsyncMock()
    consumer = BookingConsumer(FakeContainer(mock_controller))
    return consumer, mock_controller


class TestDispatch:
    async def test_dispatches_created(self) -> None:
        consumer, ctrl = make_consumer()
        await consumer.dispatch(ctrl, EventType.BOOKING_CREATED.value, "uid-1", {}, "ce-1")
        ctrl.handle_created.assert_awaited_once_with("uid-1", ce_id="ce-1")
        ctrl.handle_cancelled.assert_not_awaited()
        ctrl.handle_rescheduled.assert_not_awaited()
        ctrl.handle_reassigned.assert_not_awaited()

    async def test_dispatches_cancelled(self) -> None:
        consumer, ctrl = make_consumer()
        data = {"cancellation_reason": "Client request"}
        await consumer.dispatch(ctrl, EventType.BOOKING_CANCELLED.value, "uid-2", data, "ce-2")
        ctrl.handle_cancelled.assert_awaited_once_with("uid-2", cancellation_reason="Client request", ce_id="ce-2")

    async def test_dispatches_cancelled_without_reason(self) -> None:
        consumer, ctrl = make_consumer()
        await consumer.dispatch(ctrl, EventType.BOOKING_CANCELLED.value, "uid-3", {})
        ctrl.handle_cancelled.assert_awaited_once_with("uid-3", cancellation_reason=None, ce_id="")

    async def test_dispatches_rescheduled_with_previous_booking_uid(self) -> None:
        consumer, ctrl = make_consumer()
        data = {"previous_start_time": "2026-06-01T10:00:00+00:00", "previous_booking_uid": "old-uid"}
        await consumer.dispatch(ctrl, EventType.BOOKING_RESCHEDULED.value, "uid-4", data, "ce-4")
        ctrl.handle_rescheduled.assert_awaited_once_with(
            "uid-4",
            previous_start_time="2026-06-01T10:00:00+00:00",
            previous_booking_uid="old-uid",
            ce_id="ce-4",
        )

    async def test_dispatches_reassigned(self) -> None:
        consumer, ctrl = make_consumer()
        data = {"previous_organizer_email": "old@test.com"}
        await consumer.dispatch(ctrl, EventType.BOOKING_REASSIGNED.value, "uid-5", data, "ce-5")
        ctrl.handle_reassigned.assert_awaited_once_with(
            "uid-5",
            previous_organizer_email="old@test.com",
            ce_id="ce-5",
        )

    async def test_ignores_unknown_event(self) -> None:
        consumer, ctrl = make_consumer()
        await consumer.dispatch(ctrl, "unknown.event.type", "uid-6", {})
        ctrl.handle_created.assert_not_awaited()
        ctrl.handle_cancelled.assert_not_awaited()
        ctrl.handle_rescheduled.assert_not_awaited()
        ctrl.handle_reassigned.assert_not_awaited()


class TestRegisterContract:
    def test_uses_canonical_per_consumer_queue_spec(self) -> None:
        from event_schemas.queues import BOOKING_LIFECYCLE_BOOKING_QUEUE, RoutingKey

        assert BOOKING_LIFECYCLE_BOOKING_QUEUE.name == "events.booking.lifecycle.booking"
        assert BOOKING_LIFECYCLE_BOOKING_QUEUE.binding == RoutingKey.BOOKING_LIFECYCLE
        assert BOOKING_LIFECYCLE_BOOKING_QUEUE.arguments == {
            "x-max-priority": 10,
            "x-dead-letter-exchange": "events.dlx",
            "x-dead-letter-routing-key": "events.booking.lifecycle.booking.dlq",
        }

    def test_register_declares_queue_with_spec(self) -> None:
        from unittest.mock import MagicMock

        from event_schemas.queues import BOOKING_LIFECYCLE_BOOKING_QUEUE

        consumer, _ = make_consumer()
        broker = MagicMock()
        exchange = MagicMock()

        consumer.register(broker, exchange, BOOKING_LIFECYCLE_BOOKING_QUEUE)

        broker.subscriber.assert_called_once()
        queue = broker.subscriber.call_args.args[0]
        assert queue.name == BOOKING_LIFECYCLE_BOOKING_QUEUE.name
        assert queue.routing_key == str(BOOKING_LIFECYCLE_BOOKING_QUEUE.binding)
        # FastStream adds x-queue-type=classic; canonical args must be a subset verbatim
        assert BOOKING_LIFECYCLE_BOOKING_QUEUE.arguments.items() <= queue.arguments.items()


class TestUserEmailChange:
    async def test_updates_attendee_when_booking_uid_present(self) -> None:
        adapter = AsyncMock()
        consumer = BookingConsumer(FakeContainer(adapter))
        await consumer.handle_user_email_change(
            {"old_email": "old@x.io", "new_email": "new@x.io", "booking_uid": "book-1"}
        )
        adapter.update_attendee_email.assert_awaited_once_with(
            booking_uid="book-1", old_email="old@x.io", new_email="new@x.io"
        )

    async def test_noop_without_booking_uid(self) -> None:
        adapter = AsyncMock()
        consumer = BookingConsumer(FakeContainer(adapter))
        await consumer.handle_user_email_change({"old_email": "old@x.io", "new_email": "new@x.io", "booking_uid": None})
        adapter.update_attendee_email.assert_not_awaited()

    def test_register_user_email_subscribes_to_booking_queue(self) -> None:
        from unittest.mock import MagicMock

        from event_schemas.queues import USER_EMAIL_BOOKING_QUEUE

        broker = MagicMock()
        consumer = BookingConsumer(FakeContainer(MagicMock()))
        consumer.register_user_email(broker, MagicMock(), USER_EMAIL_BOOKING_QUEUE)
        broker.subscriber.assert_called_once()


class TestEnvelopeUnwrap:
    def test_unwrap_payload_extracts_original_for_dispatch(self) -> None:
        from event_schemas.envelope import unwrap_payload

        data = {
            "original": {"cancellation_reason": "Client request"},
            "normalized": {"participants": [{"email": "cli@example.com", "role": "client"}]},
        }

        assert unwrap_payload(data) == {"cancellation_reason": "Client request"}


class TestExtractEventData:
    def test_extracts_original_from_binary_mode_cloudevent(self) -> None:
        # Regression: handle_message used `cloud_event.data`, which does not
        # exist on cloudevents 2.x core CloudEvent (only `get_data()`), so
        # every consumed message crashed with AttributeError.
        import json

        from cloudevents.core.bindings.http import HTTPMessage, from_http
        from cloudevents.core.formats.json import JSONFormat

        from event_booking.consumer import extract_event_data

        body = json.dumps({"original": {"uid": "abc"}, "normalized": {}}).encode()
        headers = {
            "ce-type": "booking.created",
            "ce-source": "test",
            "ce-id": "1",
            "ce-specversion": "1.0",
            "ce-time": "2026-01-01T00:00:00Z",
            "content-type": "application/json",
        }
        cloud_event = from_http(HTTPMessage(headers=headers, body=body), JSONFormat())

        assert extract_event_data(cloud_event) == {"uid": "abc"}
