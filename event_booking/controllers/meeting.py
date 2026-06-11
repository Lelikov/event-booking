"""Meeting controller: Jitsi JWT generation and URL shortening.

Each participant (organizer, client) gets their OWN tokenized meeting URL:
the JWT identifies the participant and grants moderator rights to the
organizer only. Organizer URLs must never be delivered to clients.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import structlog
from event_schemas.types import EventType

from event_booking.dtos import BookingDTO
from event_booking.interfaces.chat import IChatClient
from event_booking.interfaces.events import IEventPublisher
from event_booking.interfaces.shortener import IUrlShortener

logger = structlog.get_logger(__name__)

BUFFER_MINUTES = 5
ORGANIZER_ROLE = "organizer"
CLIENT_ROLE = "client"


class MeetingController:
    def __init__(  # noqa: PLR0913
        self,
        *,
        shortener: IUrlShortener,
        chat_client: IChatClient,
        events: IEventPublisher,
        jitsi_jwt_secret: str,
        jitsi_jwt_aud: str,
        jitsi_jwt_iss: str,
        jitsi_jwt_sub: str,
        meeting_host_url: str,
    ) -> None:
        self._shortener = shortener
        self._chat_client = chat_client
        self._events = events
        self._jitsi_jwt_secret = jitsi_jwt_secret
        self._jitsi_jwt_aud = jitsi_jwt_aud
        self._jitsi_jwt_iss = jitsi_jwt_iss
        self._jitsi_jwt_sub = jitsi_jwt_sub
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
            "sub": self._jitsi_jwt_sub,
            "room": booking.uid,
            "iat": int(now.timestamp()),
            "nbf": int(self._get_not_before(booking.start_time)),
            "exp": int(self._get_expiration(booking.end_time)),
            "context": {
                "user": {
                    "name": participant_name,
                    "role": role,
                    # Jitsi-recognized claim: only the organizer moderates the room.
                    "moderator": role == ORGANIZER_ROLE,
                    "email": participant_email,
                },
            },
        }
        return jwt.encode(payload, self._jitsi_jwt_secret, algorithm="HS256")

    async def _generate_long_url(
        self,
        booking: BookingDTO,
        participant_name: str,
        participant_email: str,
        role: str,
    ) -> str:
        jwt_video = self._create_jitsi_token(booking, participant_name, participant_email, role)
        expires_at = int(self._get_expiration(booking.end_time))
        jwt_chat = await self._chat_client.create_token(
            user_id=participant_email,
            name=participant_name,
            expires_at=expires_at,
        )
        return f"{self._meeting_host_url}/{booking.uid}?jwt_video={jwt_video}&jwt_chat={jwt_chat}"

    async def _resolve_short_url(
        self,
        *,
        long_url: str,
        expires_at: float,
        not_before: float,
        external_id: str,
        old_external_id: str | None,
    ) -> str | None:
        if old_external_id is None:
            return await self._shortener.create_url(long_url, expires_at, not_before, external_id)

        updated = await self._shortener.update_url_data(
            long_url=long_url,
            expires_at=expires_at,
            not_before=not_before,
            new_external_id=external_id,
            old_external_id=old_external_id,
        )
        if updated is not None:
            return updated
        # The old short URL no longer exists (or was never created) — fall back to creating a fresh one.
        return await self._shortener.create_url(long_url, expires_at, not_before, external_id)

    async def create_meeting_url(  # noqa: PLR0913
        self,
        booking: BookingDTO,
        participant_name: str,
        participant_email: str,
        external_id_prefix: str = "",
        previous_booking_uid: str | None = None,
        replace_existing: bool = False,
        dedupe_key: str | None = None,
    ) -> str:
        """Create the participant's short meeting URL.

        - ``previous_booking_uid``: reschedule — move the old booking's short URL
          onto the new uid (old links keep working and point at the new room).
        - ``replace_existing``: reassign — regenerate tokens in place under the
          same external id.
        """
        role = CLIENT_ROLE if external_id_prefix else ORGANIZER_ROLE
        long_url = await self._generate_long_url(booking, participant_name, participant_email, role)

        not_before = self._get_not_before(booking.start_time)
        expires_at = self._get_expiration(booking.end_time)
        external_id = f"{external_id_prefix}{booking.uid}"

        old_external_id: str | None = None
        if previous_booking_uid:
            old_external_id = f"{external_id_prefix}{previous_booking_uid}"
        if replace_existing:
            old_external_id = external_id

        short_url = await self._resolve_short_url(
            long_url=long_url,
            expires_at=expires_at,
            not_before=not_before,
            external_id=external_id,
            old_external_id=old_external_id,
        )

        # Canonical MeetingUrlCreatedPayload: {email, recipient_role, meeting_url}
        await self._events.send_event(
            booking_uid=booking.uid,
            event=EventType.MEETING_URL_CREATED,
            data={
                "email": participant_email,
                "recipient_role": role,
                "meeting_url": short_url or long_url,
            },
            dedupe_key=dedupe_key,
        )

        return short_url or long_url

    async def delete_meeting_url(
        self,
        booking: BookingDTO,
        external_id_prefix: str = "",
        dedupe_key: str | None = None,
    ) -> None:
        external_id = f"{external_id_prefix}{booking.uid}"
        await self._shortener.delete_url(external_id=external_id)

        role = CLIENT_ROLE if external_id_prefix else ORGANIZER_ROLE
        participant = booking.client if external_id_prefix else booking.user
        if not participant:
            logger.warning(
                "meeting.url_deleted not published: participant missing",
                booking_uid=booking.uid,
                recipient_role=role,
            )
            return

        # Canonical MeetingUrlDeletedPayload: {email, recipient_role}
        await self._events.send_event(
            booking_uid=booking.uid,
            event=EventType.MEETING_URL_DELETED,
            data={"email": participant.email, "recipient_role": role},
            dedupe_key=dedupe_key,
        )
