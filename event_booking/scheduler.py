"""Reminder scheduler: periodically queries upcoming bookings and publishes reminders."""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from event_schemas.types import TriggerEvent

from event_booking.interfaces.db import IBookingDatabaseAdapter
from event_booking.interfaces.events import IEventPublisher

logger = structlog.get_logger(__name__)


class ReminderScheduler:
    def __init__(
        self,
        *,
        db: IBookingDatabaseAdapter,
        events: IEventPublisher,
        interval_seconds: int,
        shift_from_minutes: int,
        shift_to_minutes: int,
    ) -> None:
        self._db = db
        self._events = events
        self._interval_seconds = interval_seconds
        self._shift_from_minutes = shift_from_minutes
        self._shift_to_minutes = shift_to_minutes
        self._running = False

    async def send_reminders(self) -> int:
        """Query upcoming bookings and publish a notification.send_requested for each."""
        now = datetime.now(UTC)
        start_from = now + timedelta(minutes=self._shift_from_minutes)
        start_to = now + timedelta(minutes=self._shift_to_minutes)

        bookings = await self._db.get_bookings(start_time_from=start_from, start_time_to=start_to)
        count = 0
        for booking in bookings:
            recipients = []
            if booking.user:
                recipients.append({"email": booking.user.email, "role": "organizer"})
            if booking.client:
                recipients.append({"email": booking.client.email, "role": "client"})

            template_data: dict = {
                "booking_uid": booking.uid,
                "start_time": booking.start_time.isoformat(),
                "end_time": booking.end_time.isoformat(),
                "title": booking.title,
            }
            if booking.user:
                template_data["organizer_name"] = booking.user.name
                template_data["organizer_email"] = booking.user.email
            if booking.client:
                template_data["client_name"] = booking.client.name
                template_data["client_email"] = booking.client.email

            await self._events.send_notification_command(
                booking_uid=booking.uid,
                trigger_event=TriggerEvent.BOOKING_REMINDER.value,
                recipients=recipients,
                template_data=template_data,
            )
            count += 1

        if count:
            logger.info("Reminders sent", count=count)
        return count

    async def run_forever(self) -> None:
        """Background loop: sleep, then send reminders, repeat until stopped."""
        self._running = True
        while self._running:
            await asyncio.sleep(self._interval_seconds)
            try:
                await self.send_reminders()
            except Exception:
                logger.exception("Error during reminder send")

    def stop(self) -> None:
        """Signal the loop to stop after the current sleep/iteration."""
        self._running = False
