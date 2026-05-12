"""Tests for BookingConsumer dispatch routing."""

from unittest.mock import AsyncMock

from event_schemas.types import EventType

from event_booking.consumer import BookingConsumer


def make_consumer() -> tuple[BookingConsumer, AsyncMock]:
    mock_controller = AsyncMock()
    consumer = BookingConsumer(mock_controller)
    return consumer, mock_controller


class TestDispatch:
    async def test_dispatches_created(self) -> None:
        consumer, ctrl = make_consumer()
        await consumer.dispatch(EventType.BOOKING_CREATED.value, "uid-1", {})
        ctrl.handle_created.assert_awaited_once_with("uid-1")
        ctrl.handle_cancelled.assert_not_awaited()
        ctrl.handle_rescheduled.assert_not_awaited()
        ctrl.handle_reassigned.assert_not_awaited()

    async def test_dispatches_cancelled(self) -> None:
        consumer, ctrl = make_consumer()
        data = {"cancellation_reason": "Client request"}
        await consumer.dispatch(EventType.BOOKING_CANCELLED.value, "uid-2", data)
        ctrl.handle_cancelled.assert_awaited_once_with("uid-2", cancellation_reason="Client request")

    async def test_dispatches_cancelled_without_reason(self) -> None:
        consumer, ctrl = make_consumer()
        await consumer.dispatch(EventType.BOOKING_CANCELLED.value, "uid-3", {})
        ctrl.handle_cancelled.assert_awaited_once_with("uid-3", cancellation_reason=None)

    async def test_dispatches_rescheduled(self) -> None:
        consumer, ctrl = make_consumer()
        data = {"previous_start_time": "2026-06-01T10:00:00+00:00"}
        await consumer.dispatch(EventType.BOOKING_RESCHEDULED.value, "uid-4", data)
        ctrl.handle_rescheduled.assert_awaited_once_with("uid-4", previous_start_time="2026-06-01T10:00:00+00:00")

    async def test_dispatches_reassigned(self) -> None:
        consumer, ctrl = make_consumer()
        data = {"previous_organizer_email": "old@test.com"}
        await consumer.dispatch(EventType.BOOKING_REASSIGNED.value, "uid-5", data)
        ctrl.handle_reassigned.assert_awaited_once_with("uid-5", previous_organizer_email="old@test.com")

    async def test_ignores_unknown_event(self) -> None:
        consumer, ctrl = make_consumer()
        await consumer.dispatch("unknown.event.type", "uid-6", {})
        ctrl.handle_created.assert_not_awaited()
        ctrl.handle_cancelled.assert_not_awaited()
        ctrl.handle_rescheduled.assert_not_awaited()
        ctrl.handle_reassigned.assert_not_awaited()
