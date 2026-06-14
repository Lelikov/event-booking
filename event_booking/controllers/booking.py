"""Main booking orchestrator: coordinates constraints, chat, meeting URLs, and notifications.

Reliability model — idempotent resume instead of compensation:
every step is idempotent (chat creation returns the existing channel, welcome
messages are skipped when the channel already has messages, short URLs are
keyed by external id, follow-up events carry deterministic dedupe ids derived
from the inbound CloudEvent id). Any step failure propagates, the inbound
message is redelivered / dead-lettered, and a replay resumes exactly where the
flow stopped without duplicating side effects.
"""

import structlog
from event_schemas.types import EventType, RecipientRole, TriggerEvent
from opentelemetry import trace

from event_booking import metrics
from event_booking.dtos import BookingDTO, ConstraintsResult, MeetingUrls, notification_recipient
from event_booking.interfaces.blacklist import IBlacklistChecker
from event_booking.interfaces.chat import IChatController
from event_booking.interfaces.constraints import IBookingConstraintsAnalyzer
from event_booking.interfaces.db import IBookingDatabaseAdapter
from event_booking.interfaces.events import IEventPublisher
from event_booking.interfaces.meeting import IMeetingController

logger = structlog.get_logger(__name__)

_tracer = trace.get_tracer(__name__)

CLIENT_PREFIX = "client_"

_WELCOME_MESSAGE_1 = {
    "text": "Welcome to your booking chat! Here you can communicate with your organizer.",
}
_WELCOME_MESSAGE_2 = {
    "text": "Feel free to ask any questions about your upcoming session.",
}

# Client-visible everywhere (cal.com rejectionReason, notification template data):
# never mention the blacklist itself; rejection_type='blacklisted' carries the semantics internally.
BLACKLIST_REJECTION_REASON = "Booking was rejected. Please contact the organizer."
BLACKLIST_REJECTION_TYPE = "blacklisted"


def _dedupe(ce_id: str, *parts: str) -> str | None:
    """Deterministic dedupe key scoped to the inbound CloudEvent id."""
    if not ce_id:
        return None
    return ":".join((ce_id, *parts))


