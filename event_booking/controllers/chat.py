"""Chat controller: wraps IChatClient with event emission."""

import structlog
from event_schemas.types import EventType

from event_booking.interfaces.chat import IChatClient
from event_booking.interfaces.events import IEventPublisher

logger = structlog.get_logger(__name__)


class ChatController:
    def __init__(self, *, chat_client: IChatClient, events: IEventPublisher) -> None:
        self._chat_client = chat_client
        self._events = events

    async def create_chat(self, channel_id: str, organizer_id: str, client_id: str) -> None:
        try:
            await self._chat_client.create_chat(
                channel_id=channel_id,
                organizer_id=organizer_id,
                client_id=client_id,
            )
            await self._events.send_event(
                booking_uid=channel_id,
                event=EventType.CHAT_CREATED,
                data={"channel_id": channel_id},
            )
        except Exception:
            logger.exception("Failed to create chat", channel_id=channel_id)

    async def delete_chat(self, channel_id: str, booking_uid: str) -> None:
        try:
            await self._chat_client.delete_chat(channel_id=channel_id)
            await self._events.send_event(
                booking_uid=booking_uid,
                event=EventType.CHAT_DELETED,
                data={"channel_id": channel_id},
            )
        except Exception:
            logger.exception("Failed to delete chat", channel_id=channel_id)

    async def send_message(self, channel_id: str, user_id: str, message: dict) -> None:
        try:
            await self._chat_client.send_message(channel_id=channel_id, user_id=user_id, message=message)
        except Exception:
            logger.exception("Failed to send message", channel_id=channel_id, user_id=user_id)
