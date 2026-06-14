"""Chat controller: wraps IChatClient with event emission.

Failures propagate to the message handler so the broker redelivers /
dead-letters; every wrapped operation is idempotent at the adapter level,
which makes redelivery a safe resume instead of a duplicate side effect.
"""

import structlog
from event_schemas.types import EventType
from opentelemetry import trace

from event_booking import metrics
from event_booking.interfaces.chat import IChatClient
from event_booking.interfaces.events import IEventPublisher

logger = structlog.get_logger(__name__)

_tracer = trace.get_tracer(__name__)


class ChatController:
    def __init__(self, *, chat_client: IChatClient, events: IEventPublisher) -> None:
        self._chat_client = chat_client
        self._events = events

    async def create_chat(
        self, channel_id: str, organizer_id: str, client_id: str, *, dedupe_key: str | None = None
    ) -> None:
        with _tracer.start_as_current_span("booking.chat_create") as span:
            span.set_attribute("booking.uid", channel_id)
            await self._chat_client.create_chat(
                channel_id=channel_id,
                organizer_id=organizer_id,
                client_id=client_id,
            )
        metrics.CHATS_CREATED_TOTAL.inc()
        await self._events.send_event(
            booking_uid=channel_id,
            event=EventType.CHAT_CREATED,
            data={"channel_id": channel_id},
            dedupe_key=dedupe_key,
        )

    async def has_messages(self, channel_id: str) -> bool:
        return await self._chat_client.has_messages(channel_id=channel_id)

    async def delete_chat(
        self, channel_id: str, booking_uid: str, *, hard: bool = False, dedupe_key: str | None = None
    ) -> None:
        await self._chat_client.delete_chat(channel_id=channel_id, hard=hard)
        await self._events.send_event(
            booking_uid=booking_uid,
            event=EventType.CHAT_DELETED,
            data={"channel_id": channel_id},
            dedupe_key=dedupe_key,
        )

    async def send_message(self, channel_id: str, user_id: str, message: dict) -> None:
        await self._chat_client.send_message(channel_id=channel_id, user_id=user_id, message=message)
