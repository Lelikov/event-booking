"""Tests for ReminderScheduler."""

from unittest.mock import AsyncMock

from event_booking.scheduler import ReminderScheduler
from tests.factories import make_booking


def make_scheduler(db: AsyncMock, events: AsyncMock) -> ReminderScheduler:
    return ReminderScheduler(
        db=db,
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

    async def test_skips_when_no_bookings(self) -> None:
        mock_db = AsyncMock()
        mock_db.get_bookings.return_value = []
        mock_events = AsyncMock()

        scheduler = make_scheduler(mock_db, mock_events)
        count = await scheduler.send_reminders()

        assert count == 0
        mock_events.send_notification_command.assert_not_awaited()
