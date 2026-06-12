"""RabbitMQ consumer: parses CloudEvents and dispatches to BookingController."""

import json
from time import perf_counter

import structlog
from cloudevents.core.bindings.http import HTTPMessage, from_http
from cloudevents.core.formats.json import JSONFormat
from dishka import AsyncContainer
from event_schemas.attributes import BOOKING_ID_ATTRIBUTE
from event_schemas.envelope import unwrap_payload
from event_schemas.queues import EVENTS_DLX, QueueSpec
from event_schemas.types import EventType
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitMessage, RabbitQueue

from event_booking import metrics
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


def extract_event_data(cloud_event: object) -> dict:
    """Unwrap the {original, normalized} envelope from a parsed CloudEvent.

    cloudevents 2.x core CloudEvent exposes payload via ``get_data()`` (there
    is no ``.data`` attribute); binary-mode JSON bodies may still arrive as
    raw bytes depending on content type.
    """
    raw_data = cloud_event.get_data()  # type: ignore[attr-defined]
    if isinstance(raw_data, bytes):
        raw_data = json.loads(raw_data)
    return unwrap_payload(raw_data if isinstance(raw_data, dict) else None)


class BookingConsumer:
    def __init__(self, container: AsyncContainer) -> None:
        self._container = container

    @staticmethod
    async def dispatch(
        controller: BookingController,
        event_type: str,
        booking_uid: str,
        data: dict,
        ce_id: str = "",
    ) -> None:
        """Route event_type to the appropriate BookingController handler.

        ``ce_id`` (inbound CloudEvent id) seeds deterministic dedupe keys for all
        follow-up events, so broker redeliveries do not duplicate side effects.
        """
        if event_type == EventType.BOOKING_CREATED.value:
            await controller.handle_created(booking_uid, ce_id=ce_id)
            return

        if event_type == EventType.BOOKING_RESCHEDULED.value:
            await controller.handle_rescheduled(
                booking_uid,
                previous_start_time=data.get("previous_start_time"),
                previous_booking_uid=data.get("previous_booking_uid"),
                ce_id=ce_id,
            )
            return

        if event_type == EventType.BOOKING_REASSIGNED.value:
            await controller.handle_reassigned(
                booking_uid,
                previous_organizer_email=data.get("previous_organizer_email"),
                ce_id=ce_id,
            )
            return

        if event_type == EventType.BOOKING_CANCELLED.value:
            await controller.handle_cancelled(
                booking_uid,
                cancellation_reason=data.get("cancellation_reason"),
                ce_id=ce_id,
            )
            return

        logger.warning("Unknown event type received, ignoring", event_type=event_type, booking_uid=booking_uid)

    def register(self, broker: RabbitBroker, exchange: RabbitExchange, queue_spec: QueueSpec) -> None:
        """Create the canonical per-consumer queue and register subscriber on the broker."""
        queue = RabbitQueue(
            name=queue_spec.name,
            durable=True,
            routing_key=str(queue_spec.binding),
            arguments=queue_spec.arguments,
        )

        @broker.subscriber(queue, exchange)
        async def handle_message(msg: RabbitMessage) -> None:
            started_at = perf_counter()
            headers: dict[str, str] = {k: v for k, v in (msg.headers or {}).items() if isinstance(v, str)}
            body: bytes = msg.body if isinstance(msg.body, bytes) else json.dumps(msg.body).encode()

            try:
                http_msg = HTTPMessage(headers=headers, body=body)
                cloud_event = from_http(http_msg, JSONFormat())
            except Exception:
                metrics.record_message(
                    queue=queue_spec.name,
                    event_type="unknown",
                    outcome="rejected",
                    started_at=started_at,
                )
                logger.exception("Failed to parse CloudEvent", headers=headers)
                return

            event_type: str = cloud_event.get_attributes().get("type", "")
            booking_uid: str = cloud_event.get_attributes().get(BOOKING_ID_ATTRIBUTE) or ""
            data: dict = extract_event_data(cloud_event)

            if event_type not in HANDLED_EVENTS:
                metrics.record_message(
                    queue=queue_spec.name,
                    event_type=event_type,
                    outcome="ok",
                    started_at=started_at,
                )
                logger.warning("Unhandled event type, skipping", event_type=event_type)
                return

            ce_id: str = cloud_event.get_attributes().get("id") or ""
            logger.info("Dispatching event", event_type=event_type, booking_uid=booking_uid, ce_id=ce_id)
            try:
                async with self._container() as request_container:
                    controller = await request_container.get(BookingController)
                    await self.dispatch(controller, event_type, booking_uid, data, ce_id)
            except Exception:
                # The raised error dead-letters the message (queue DLX arguments).
                metrics.record_message(
                    queue=queue_spec.name,
                    event_type=event_type,
                    outcome="rejected",
                    started_at=started_at,
                )
                raise
            metrics.record_message(
                queue=queue_spec.name,
                event_type=event_type,
                outcome="ok",
                started_at=started_at,
            )


async def ensure_dead_letter_topology(broker: RabbitBroker, queue_spec: QueueSpec) -> None:
    """Idempotently declare the DLX and this consumer's DLQ (no startup-order dependency)."""
    dlx = RabbitExchange(name=EVENTS_DLX, type=ExchangeType.TOPIC, durable=True)
    declared_dlx = await broker.declare_exchange(dlx)
    dlq = RabbitQueue(
        name=queue_spec.dlq_name,
        durable=True,
        routing_key=queue_spec.dlq_name,
        arguments=queue_spec.dlq_arguments,
    )
    declared_dlq = await broker.declare_queue(dlq)
    await declared_dlq.bind(exchange=declared_dlx, routing_key=queue_spec.dlq_name)
    logger.info("Dead-letter topology ensured", dlx=EVENTS_DLX, dlq=queue_spec.dlq_name)
