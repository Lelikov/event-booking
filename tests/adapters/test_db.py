"""Tests for BookingDatabaseAdapter."""

from event_booking.adapters.db import BookingDatabaseAdapter


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
            "telegram_chat_id": 12345,
            "client_name": "Client",
            "client_email": "client@test.com",
            "client_time_zone": "Europe/Kiev",
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
