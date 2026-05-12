"""Publishes CloudEvents to event-receiver via HTTP POST."""

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

logger = structlog.get_logger(__name__)


class EventPublisher:
    def __init__(
        self,
        *,
        endpoint_url: str | None,
        api_key: str | None,
        source: str,
        timeout_seconds: float,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._api_key = api_key
        self._source = source
        self._timeout_seconds = timeout_seconds

    async def send_event(
        self,
        booking_uid: str,
        event: EventType,
        data: dict[str, Any] | None = None,
    ) -> None:
        if not self._endpoint_url:
            return
        payload = {"booking_uid": booking_uid, **(data or {})}
        ce = CloudEvent(
            {
                "type": event.value,
                "source": self._source,
                "id": str(uuid.uuid4()),
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
                await client.post(self._endpoint_url, headers=headers, content=message.body)
            logger.info("Event sent", event_type=event.value, booking_uid=booking_uid)
        except Exception:
            logger.exception("Failed to send event", event_type=event.value, booking_uid=booking_uid)

    async def send_notification_command(
        self,
        *,
        booking_uid: str,
        trigger_event: str,
        recipients: list[dict[str, str]],
        template_data: dict[str, Any],
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
        )
