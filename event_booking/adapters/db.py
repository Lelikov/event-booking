"""Database adapter for Cal.com PostgreSQL via raw SQL."""

from datetime import datetime

from sqlalchemy.engine import RowMapping

from event_booking.dtos import AttendeeBookingDTO, BookingClientDTO, BookingDTO, UserDTO
from event_booking.interfaces.sql import ISqlExecutor

_GET_BOOKING_SQL = """
    SELECT
        b.id           AS booking_id,
        b.uid          AS uid,
        b.title        AS title,
        b.status       AS status,
        b."startTime"  AS start_time,
        b."endTime"    AS end_time,
        b."createdAt"  AS created_at,
        b.metadata     AS metadata,
        b."fromReschedule" AS from_reschedule,
        u.id           AS user_id,
        u.name         AS user_name,
        u.email        AS user_email,
        u.locked       AS user_locked,
        u."timeZone"   AS user_time_zone,
        u."tgChatId"   AS telegram_chat_id,
        a.name         AS client_name,
        a.email        AS client_email,
        a."timeZone"   AS client_time_zone,
        et.slug        AS event_type_slug
    FROM "Booking" b
    LEFT JOIN users u ON u.id = b."userId"
    LEFT JOIN "Attendee" a ON a."bookingId" = b.id
    LEFT JOIN "EventType" et ON et.id = b."eventTypeId"
    WHERE b.uid = :uid
"""

_GET_BOOKINGS_SQL = """
    SELECT
        b.id           AS booking_id,
        b.uid          AS uid,
        b.title        AS title,
        b.status       AS status,
        b."startTime"  AS start_time,
        b."endTime"    AS end_time,
        b."createdAt"  AS created_at,
        b.metadata     AS metadata,
        b."fromReschedule" AS from_reschedule,
        u.id           AS user_id,
        u.name         AS user_name,
        u.email        AS user_email,
        u.locked       AS user_locked,
        u."timeZone"   AS user_time_zone,
        u."tgChatId"   AS telegram_chat_id,
        a.name         AS client_name,
        a.email        AS client_email,
        a."timeZone"   AS client_time_zone,
        et.slug        AS event_type_slug
    FROM "Booking" b
    LEFT JOIN users u ON u.id = b."userId"
    LEFT JOIN "Attendee" a ON a."bookingId" = b.id
    LEFT JOIN "EventType" et ON et.id = b."eventTypeId"
    WHERE b.status = 'accepted'
      AND b."startTime" >= :start_time_from
      AND b."startTime" <= :start_time_to
"""

_GET_ATTENDEE_BOOKINGS_SQL = """
    SELECT
        b.id           AS booking_id,
        b.uid          AS booking_uid,
        a.name         AS name,
        a.email        AS email,
        b."startTime"  AS start_time,
        b."endTime"    AS end_time,
        b.status       AS status
    FROM "Attendee" a
    JOIN "Booking" b ON b.id = a."bookingId"
    WHERE lower(regexp_replace(a.email, '\\+[^@]+', '')) = :normalized_email
"""

_GET_USER_BY_EMAIL_SQL = """
    SELECT id, name, email, locked, "timeZone", "tgChatId" AS telegram_chat_id
    FROM users
    WHERE email = :email
"""

_GET_ORGANIZER_CHAT_ID_SQL = """
    SELECT "tgChatId" AS telegram_chat_id
    FROM users
    WHERE email = :email
      AND locked = FALSE
      AND "tgChatId" IS NOT NULL
"""

_UPDATE_VIDEO_URL_SQL = """
    UPDATE "Booking"
    SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('videoCallUrl', :url)
    WHERE uid = :uid
"""

_DELETE_ATTENDEE_SQL = """
    DELETE FROM "Attendee"
    WHERE "bookingId" = :booking_id
"""

_DELETE_BOOKING_SQL = """
    DELETE FROM "Booking"
    WHERE id = :booking_id
"""


class BookingDatabaseAdapter:
    def __init__(self, executor: ISqlExecutor) -> None:
        self._executor = executor

    @staticmethod
    def _normalize_email(email: str) -> str:
        email = email.strip().lower()
        local, _, domain = email.partition("@")
        local = local.partition("+")[0]
        return f"{local}@{domain}"

    @staticmethod
    def _fill_booking_dto(row: RowMapping) -> BookingDTO:
        user: UserDTO | None = None
        if row.get("user_id") is not None:
            user = UserDTO(
                id=row["user_id"],
                name=row.get("user_name", ""),
                email=row.get("user_email", ""),
                locked=row.get("user_locked", False),
                time_zone=row.get("user_time_zone", ""),
                telegram_chat_id=row.get("telegram_chat_id"),
            )

        client: BookingClientDTO | None = None
        if row.get("client_name") is not None:
            client = BookingClientDTO(
                name=row["client_name"],
                email=row.get("client_email", ""),
                time_zone=row.get("client_time_zone", ""),
            )

        return BookingDTO(
            id=row["booking_id"],
            uid=row["uid"],
            title=row["title"],
            status=row["status"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            created_at=row["created_at"],
            metadata=row.get("metadata"),
            from_reschedule=row.get("from_reschedule"),
            event_type_slug=row.get("event_type_slug"),
            user=user,
            client=client,
        )

    async def get_booking(self, booking_uid: str) -> BookingDTO | None:
        row = await self._executor.fetch_one(_GET_BOOKING_SQL, {"uid": booking_uid})
        if row is None:
            return None
        return self._fill_booking_dto(row)

    async def get_bookings(self, start_time_from: datetime, start_time_to: datetime) -> list[BookingDTO]:
        rows = await self._executor.fetch_all(
            _GET_BOOKINGS_SQL,
            {"start_time_from": start_time_from, "start_time_to": start_time_to},
        )
        return [self._fill_booking_dto(row) for row in rows]

    async def get_attendee_bookings_by_email(self, *, email: str) -> list[AttendeeBookingDTO]:
        normalized = self._normalize_email(email)
        rows = await self._executor.fetch_all(
            _GET_ATTENDEE_BOOKINGS_SQL,
            {"normalized_email": normalized},
        )
        return [
            AttendeeBookingDTO(
                booking_id=row["booking_id"],
                booking_uid=row["booking_uid"],
                name=row["name"],
                email=row["email"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                status=row["status"],
            )
            for row in rows
        ]

    async def get_user_by_email(self, email: str) -> UserDTO | None:
        row = await self._executor.fetch_one(_GET_USER_BY_EMAIL_SQL, {"email": email})
        if row is None:
            return None
        return UserDTO(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            locked=row["locked"],
            time_zone=row["timeZone"],
            telegram_chat_id=row.get("telegram_chat_id"),
        )

    async def get_organizer_chat_id(self, email: str) -> int | None:
        row = await self._executor.fetch_one(_GET_ORGANIZER_CHAT_ID_SQL, {"email": email})
        if row is None:
            return None
        return row["telegram_chat_id"]

    async def update_booking_video_url(self, booking_uid: str, url: str) -> None:
        await self._executor.execute(_UPDATE_VIDEO_URL_SQL, {"uid": booking_uid, "url": url})

    async def delete_booking_and_attendee_by_booking_id(self, *, booking_id: int) -> None:
        await self._executor.execute_in_transaction(
            [
                (_DELETE_ATTENDEE_SQL, {"booking_id": booking_id}),
                (_DELETE_BOOKING_SQL, {"booking_id": booking_id}),
            ]
        )
