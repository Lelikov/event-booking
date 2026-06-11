"""Meeting controller protocol."""

from typing import Protocol

from event_booking.dtos import BookingDTO


class IMeetingController(Protocol):
    async def create_meeting_url(  # noqa: PLR0913
        self,
        *,
        booking: BookingDTO,
        participant_name: str,
        participant_email: str,
        external_id_prefix: str = "",
        previous_booking_uid: str | None = None,
        replace_existing: bool = False,
        dedupe_key: str | None = None,
    ) -> str: ...
    async def delete_meeting_url(
        self,
        *,
        booking: BookingDTO,
        external_id_prefix: str = "",
        dedupe_key: str | None = None,
    ) -> None: ...
