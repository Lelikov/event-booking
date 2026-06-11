"""Booking constraints analyzer: validates booking limits per client.

Semantics:
- Runs only on booking.created. cal.com reschedules mint a new uid and arrive
  as booking.rescheduled, which is intentionally NOT re-validated.
- The booking under validation already exists in cal.com's DB, so the history
  passed in must exclude it (the adapter excludes by booking id; this module
  also filters defensively).
- cal.com BookingStatus has no 'rescheduled' value; active = accepted,
  pending and awaiting_host (a pending future booking still blocks a new one).
"""

from datetime import UTC, datetime, timedelta

from event_booking.dtos import AttendeeBookingDTO, BookingDTO, ConstraintsResult

MIN_DAYS_BETWEEN_BOOKINGS = 7
MAX_BOOKINGS_PER_MONTH = 2
MAX_BOOKINGS_PER_YEAR = 10
ACTIVE_STATUSES = {"accepted", "pending", "awaiting_host"}


def _as_utc(value: datetime) -> datetime:
    """Defensive normalization: cal.com naive timestamps are UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def analyze_on_create(booking: BookingDTO, attendee_bookings: list[AttendeeBookingDTO]) -> ConstraintsResult:
    """Validate whether a new booking is allowed given the attendee's booking history."""
    history = [b for b in attendee_bookings if b.booking_id != booking.id]
    active = [b for b in history if b.status in ACTIVE_STATUSES]

    now = datetime.now(UTC)
    future_active = [b for b in active if _as_utc(b.start_time) > now]
    if future_active:
        return ConstraintsResult(
            is_allowed=False,
            has_active_booking=True,
            active_booking_start=_as_utc(future_active[0].start_time),
            rejection_reasons=["Active future booking already exists"],
        )

    booking_start = _as_utc(booking.start_time)
    same_month = [
        b
        for b in active
        if _as_utc(b.start_time).year == booking_start.year and _as_utc(b.start_time).month == booking_start.month
    ]
    if len(same_month) >= MAX_BOOKINGS_PER_MONTH:
        return ConstraintsResult(
            is_allowed=False,
            rejection_type="month_limit",
            rejection_reasons=[f"Monthly limit of {MAX_BOOKINGS_PER_MONTH} bookings exceeded"],
        )

    same_year = [b for b in active if _as_utc(b.start_time).year == booking_start.year]
    if len(same_year) >= MAX_BOOKINGS_PER_YEAR:
        return ConstraintsResult(
            is_allowed=False,
            rejection_type="year_limit",
            rejection_reasons=[f"Yearly limit of {MAX_BOOKINGS_PER_YEAR} bookings exceeded"],
        )

    if active:
        latest = max(active, key=lambda b: _as_utc(b.start_time))
        delta_days = (booking_start - _as_utc(latest.start_time)).days
        if delta_days < MIN_DAYS_BETWEEN_BOOKINGS:
            available_from = _as_utc(latest.start_time) + timedelta(days=MIN_DAYS_BETWEEN_BOOKINGS)
            return ConstraintsResult(
                is_allowed=False,
                rejection_type="min_interval",
                available_from=available_from,
                rejection_reasons=[f"Minimum interval of {MIN_DAYS_BETWEEN_BOOKINGS} days between bookings not met"],
            )

    return ConstraintsResult(is_allowed=True)
