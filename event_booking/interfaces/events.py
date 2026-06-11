"""Event publisher protocol."""

from typing import Any, Protocol

from event_schemas.types import EventType


class IEventPublisher(Protocol):
    async def send_event(
        self,
        booking_uid: str,
        event: EventType,
        data: dict[str, Any] | None = None,
        *,
        dedupe_key: str | None = None,
    ) -> None: ...
    async def send_notification_command(
        self,
        *,
        booking_uid: str,
        trigger_event: str,
        recipients: list[dict[str, str]],
        template_data: dict[str, Any],
        dedupe_key: str | None = None,
    ) -> None: ...
