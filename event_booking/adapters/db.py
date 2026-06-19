"""Database adapter for Cal.com PostgreSQL via raw SQL.

cal.com stores timestamps as ``timestamp(3) without time zone`` (naive UTC).
This adapter is the timezone boundary: every datetime leaving it is aware UTC,
every datetime bound as a query parameter is converted back to naive UTC.

cal.com owns its rows: bookings rejected by constraints are marked
``status='rejected'`` (cal.com's own cancellation semantics) — never deleted.
"""

from datetime import UTC, datetime

from sqlalchemy.engine import RowMapping

from event_booking.dtos import AttendeeBookingDTO, BookingClientDTO, BookingDTO, UserDTO
from event_booking.interfaces.sql import ISqlExecutor

_GET_BOOKING_SQL = """
    SELECT DISTINCT ON (b.id)
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
        u.locale       AS user_locale,
        u."tgChatId"   AS telegram_chat_id,
        a.name         AS client_name,
        a.email        AS client_email,
        a."timeZone"   AS client_time_zone,
        a.locale       AS client_locale,
        et.slug        AS event_type_slug
    FROM "Booking" b
    LEFT JOIN users u ON u.id = b."userId"
    LEFT JOIN "Attendee" a ON a."bookingId" = b.id
    LEFT JOIN "EventType" et ON et.id = b."eventTypeId"
    WHERE b.uid = :uid
    ORDER BY b.id, a.id ASC NULLS LAST
"""

_GET_BOOKINGS_SQL = """
    SELECT DISTINCT ON (b.id)
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
        u.locale       AS user_locale,
        u."tgChatId"   AS telegram_chat_id,
        a.name         AS client_name,
        a.email        AS client_email,
        a."timeZone"   AS client_time_zone,
        a.locale       AS client_locale,
        et.slug        AS event_type_slug
    FROM "Booking" b
    LEFT JOIN users u ON u.id = b."userId"
    LEFT JOIN "Attendee" a ON a."bookingId" = b.id
    LEFT JOIN "EventType" et ON et.id = b."eventTypeId"
    WHERE b.status = 'accepted'
      AND b."startTime" >= :start_time_from
      AND b."startTime" <= :start_time_to
      AND NOT (COALESCE(b.metadata, '{}'::jsonb) ? :reminder_marker_key)
    ORDER BY b.id, a.id ASC NULLS LAST
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
      AND b.id != :exclude_booking_id
"""

_UPDATE_VIDEO_URL_SQL = """
    UPDATE "Booking"
    SET metadata = COALESCE(metadata, '{}'::jsonb)
        || jsonb_build_object('videoCallUrl', CAST(:url AS TEXT))
    WHERE uid = :uid
"""

_MARK_REMINDER_SENT_SQL = """
    UPDATE "Booking"
    SET metadata = COALESCE(metadata, '{}'::jsonb)
        || jsonb_build_object(CAST(:reminder_marker_key AS TEXT), CAST(:sent_at AS TEXT))
    WHERE uid = :uid
"""

_REJECT_BOOKING_SQL = """
    UPDATE "Booking"
    SET status = 'rejected',
        "rejectionReason" = :reason
    WHERE id = :booking_id
"""

_SUPPRESS_SYNC_SQL = "SET LOCAL app.sync_suppress = 'on'"

_UPDATE_ATTENDEE_EMAIL_SQL = """
UPDATE "Attendee"
SET email = :new_email
WHERE "bookingId" = (SELECT id FROM "Booking" WHERE uid = :booking_uid)
  AND lower(email) = lower(:old_email)
"""

REMINDER_MARKER_KEY = "bookingReminderSentAt"


def _as_utc(value: datetime) -> datetime:
    """cal.com stores naive UTC; attach tzinfo so the rest of the app is aware-only."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _as_naive_utc(value: datetime) -> datetime:
    """Bindable parameter for cal.com 'timestamp without time zone' columns."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


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
                locale=row.get("user_locale"),
            )

        client: BookingClientDTO | None = None
        if row.get("client_name") is not None:
            client = BookingClientDTO(
                name=row["client_name"],
                email=row.get("client_email", ""),
                time_zone=row.get("client_time_zone", ""),
                locale=row.get("client_locale"),
            )

        return BookingDTO(
            id=row["booking_id"],
            uid=row["uid"],
            title=row["title"],
            status=row["status"],
            start_time=_as_utc(row["start_time"]),
            end_time=_as_utc(row["end_time"]),
            created_at=_as_utc(row["created_at"]),
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
            {
                "start_time_from": _as_naive_utc(start_time_from),
                "start_time_to": _as_naive_utc(start_time_to),
                "reminder_marker_key": REMINDER_MARKER_KEY,
            },
        )
        return [self._fill_booking_dto(row) for row in rows]

    async def get_attendee_bookings_by_email(self, *, email: str, exclude_booking_id: int) -> list[AttendeeBookingDTO]:
        normalized = self._normalize_email(email)
        rows = await self._executor.fetch_all(
            _GET_ATTENDEE_BOOKINGS_SQL,
            {"normalized_email": normalized, "exclude_booking_id": exclude_booking_id},
        )
        return [
            AttendeeBookingDTO(
                booking_id=row["booking_id"],
                booking_uid=row["booking_uid"],
                name=row["name"],
                email=row["email"],
                start_time=_as_utc(row["start_time"]),
                end_time=_as_utc(row["end_time"]),
                status=row["status"],
            )
            for row in rows
        ]

    async def update_booking_video_url(self, booking_uid: str, url: str) -> None:
        await self._executor.execute(_UPDATE_VIDEO_URL_SQL, {"uid": booking_uid, "url": url})

    async def mark_reminder_sent(self, booking_uid: str, sent_at: datetime) -> None:
        await self._executor.execute(
            _MARK_REMINDER_SENT_SQL,
            {
                "uid": booking_uid,
                "reminder_marker_key": REMINDER_MARKER_KEY,
                "sent_at": sent_at.astimezone(UTC).isoformat(),
            },
        )

    async def reject_booking(self, *, booking_id: int, reason: str) -> None:
        """Mark a booking rejected in cal.com (cal.com owns its rows — never DELETE)."""
        await self._executor.execute(_REJECT_BOOKING_SQL, {"booking_id": booking_id, "reason": reason})

    async def update_attendee_email(self, booking_uid: str, old_email: str, new_email: str) -> None:
        await self._executor.execute_in_transaction(
            [
                (_SUPPRESS_SYNC_SQL, {}),
                (
                    _UPDATE_ATTENDEE_EMAIL_SQL,
                    {"booking_uid": booking_uid, "old_email": old_email, "new_email": new_email},
                ),
            ]
        )
