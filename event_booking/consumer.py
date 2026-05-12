"""RabbitMQ consumer: parses CloudEvents and dispatches to BookingController."""

import json

import structlog
from cloudevents.core.bindings.http import HTTPMessage, from_http
from cloudevents.core.formats.json import JSONFormat
from event_schemas.types import EventType
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitMessage, RabbitQueue

from event_booking.controllers.booking import BookingController

logger = structlog.get_logger(__name__)

HANDLED_EVENTS: frozenset[str] = frozenset(
    {
        EventType.BOOKING_CREATED.value,
        EventType.BOOKING_RESCHEDULED.value,
        EventType.BOOKING_REASSIGNED.value,
        EventType.BOOKING_CANCELLED.value,
    }
)


class BookingConsumer:
    def __init__(self, booking_controller: BookingController) -> None:
        self._controller = booking_controller

    async def dispatch(self, event_type: str, booking_uid: str, data: dict) -> None:
        """Route event_type to the appropriate BookingController handler."""
        if event_type == EventType.BOOKING_CREATED.value:
            await self._controller.handle_created(booking_uid)
            return

        if event_type == EventType.BOOKING_RESCHEDULED.value:
            previous_start_time = data.get("previous_start_time")
            await self._controller.handle_rescheduled(booking_uid, previous_start_time=previous_start_time)
            return

        if event_type == EventType.BOOKING_REASSIGNED.value:
            previous_organizer_email = data.get("previous_organizer_email")
            await self._controller.handle_reassigned(booking_uid, previous_organizer_email=previous_organizer_email)
            return

        if event_type == EventType.BOOKING_CANCELLED.value:
            cancellation_reason = data.get("cancellation_reason")
            await self._controller.handle_cancelled(booking_uid, cancellation_reason=cancellation_reason)
            return

        logger.warning("Unknown event type received, ignoring", event_type=event_type, booking_uid=booking_uid)

    def register(self, broker: RabbitBroker, exchange: RabbitExchange, queue_name: str) -> None:
        """Create queue and register subscriber on the broker."""
        queue = RabbitQueue(
            name=queue_name,
            durable=True,
            arguments={
                "x-max-priority": 10,
                "x-dead-letter-exchange": f"{queue_name}.dlq",
            },
        )

        @broker.subscriber(queue, exchange)
        async def handle_message(msg: RabbitMessage) -> None:
            headers: dict[str, str] = {k: v for k, v in (msg.headers or {}).items() if isinstance(v, str)}
            body: bytes = msg.body if isinstance(msg.body, bytes) else json.dumps(msg.body).encode()

            try:
                http_msg = HTTPMessage(headers=headers, body=body)
                cloud_event = from_http(http_msg, JSONFormat())
            except Exception:
                logger.exception("Failed to parse CloudEvent", headers=headers)
                return

            event_type: str = cloud_event.get_attributes().get("type", "")
            booking_uid: str = (
                cloud_event.get_attributes().get("bookingid") or cloud_event.get_attributes().get("booking_id") or ""
            )
            raw_data = cloud_event.data
            if isinstance(raw_data, bytes):
                raw_data = json.loads(raw_data)
            data: dict = raw_data if isinstance(raw_data, dict) else {}

            if event_type not in HANDLED_EVENTS:
                logger.warning("Unhandled event type, skipping", event_type=event_type)
                return

            logger.info("Dispatching event", event_type=event_type, booking_uid=booking_uid)
            await self.dispatch(event_type, booking_uid, data)
