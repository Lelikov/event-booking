"""Main booking orchestrator: coordinates constraints, chat, meeting URLs, and notifications."""

import structlog
from event_schemas.types import EventType, RecipientRole, TriggerEvent

from event_booking.dtos import BookingDTO, ConstraintsResult
from event_booking.interfaces.constraints import IBookingConstraintsAnalyzer
from event_booking.interfaces.db import IBookingDatabaseAdapter
from event_booking.interfaces.events import IEventPublisher
from event_booking.interfaces.meeting import IMeetingController

logger = structlog.get_logger(__name__)

CLIENT_PREFIX = "client_"

_WELCOME_MESSAGE_1 = {
    "text": "Welcome to your booking chat! Here you can communicate with your organizer.",
}
_WELCOME_MESSAGE_2 = {
    "text": "Feel free to ask any questions about your upcoming session.",
}


class BookingController:
    def __init__(  # noqa: PLR0913
        self,
        *,
        db: IBookingDatabaseAdapter,
        events: IEventPublisher,
        chat_controller: object,
        meeting_controller: IMeetingController,
        constraints_analyzer: IBookingConstraintsAnalyzer,
        is_enable_constraints: bool = False,
    ) -> None:
        self._db = db
        self._events = events
        self._chat = chat_controller
        self._meeting = meeting_controller
        self._constraints = constraints_analyzer
        self._is_enable_constraints = is_enable_constraints

    async def handle_created(self, booking_uid: str) -> None:
        """Handle booking.created: run constraints, create chat + meeting URLs, notify."""
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("handle_created: booking not found", booking_uid=booking_uid)
            return

        if self._is_enable_constraints and booking.client:
            attendee_bookings = await self._db.get_attendee_bookings_by_email(
                email=booking.client.email,
                exclude_booking_id=booking.id,
            )
            result = self._constraints.analyze_on_create(booking=booking, attendee_bookings=attendee_bookings)
            if not result.is_allowed:
                await self._db.reject_booking(booking_id=booking.id, reason="; ".join(result.rejection_reasons))
                await self._send_rejection_notification(booking, result)
                await self._events.send_event(
                    booking_uid=booking.uid,
                    event=EventType.BOOKING_REJECTED,
                    data=self._build_rejected_payload(booking, result),
                )
                return

        await self._process_booking_flow(booking)

    async def handle_rescheduled(self, booking_uid: str, previous_start_time: str | None = None) -> None:
        """Handle booking.rescheduled: update URLs and notify."""
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("handle_rescheduled: booking not found", booking_uid=booking_uid)
            return

        await self._process_booking_flow(booking, is_update_url_data=True)

        template_data = self._build_template_data(booking)
        if previous_start_time is not None:
            template_data["previous_start_time"] = previous_start_time

        await self._send_notification(booking, TriggerEvent.BOOKING_RESCHEDULED, template_data)

    async def handle_reassigned(self, booking_uid: str, previous_organizer_email: str | None = None) -> None:
        """Handle booking.reassigned: recreate chat + meeting URLs, notify."""
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("handle_reassigned: booking not found", booking_uid=booking_uid)
            return

        await self._chat.delete_chat(channel_id=booking.uid, booking_uid=booking.uid)
        await self._create_chat_with_welcome(booking)

        await self._create_meeting_urls(booking)

        template_data = self._build_template_data(booking)
        if previous_organizer_email is not None:
            template_data["previous_organizer_email"] = previous_organizer_email

        await self._send_notification(booking, TriggerEvent.BOOKING_REASSIGNED, template_data)

    async def handle_cancelled(self, booking_uid: str, cancellation_reason: str | None = None) -> None:
        """Handle booking.cancelled: notify, delete chat + meeting URLs."""
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("handle_cancelled: booking not found", booking_uid=booking_uid)
            return

        template_data = self._build_template_data(booking)
        if cancellation_reason is not None:
            template_data["cancellation_reason"] = cancellation_reason

        await self._send_notification(booking, TriggerEvent.BOOKING_CANCELLED, template_data)

        await self._chat.delete_chat(channel_id=booking.uid, booking_uid=booking.uid)
        await self._meeting.delete_meeting_url(booking=booking, external_id_prefix="")
        await self._meeting.delete_meeting_url(booking=booking, external_id_prefix=CLIENT_PREFIX)

    async def _process_booking_flow(self, booking: BookingDTO, *, is_update_url_data: bool = False) -> None:
        """Shared flow: create chat, create meeting URLs, notify on creation."""
        await self._create_chat_with_welcome(booking)
        organizer_url = await self._create_meeting_urls(booking, is_update_url_data=is_update_url_data)

        if is_update_url_data:
            return

        template_data = self._build_template_data(booking, organizer_meeting_url=organizer_url)
        await self._send_notification(booking, TriggerEvent.BOOKING_CREATED, template_data)

    async def _create_chat_with_welcome(self, booking: BookingDTO) -> None:
        """Create GetStream chat channel and send two welcome messages from organizer."""
        if not booking.user or not booking.client:
            return

        await self._chat.create_chat(
            channel_id=booking.uid,
            organizer_id=booking.user.email,
            client_id=booking.client.email,
        )
        await self._chat.send_message(
            channel_id=booking.uid,
            user_id=booking.user.email,
            message=_WELCOME_MESSAGE_1,
        )
        await self._chat.send_message(
            channel_id=booking.uid,
            user_id=booking.user.email,
            message=_WELCOME_MESSAGE_2,
        )

    async def _create_meeting_urls(self, booking: BookingDTO, *, is_update_url_data: bool = False) -> str:
        """Create (or update) short meeting URLs for organizer and client."""
        organizer_url = ""
        if booking.user:
            organizer_url = await self._meeting.create_meeting_url(
                booking=booking,
                participant_name=booking.user.name,
                participant_email=booking.user.email,
                is_update_url_data=is_update_url_data,
                external_id_prefix="",
            )

        if booking.client:
            await self._meeting.create_meeting_url(
                booking=booking,
                participant_name=booking.client.name,
                participant_email=booking.client.email,
                is_update_url_data=is_update_url_data,
                external_id_prefix=CLIENT_PREFIX,
            )

        return organizer_url

    async def _send_notification(
        self,
        booking: BookingDTO,
        trigger_event: TriggerEvent,
        template_data: dict,
    ) -> None:
        """Build recipients from booking user/client and dispatch notification command."""
        recipients: list[dict[str, str]] = []
        if booking.user:
            recipients.append({"email": booking.user.email, "role": RecipientRole.ORGANIZER.value})
        if booking.client:
            recipients.append({"email": booking.client.email, "role": RecipientRole.CLIENT.value})

        await self._events.send_notification_command(
            booking_uid=booking.uid,
            trigger_event=trigger_event.value,
            recipients=recipients,
            template_data=template_data,
        )

    @staticmethod
    def _build_rejected_payload(booking: BookingDTO, result: ConstraintsResult) -> dict:
        """Build the canonical BookingRejectedPayload dict for booking.rejected."""
        payload: dict = {
            "client_email": booking.client.email if booking.client else "",
            "rejection_type": result.rejection_type,
            "rejection_reasons": result.rejection_reasons,
            "has_active_booking": result.has_active_booking,
        }
        if result.available_from is not None:
            payload["available_from"] = result.available_from.isoformat()
        if result.active_booking_start is not None:
            payload["active_booking_start"] = result.active_booking_start.isoformat()
        return payload

    async def _send_rejection_notification(self, booking: BookingDTO, result: ConstraintsResult) -> None:
        """Send rejection notification command with constraint details."""
        template_data: dict = {
            "rejection_reasons": result.rejection_reasons,
            "rejection_type": result.rejection_type,
            "has_active_booking": result.has_active_booking,
        }
        if result.active_booking_start is not None:
            template_data["active_booking_start"] = result.active_booking_start.isoformat()
        if result.available_from is not None:
            template_data["available_from"] = result.available_from.isoformat()

        recipients: list[dict[str, str]] = []
        if booking.client:
            recipients.append({"email": booking.client.email, "role": RecipientRole.CLIENT.value})

        await self._events.send_notification_command(
            booking_uid=booking.uid,
            trigger_event=TriggerEvent.BOOKING_REJECTED.value,
            recipients=recipients,
            template_data=template_data,
        )

    @staticmethod
    def _build_template_data(booking: BookingDTO, *, organizer_meeting_url: str | None = None) -> dict:
        """Build base template data dict from booking fields."""
        data: dict = {
            "booking_uid": booking.uid,
            "start_time": booking.start_time.isoformat(),
            "end_time": booking.end_time.isoformat(),
            "title": booking.title,
        }

        if booking.user:
            data["organizer_name"] = booking.user.name
            data["organizer_email"] = booking.user.email
            data["organizer_time_zone"] = booking.user.time_zone

        if booking.client:
            data["client_name"] = booking.client.name
            data["client_email"] = booking.client.email
            data["client_time_zone"] = booking.client.time_zone

        if organizer_meeting_url:
            data["meeting_url"] = organizer_meeting_url

        return data
