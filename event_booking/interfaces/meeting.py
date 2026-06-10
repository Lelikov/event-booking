"""Meeting controller protocol."""

from typing import Protocol

from event_booking.dtos import BookingDTO


class IMeetingController(Protocol):
    async def create_meeting_url(
        self,
        *,
        booking: BookingDTO,
        participant_name: str,
        participant_email: str,
        is_update_url_data: bool = False,
        external_id_prefix: str = "",
    ) -> str: ...
    async def delete_meeting_url(self, *, booking: BookingDTO, external_id_prefix: str = "") -> None: ...
