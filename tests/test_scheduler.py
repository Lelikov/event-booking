"""Tests for ReminderScheduler."""

from unittest.mock import AsyncMock

from event_booking.scheduler import ReminderScheduler
from tests.conftest import FakeContainer
from tests.factories import make_booking


def make_scheduler(db: AsyncMock, events: AsyncMock) -> ReminderScheduler:
    return ReminderScheduler(
        container=FakeContainer(db),
        events=events,
        interval_seconds=300,
        shift_from_minutes=55,
        shift_to_minutes=65,
    )


class TestSendReminders:
    async def test_sends_reminder_for_upcoming_booking(self) -> None:
        booking = make_booking(uid="booking-abc")
        mock_db = AsyncMock()
        mock_db.get_bookings.return_value = [booking]
        mock_events = AsyncMock()

        scheduler = make_scheduler(mock_db, mock_events)
        count = await scheduler.send_reminders()

        assert count == 1
        mock_events.send_notification_command.assert_awaited_once()
        call_kwargs = mock_events.send_notification_command.call_args.kwargs
        assert call_kwargs["booking_uid"] == "booking-abc"
        assert call_kwargs["trigger_event"] == "BOOKING_REMINDER"

    async def test_marks_reminder_sent_to_prevent_duplicates(self) -> None:
        """10-min window / 5-min poll sees every booking twice — the persistent marker must be written."""
        booking = make_booking(uid="booking-abc")
        mock_db = AsyncMock()
        mock_db.get_bookings.return_value = [booking]
        mock_events = AsyncMock()

        scheduler = make_scheduler(mock_db, mock_events)
        await scheduler.send_reminders()

        mock_db.mark_reminder_sent.assert_awaited_once()
        args, kwargs = mock_db.mark_reminder_sent.call_args
        assert args[0] == "booking-abc"
        assert "sent_at" in kwargs

    async def test_reminder_command_has_deterministic_dedupe_key(self) -> None:
        booking = make_booking(uid="booking-abc")
        mock_db = AsyncMock()
        mock_db.get_bookings.return_value = [booking]
        mock_events = AsyncMock()

        scheduler = make_scheduler(mock_db, mock_events)
        await scheduler.send_reminders()

        call_kwargs = mock_events.send_notification_command.call_args.kwargs
        assert call_kwargs["dedupe_key"] == "reminder:booking-abc"

    async def test_emits_booking_reminder_sent_event(self) -> None:
        from event_schemas.types import EventType

        booking = make_booking(uid="booking-abc")
        mock_db = AsyncMock()
        mock_db.get_bookings.return_value = [booking]
        mock_events = AsyncMock()

        scheduler = make_scheduler(mock_db, mock_events)
        await scheduler.send_reminders()

        mock_events.send_event.assert_awaited_once()
        call_kwargs = mock_events.send_event.call_args.kwargs
        assert call_kwargs["event"] == EventType.BOOKING_REMINDER_SENT
        assert call_kwargs["data"] == {"email": booking.client.email}
        assert call_kwargs["dedupe_key"] == "reminder_sent:booking-abc"

    async def test_skips_when_no_bookings(self) -> None:
        mock_db = AsyncMock()
        mock_db.get_bookings.return_value = []
        mock_events = AsyncMock()

        scheduler = make_scheduler(mock_db, mock_events)
        count = await scheduler.send_reminders()

        assert count == 0
        mock_events.send_notification_command.assert_not_awaited()
