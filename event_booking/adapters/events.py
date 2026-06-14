"""Publishes CloudEvents to event-receiver via HTTP POST.

Delivery semantics:
- Non-2xx responses and transport errors raise :class:`EventPublishError` so
  the calling message handler fails and the inbound message is redelivered /
  dead-lettered instead of silently losing follow-up events.
- ``dedupe_key`` makes the CloudEvent ``id`` deterministic (UUIDv5), so a
  redelivered inbound message re-emits byte-identical event ids and downstream
  consumers can deduplicate.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from cloudevents.core.bindings.http import to_binary
from cloudevents.core.formats.json import JSONFormat
from cloudevents.core.v1.event import CloudEvent
from event_schemas.types import EventType
from opentelemetry import trace

logger = structlog.get_logger(__name__)

_tracer = trace.get_tracer(__name__)

# Fixed namespace for deterministic event ids (do not change: breaks dedupe on rolling deploys).
_EVENT_ID_NAMESPACE = uuid.UUID("9b1dab2e-5c3f-4f24-9f6a-1a2b3c4d5e6f")


class EventPublishError(RuntimeError):
    """Raised when event-receiver did not durably accept an event."""


def _event_id(dedupe_key: str | None) -> str:
    if dedupe_key:
        return str(uuid.uuid5(_EVENT_ID_NAMESPACE, dedupe_key))
    return str(uuid.uuid4())


class EventPublisher:
    def __init__(
        self,
        *,
        endpoint_url: str,
        api_key: str | None,
        source: str,
        timeout_seconds: float,
    ) -> None:
        if not endpoint_url:
            msg = "EVENTS_ENDPOINT_URL must be configured: event-booking is useless without an output"
            raise ValueError(msg)
        self._endpoint_url = endpoint_url
        self._api_key = api_key
        self._source = source
        self._timeout_seconds = timeout_seconds

    async def send_event(
        self,
        booking_uid: str,
        event: EventType,
        data: dict[str, Any] | None = None,
        *,
        dedupe_key: str | None = None,
    ) -> None:
        with _tracer.start_as_current_span("booking.publish_followup") as span:
            span.set_attribute("booking.uid", booking_uid)
            span.set_attribute("event.type", event.value)
            payload = {"booking_uid": booking_uid, **(data or {})}
            ce = CloudEvent(
                {
                    "type": event.value,
                    "source": self._source,
                    "id": _event_id(dedupe_key),
                    "time": datetime.now(UTC),
                    "specversion": "1.0",
                },
                json.dumps(payload).encode(),
            )
            message = to_binary(ce, JSONFormat())
            headers = dict(message.headers)
            headers["content-type"] = "application/json"
            if self._api_key:
                headers["Authorization"] = self._api_key
            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.post(self._endpoint_url, headers=headers, content=message.body)
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.exception("Failed to send event", event_type=event.value, booking_uid=booking_uid)
                msg = f"event-receiver rejected {event.value} for booking {booking_uid}: {exc}"
                raise EventPublishError(msg) from exc
        logger.info("Event sent", event_type=event.value, booking_uid=booking_uid)

    async def send_notification_command(
        self,
        *,
        booking_uid: str,
        trigger_event: str,
        recipients: list[dict[str, str]],
        template_data: dict[str, Any],
        dedupe_key: str | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "booking_uid": booking_uid,
            "booking_id": booking_uid,
            "trigger_event": trigger_event,
            "recipients": recipients,
            "template_data": template_data,
        }
        await self.send_event(
            booking_uid=booking_uid,
            event=EventType.NOTIFICATION_SEND_REQUESTED,
            data=data,
            dedupe_key=dedupe_key,
        )
