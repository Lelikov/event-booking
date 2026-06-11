"""Tests for the booking constraints analyzer."""

from datetime import UTC, datetime, timedelta

from event_booking.controllers.constraints import analyze_on_create
from event_booking.dtos import AttendeeBookingDTO, BookingDTO


def _make_booking(start_time: datetime | None = None) -> BookingDTO:
    now = start_time or datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    return BookingDTO(
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        end_time=now + timedelta(hours=1),
        id=1,
        start_time=now,
        status="accepted",
        title="Test",
        uid="test-uid",
    )


def _make_attendee_booking(start_time: datetime, status: str = "accepted", booking_id: int = 2) -> AttendeeBookingDTO:
    return AttendeeBookingDTO(
        booking_id=booking_id,
        booking_uid="att-uid",
        name="Client",
        email="client@test.com",
        start_time=start_time,
        end_time=start_time + timedelta(hours=1),
        status=status,
    )


def test_allowed_when_no_history():
    booking = _make_booking()
    result = analyze_on_create(booking, [])
    assert result.is_allowed is True


def test_rejects_when_min_interval_violated():
    # booking_start in the past; previous booking 2 days before it — both past, no future-active trigger
    booking_start = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    booking = _make_booking(start_time=booking_start)
    previous_start = booking_start - timedelta(days=2)
    attendee_bookings = [_make_attendee_booking(previous_start)]

    result = analyze_on_create(booking, attendee_bookings)

    assert result.is_allowed is False
    assert result.rejection_type == "min_interval"


def test_rejects_when_monthly_limit_exceeded():
    # Both existing bookings and the new booking are in April 2026 (past), so no future-active check triggers
    booking_start = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    booking = _make_booking(start_time=booking_start)
    past_april_1 = datetime(2026, 4, 1, tzinfo=UTC)
    past_april_2 = datetime(2026, 4, 2, tzinfo=UTC)
    attendee_bookings = [
        _make_attendee_booking(past_april_1),
        _make_attendee_booking(past_april_2),
    ]

    result = analyze_on_create(booking, attendee_bookings)

    assert result.is_allowed is False
    assert result.rejection_type == "month_limit"


def test_rejects_when_active_booking_exists():
    booking_start = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    booking = _make_booking(start_time=booking_start)
    # Future active booking
    future_start = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    attendee_bookings = [_make_attendee_booking(future_start, status="accepted")]

    result = analyze_on_create(booking, attendee_bookings)

    assert result.is_allowed is False
    assert result.has_active_booking is True
    assert result.active_booking_start == future_start


def test_ignores_cancelled_bookings():
    booking_start = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    booking = _make_booking(start_time=booking_start)
    # Cancelled booking within 7-day interval — must be ignored
    recent_start = booking_start - timedelta(days=2)
    attendee_bookings = [_make_attendee_booking(recent_start, status="cancelled")]

    result = analyze_on_create(booking, attendee_bookings)

    assert result.is_allowed is True


def test_excludes_the_booking_being_validated_from_history():
    """The new booking already exists in cal.com's DB — it must not be counted against itself."""
    booking_start = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    booking = _make_booking(start_time=booking_start)
    # Same booking id as the one under validation (future + accepted: would trigger active-booking rejection)
    attendee_bookings = [_make_attendee_booking(booking_start, booking_id=booking.id)]

    result = analyze_on_create(booking, attendee_bookings)

    assert result.is_allowed is True


def test_pending_booking_counts_as_active():
    booking_start = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    booking = _make_booking(start_time=booking_start)
    future_start = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    attendee_bookings = [_make_attendee_booking(future_start, status="pending")]

    result = analyze_on_create(booking, attendee_bookings)

    assert result.is_allowed is False
    assert result.has_active_booking is True


def test_rescheduled_is_not_a_valid_status():
    """'rescheduled' is not a cal.com BookingStatus and must not be treated as active."""
    booking_start = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    booking = _make_booking(start_time=booking_start)
    future_start = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    attendee_bookings = [_make_attendee_booking(future_start, status="rescheduled")]

    result = analyze_on_create(booking, attendee_bookings)

    assert result.is_allowed is True


def test_handles_naive_datetimes_from_calcom():
    """cal.com timestamp(3) columns are naive UTC — the analyzer must not crash on mixed awareness."""
    booking_start = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    booking = _make_booking(start_time=booking_start)
    naive_future = datetime(2026, 7, 1, 10, 0)  # noqa: DTZ001
    attendee_bookings = [_make_attendee_booking(naive_future)]

    result = analyze_on_create(booking, attendee_bookings)

    assert result.is_allowed is False
    assert result.has_active_booking is True
    assert result.active_booking_start == naive_future.replace(tzinfo=UTC)