class BookingController:
    def __init__(  # noqa: PLR0913
        self,
        *,
        db: IBookingDatabaseAdapter,
        events: IEventPublisher,
        chat_controller: IChatController,
        meeting_controller: IMeetingController,
        constraints_analyzer: IBookingConstraintsAnalyzer,
        blacklist_checker: IBlacklistChecker,
        is_enable_constraints: bool = False,
    ) -> None:
        self._db = db
        self._events = events
        self._chat = chat_controller
        self._meeting = meeting_controller
        self._constraints = constraints_analyzer
        self._blacklist = blacklist_checker
        self._is_enable_constraints = is_enable_constraints

    async def handle_created(self, booking_uid: str, *, ce_id: str = "") -> None:
        """Handle booking.created: run constraints, create chat + meeting URLs, notify."""
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("handle_created: booking not found", booking_uid=booking_uid)
            return

        with _tracer.start_as_current_span("booking.blacklist_check") as span:
            span.set_attribute("booking.uid", booking_uid)
            is_blacklisted = booking.client and await self._blacklist.is_blacklisted(booking.client.email)
        if is_blacklisted:
            logger.info("handle_created: client is blacklisted, rejecting", booking_uid=booking.uid)
            result = ConstraintsResult(
                is_allowed=False,
                rejection_reasons=[BLACKLIST_REJECTION_REASON],
                rejection_type=BLACKLIST_REJECTION_TYPE,
            )
            await self._reject_booking(
                booking,
                result,
                trigger_event=TriggerEvent.BOOKING_REJECTED_BLACKLISTED,
                ce_id=ce_id,
            )
            return

        if self._is_enable_constraints and booking.client:
            attendee_bookings = await self._db.get_attendee_bookings_by_email(
                email=booking.client.email,
                exclude_booking_id=booking.id,
            )
            result = self._constraints.analyze_on_create(booking=booking, attendee_bookings=attendee_bookings)
            if not result.is_allowed:
                await self._reject_booking(booking, result, trigger_event=TriggerEvent.BOOKING_REJECTED, ce_id=ce_id)
                return

        await self._ensure_chat_with_welcome(booking, ce_id=ce_id)
        urls = await self._create_meeting_urls(booking, ce_id=ce_id)
        await self._store_client_video_url(booking, urls)
        await self._notify_participants(booking, TriggerEvent.BOOKING_CREATED, {}, urls, ce_id=ce_id)

    async def handle_rescheduled(
        self,
        booking_uid: str,
        *,
        previous_start_time: str | None = None,
        previous_booking_uid: str | None = None,
        ce_id: str = "",
    ) -> None:
        """Handle booking.rescheduled: clean up the old uid's chat, move URLs, notify with new URLs."""
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("handle_rescheduled: booking not found", booking_uid=booking_uid)
            return

        previous_uid = previous_booking_uid or booking.from_reschedule
        if previous_uid and previous_uid != booking.uid:
            # cal.com mints a NEW uid on reschedule — the old uid's chat would be orphaned.
            await self._chat.delete_chat(
                previous_uid,
                booking.uid,
                dedupe_key=_dedupe(ce_id, EventType.CHAT_DELETED.value, previous_uid),
            )

        await self._ensure_chat_with_welcome(booking, ce_id=ce_id)
        urls = await self._create_meeting_urls(booking, ce_id=ce_id, previous_booking_uid=previous_uid)
        await self._store_client_video_url(booking, urls)

        extra: dict = {}
        if previous_start_time is not None:
            extra["previous_start_time"] = previous_start_time
        await self._notify_participants(booking, TriggerEvent.BOOKING_RESCHEDULED, extra, urls, ce_id=ce_id)

    async def handle_reassigned(
        self,
        booking_uid: str,
        *,
        previous_organizer_email: str | None = None,
        ce_id: str = "",
    ) -> None:
        """Handle booking.reassigned: recreate chat (hard delete first) + meeting URLs, notify."""
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("handle_reassigned: booking not found", booking_uid=booking_uid)
            return

        # Hard delete: GetStream refuses to recreate a soft-deleted channel id.
        await self._chat.delete_chat(
            booking.uid,
            booking.uid,
            hard=True,
            dedupe_key=_dedupe(ce_id, EventType.CHAT_DELETED.value, booking.uid),
        )
        await self._ensure_chat_with_welcome(booking, ce_id=ce_id)

        urls = await self._create_meeting_urls(booking, ce_id=ce_id, replace_existing=True)
        await self._store_client_video_url(booking, urls)

        extra: dict = {}
        if previous_organizer_email is not None:
            extra["previous_organizer_email"] = previous_organizer_email
        await self._notify_participants(booking, TriggerEvent.BOOKING_REASSIGNED, extra, urls, ce_id=ce_id)

    async def handle_cancelled(
        self,
        booking_uid: str,
        *,
        cancellation_reason: str | None = None,
        ce_id: str = "",
    ) -> None:
        """Handle booking.cancelled: notify, delete chat + meeting URLs."""
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("handle_cancelled: booking not found", booking_uid=booking_uid)
            return

        extra: dict = {}
        if cancellation_reason is not None:
            extra["cancellation_reason"] = cancellation_reason
        await self._notify_participants(booking, TriggerEvent.BOOKING_CANCELLED, extra, None, ce_id=ce_id)

        await self._chat.delete_chat(
            booking.uid,
            booking.uid,
            dedupe_key=_dedupe(ce_id, EventType.CHAT_DELETED.value, booking.uid),
        )
        await self._meeting.delete_meeting_url(
            booking=booking,
            external_id_prefix="",
            dedupe_key=_dedupe(ce_id, EventType.MEETING_URL_DELETED.value, "organizer"),
        )
        await self._meeting.delete_meeting_url(
            booking=booking,
            external_id_prefix=CLIENT_PREFIX,
            dedupe_key=_dedupe(ce_id, EventType.MEETING_URL_DELETED.value, "client"),
        )

    async def _ensure_chat_with_welcome(self, booking: BookingDTO, *, ce_id: str) -> None:
        """Create GetStream chat channel; send welcome messages only once (idempotent on redelivery)."""
        if not booking.user or not booking.client:
            return

        await self._chat.create_chat(
            booking.uid,
            booking.user.email,
            booking.client.email,
            dedupe_key=_dedupe(ce_id, EventType.CHAT_CREATED.value, booking.uid),
        )
        if await self._chat.has_messages(booking.uid):
            return
        await self._chat.send_message(booking.uid, booking.user.email, _WELCOME_MESSAGE_1)
        await self._chat.send_message(booking.uid, booking.user.email, _WELCOME_MESSAGE_2)

    async def _create_meeting_urls(
        self,
        booking: BookingDTO,
        *,
        ce_id: str,
        previous_booking_uid: str | None = None,
        replace_existing: bool = False,
    ) -> MeetingUrls:
        """Create each participant's OWN tokenized short URL (never shared between roles)."""
        organizer_url: str | None = None
        client_url: str | None = None

        if booking.user:
            organizer_url = await self._meeting.create_meeting_url(
                booking=booking,
                participant_name=booking.user.name,
                participant_email=booking.user.email,
                external_id_prefix="",
                previous_booking_uid=previous_booking_uid,
                replace_existing=replace_existing,
                dedupe_key=_dedupe(ce_id, EventType.MEETING_URL_CREATED.value, "organizer"),
            )

        if booking.client:
            client_url = await self._meeting.create_meeting_url(
                booking=booking,
                participant_name=booking.client.name,
                participant_email=booking.client.email,
                external_id_prefix=CLIENT_PREFIX,
                previous_booking_uid=previous_booking_uid,
                replace_existing=replace_existing,
                dedupe_key=_dedupe(ce_id, EventType.MEETING_URL_CREATED.value, "client"),
            )

        return MeetingUrls(organizer=organizer_url, client=client_url)

    async def _store_client_video_url(self, booking: BookingDTO, urls: MeetingUrls) -> None:
        """Write the client's short URL into cal.com Booking.metadata.videoCallUrl.

        cal.com surfaces videoCallUrl to the attendee (confirmation email, booking
        page). The client URL carries client-role claims only, so it is safe to
        store there; the organizer's moderator URL is delivered exclusively via
        the organizer's own notification.
        """
        if not urls.client:
            return
        await self._db.update_booking_video_url(booking.uid, urls.client)

    async def _notify_participants(
        self,
        booking: BookingDTO,
        trigger_event: TriggerEvent,
        extra_template_data: dict,
        urls: MeetingUrls | None,
        *,
        ce_id: str,
    ) -> None:
        """Send one notification command per participant with that participant's own meeting URL."""
        base_data = {**self._build_template_data(booking), **extra_template_data}

        recipients: list[tuple[dict[str, str], str | None]] = []
        if booking.user:
            recipient = notification_recipient(booking.user.email, RecipientRole.ORGANIZER.value, booking.user.locale)
            recipients.append((recipient, urls.organizer if urls else None))
        if booking.client:
            recipient = notification_recipient(booking.client.email, RecipientRole.CLIENT.value, booking.client.locale)
            recipients.append((recipient, urls.client if urls else None))

        for recipient, meeting_url in recipients:
            template_data = dict(base_data)
            if meeting_url:
                template_data["meeting_url"] = meeting_url
            await self._events.send_notification_command(
                booking_uid=booking.uid,
                trigger_event=trigger_event.value,
                recipients=[recipient],
                template_data=template_data,
                dedupe_key=_dedupe(ce_id, "notification", trigger_event.value, recipient["role"]),
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

    async def _reject_booking(
        self,
        booking: BookingDTO,
        result: ConstraintsResult,
        *,
        trigger_event: TriggerEvent,
        ce_id: str,
    ) -> None:
        """Shared rejection flow: cal.com status update, notification command, booking.rejected event."""
        metrics.REJECTIONS_TOTAL.labels(rejection_type=result.rejection_type or "unknown").inc()
        await self._db.reject_booking(booking_id=booking.id, reason="; ".join(result.rejection_reasons))
        await self._send_rejection_notification(booking, result, trigger_event=trigger_event, ce_id=ce_id)
        await self._events.send_event(
            booking_uid=booking.uid,
            event=EventType.BOOKING_REJECTED,
            data=self._build_rejected_payload(booking, result),
            dedupe_key=_dedupe(ce_id, EventType.BOOKING_REJECTED.value),
        )

    async def _send_rejection_notification(
        self,
        booking: BookingDTO,
        result: ConstraintsResult,
        *,
        trigger_event: TriggerEvent,
        ce_id: str,
    ) -> None:
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
            recipients.append(
                notification_recipient(booking.client.email, RecipientRole.CLIENT.value, booking.client.locale)
            )

        await self._events.send_notification_command(
            booking_uid=booking.uid,
            trigger_event=trigger_event.value,
            recipients=recipients,
            template_data=template_data,
            dedupe_key=_dedupe(ce_id, "notification", trigger_event.value, "client"),
        )

    @staticmethod
    def _build_template_data(booking: BookingDTO) -> dict:
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

        return data
