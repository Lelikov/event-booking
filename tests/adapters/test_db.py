"""Tests for BookingDatabaseAdapter."""

from datetime import UTC, datetime

from event_booking.adapters.db import (
    _GET_BOOKING_SQL,
    _GET_BOOKINGS_SQL,
    REMINDER_MARKER_KEY,
    BookingDatabaseAdapter,
    _as_naive_utc,
    _as_utc,
)


class FakeExecutor:
    """Records executed queries/params; returns canned rows."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, dict]] = []

    async def fetch_one(self, query: str, values: dict) -> dict | None:
        self.calls.append((query, values))
        return self.rows[0] if self.rows else None

    async def fetch_all(self, query: str, values: dict) -> list[dict]:
        self.calls.append((query, values))
        return self.rows

    async def execute(self, query: str, values: dict) -> None:
        self.calls.append((query, values))


class TestNormalizeEmail:
    def test_strips_and_lowercases(self) -> None:
        assert BookingDatabaseAdapter._normalize_email("  User@Example.COM  ") == "user@example.com"

    def test_removes_plus_alias(self) -> None:
        assert BookingDatabaseAdapter._normalize_email("user+tag@example.com") == "user@example.com"

    def test_no_alias(self) -> None:
        assert BookingDatabaseAdapter._normalize_email("user@example.com") == "user@example.com"


class TestFillBookingDto:
    def test_fills_all_fields(self) -> None:
        from datetime import UTC, datetime

        row = {
            "booking_id": 1,
            "uid": "abc-123",
            "title": "Test Booking",
            "status": "accepted",
            "start_time": datetime(2026, 6, 15, 10, 0, tzinfo=UTC),
            "end_time": datetime(2026, 6, 15, 11, 0, tzinfo=UTC),
            "created_at": datetime(2026, 6, 1, tzinfo=UTC),
            "metadata": None,
            "from_reschedule": None,
            "user_id": 42,
            "user_name": "Organizer",
            "user_email": "org@test.com",
            "user_locked": False,
            "user_time_zone": "Europe/Moscow",
            "user_locale": "ru",
            "telegram_chat_id": 12345,
            "client_name": "Client",
            "client_email": "client@test.com",
            "client_time_zone": "Europe/Kiev",
            "client_locale": "en",
            "event_type_slug": "consultation",
        }

        class FakeRow(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        booking = BookingDatabaseAdapter._fill_booking_dto(FakeRow(row))
        assert booking.uid == "abc-123"
        assert booking.user is not None
        assert booking.user.name == "Organizer"
        assert booking.client is not None
        assert booking.client.email == "client@test.com"
        assert booking.user.locale == "ru"
        assert booking.client.locale == "en"

    def test_no_user_no_client(self) -> None:
        from datetime import UTC, datetime

        row = {
            "booking_id": 1,
            "uid": "abc-123",
            "title": "Test",
            "status": "accepted",
            "start_time": datetime(2026, 6, 15, 10, 0, tzinfo=UTC),
            "end_time": datetime(2026, 6, 15, 11, 0, tzinfo=UTC),
            "created_at": datetime(2026, 6, 1, tzinfo=UTC),
            "metadata": None,
            "from_reschedule": None,
            "user_id": None,
            "client_name": None,
        }

        class FakeRow(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        booking = BookingDatabaseAdapter._fill_booking_dto(FakeRow(row))
        assert booking.user is None
        assert booking.client is None


def _booking_row(**overrides) -> dict:
    row = {
        "booking_id": 7,
        "uid": "uid-7",
        "title": "T",
        "status": "accepted",
        "start_time": datetime(2026, 6, 15, 10, 0),  # noqa: DTZ001 — cal.com naive UTC
        "end_time": datetime(2026, 6, 15, 11, 0),  # noqa: DTZ001
        "created_at": datetime(2026, 6, 1, 0, 0),  # noqa: DTZ001
        "metadata": None,
        "from_reschedule": None,
        "user_id": None,
        "client_name": None,
    }
    row.update(overrides)
    return row


class TestTimezoneBoundary:
    def test_as_utc_attaches_utc_to_naive(self) -> None:
        naive = datetime(2026, 6, 15, 10, 0)  # noqa: DTZ001
        assert _as_utc(naive) == datetime(2026, 6, 15, 10, 0, tzinfo=UTC)

    def test_as_naive_utc_strips_tzinfo(self) -> None:
        aware = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
        result = _as_naive_utc(aware)
        assert result.tzinfo is None
        assert result == datetime(2026, 6, 15, 10, 0)  # noqa: DTZ001

    async def test_get_booking_returns_aware_utc(self) -> None:
        executor = FakeExecutor(rows=[_booking_row()])
        adapter = BookingDatabaseAdapter(executor)

        booking = await adapter.get_booking("uid-7")

        assert booking is not None
        assert booking.start_time.tzinfo is UTC
        assert booking.end_time.tzinfo is UTC
        assert booking.created_at.tzinfo is UTC

    async def test_get_bookings_binds_naive_utc_params(self) -> None:
        executor = FakeExecutor()
        adapter = BookingDatabaseAdapter(executor)

        await adapter.get_bookings(
            start_time_from=datetime(2026, 6, 15, 10, 0, tzinfo=UTC),
            start_time_to=datetime(2026, 6, 15, 11, 0, tzinfo=UTC),
        )

        _, params = executor.calls[0]
        assert params["start_time_from"].tzinfo is None
        assert params["start_time_to"].tzinfo is None

    async def test_get_attendee_bookings_returns_aware_utc(self) -> None:
        row = {
            "booking_id": 2,
            "booking_uid": "uid-2",
            "name": "C",
            "email": "c@test.com",
            "start_time": datetime(2026, 6, 15, 10, 0),  # noqa: DTZ001
            "end_time": datetime(2026, 6, 15, 11, 0),  # noqa: DTZ001
            "status": "accepted",
        }
        executor = FakeExecutor(rows=[row])
        adapter = BookingDatabaseAdapter(executor)

        result = await adapter.get_attendee_bookings_by_email(email="c@test.com", exclude_booking_id=7)

        assert result[0].start_time.tzinfo is UTC


class TestAttendeeHistoryExclusion:
    async def test_excludes_current_booking_id(self) -> None:
        executor = FakeExecutor()
        adapter = BookingDatabaseAdapter(executor)

        await adapter.get_attendee_bookings_by_email(email="c@test.com", exclude_booking_id=42)

        query, params = executor.calls[0]
        assert params["exclude_booking_id"] == 42  # noqa: PLR2004
        assert "b.id != :exclude_booking_id" in query


class TestCalcomRowsAreNeverDeleted:
    def test_no_delete_statements_in_module(self) -> None:
        import event_booking.adapters.db as db_module

        sql_constants = [v for k, v in vars(db_module).items() if k.endswith("_SQL")]
        assert sql_constants
        for sql in sql_constants:
            assert "DELETE" not in sql.upper()

    async def test_reject_booking_updates_status(self) -> None:
        executor = FakeExecutor()
        adapter = BookingDatabaseAdapter(executor)

        await adapter.reject_booking(booking_id=7, reason="Monthly limit exceeded")

        query, params = executor.calls[0]
        assert "status = 'rejected'" in query
        assert params == {"booking_id": 7, "reason": "Monthly limit exceeded"}


class TestMultiAttendeeDeterminism:
    def test_booking_queries_use_distinct_on_with_attendee_order(self) -> None:
        for sql in (_GET_BOOKING_SQL, _GET_BOOKINGS_SQL):
            assert "DISTINCT ON (b.id)" in sql
            assert "ORDER BY b.id, a.id ASC" in sql


class TestReminderMarker:
    async def test_mark_reminder_sent_writes_metadata_key(self) -> None:
        executor = FakeExecutor()
        adapter = BookingDatabaseAdapter(executor)

        await adapter.mark_reminder_sent("uid-7", sent_at=datetime(2026, 6, 15, 9, 0, tzinfo=UTC))

        query, params = executor.calls[0]
        assert "metadata" in query
        assert params["reminder_marker_key"] == REMINDER_MARKER_KEY
        assert params["uid"] == "uid-7"

    def test_get_bookings_excludes_already_reminded(self) -> None:
        assert REMINDER_MARKER_KEY == "bookingReminderSentAt"
        assert "NOT (COALESCE(b.metadata, '{}'::jsonb) ? :reminder_marker_key)" in _GET_BOOKINGS_SQL


class TestJsonbBuildObjectParamsAreCast:
    def test_all_bind_params_inside_jsonb_build_object_have_explicit_casts(self) -> None:
        # Regression: asyncpg cannot infer the type of a bare bind param inside
        # jsonb_build_object() — UPDATE "Booking" metadata failed at runtime
        # with IndeterminateDatatypeError ("could not determine data type of
        # parameter $1"). Every param there must be wrapped in CAST(... AS TEXT).
        import re

        import event_booking.adapters.db as db_module

        sql_constants = {k: v for k, v in vars(db_module).items() if k.endswith("_SQL")}
        assert sql_constants
        for name, sql in sql_constants.items():
            for call in re.findall(r"jsonb_build_object\(([^)]*)\)", sql):
                for param in re.findall(r"(?<!CAST\():\w+", call):
                    raise AssertionError(f"{name}: uncast bind param {param} inside jsonb_build_object")
