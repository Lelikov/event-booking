"""Meeting controller: Jitsi JWT generation and URL shortening."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import structlog
from event_schemas.types import EventType

from event_booking.dtos import BookingDTO
from event_booking.interfaces.chat import IChatClient
from event_booking.interfaces.db import IBookingDatabaseAdapter
from event_booking.interfaces.events import IEventPublisher
from event_booking.interfaces.shortener import IUrlShortener

logger = structlog.get_logger(__name__)

BUFFER_MINUTES = 5


class MeetingController:
    def __init__(  # noqa: PLR0913
        self,
        *,
        shortener: IUrlShortener,
        chat_client: IChatClient,
        db: IBookingDatabaseAdapter,
        events: IEventPublisher,
        jitsi_jwt_secret: str,
        jitsi_jwt_aud: str,
        jitsi_jwt_iss: str,
        meeting_host_url: str,
    ) -> None:
        self._shortener = shortener
        self._chat_client = chat_client
        self._db = db
        self._events = events
        self._jitsi_jwt_secret = jitsi_jwt_secret
        self._jitsi_jwt_aud = jitsi_jwt_aud
        self._jitsi_jwt_iss = jitsi_jwt_iss
        self._meeting_host_url = meeting_host_url.rstrip("/")

    def _get_not_before(self, start_time: datetime) -> float:
        return (start_time - timedelta(minutes=BUFFER_MINUTES)).timestamp()

    def _get_expiration(self, end_time: datetime) -> float:
        return (end_time + timedelta(minutes=BUFFER_MINUTES)).timestamp()

    def _create_jitsi_token(
        self,
        booking: BookingDTO,
        participant_name: str,
        participant_email: str,
        role: str,
    ) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "aud": self._jitsi_jwt_aud,
            "iss": self._jitsi_jwt_iss,
            "sub": "*",
            "room": booking.uid,
            "iat": int(now.timestamp()),
            "nbf": int(self._get_not_before(booking.start_time)),
            "exp": int(self._get_expiration(booking.end_time)),
            "context": {
                "user": {
                    "name": participant_name,
                    "role": role,
                    "email": participant_email,
                },
            },
        }
        return jwt.encode(payload, self._jitsi_jwt_secret, algorithm="HS256")

    def _generate_long_url(
        self,
        booking: BookingDTO,
        participant_name: str,
        participant_email: str,
        role: str,
    ) -> str:
        jwt_video = self._create_jitsi_token(booking, participant_name, participant_email, role)
        expires_at = int(self._get_expiration(booking.end_time))
        jwt_chat = self._chat_client.create_token(
            user_id=participant_email,
            name=participant_name,
            expires_at=expires_at,
        )
        return f"{self._meeting_host_url}/{booking.uid}?jwt_video={jwt_video}&jwt_chat={jwt_chat}"

    async def create_meeting_url(  # noqa: PLR0913
        self,
        booking: BookingDTO,
        participant_id: str,
        participant_name: str,
        participant_email: str,
        is_update_url_data: bool = False,
        external_id_prefix: str = "",
    ) -> str:
        role = "client" if external_id_prefix else "organizer"
        long_url = self._generate_long_url(booking, participant_name, participant_email, role)

        not_before = self._get_not_before(booking.start_time)
        expires_at = self._get_expiration(booking.end_time)
        external_id = f"{external_id_prefix}{booking.uid}" if external_id_prefix else booking.uid

        short_url: str | None = None
        if is_update_url_data and booking.from_reschedule:
            old_external_id = (
                f"{external_id_prefix}{booking.from_reschedule}" if external_id_prefix else booking.from_reschedule
            )
            short_url = await self._shortener.update_url_data(
                long_url=long_url,
                expires_at=expires_at,
                not_before=not_before,
                new_external_id=external_id,
                old_external_id=old_external_id,
            )
        else:
            short_url = await self._shortener.create_url(long_url, expires_at, not_before, external_id)

        await self._events.send_event(
            booking_uid=booking.uid,
            event=EventType.MEETING_URL_CREATED,
            data={
                "participant_id": participant_id,
                "url": short_url or long_url,
            },
        )

        return short_url or long_url

    async def delete_meeting_url(self, booking: BookingDTO, external_id_prefix: str = "") -> None:
        external_id = f"{external_id_prefix}{booking.uid}" if external_id_prefix else booking.uid
        await self._shortener.delete_url(external_id=external_id)
        await self._events.send_event(
            booking_uid=booking.uid,
            event=EventType.MEETING_URL_DELETED,
            data={"external_id": external_id},
        )
