"""Booking constraints analyzer: validates booking limits per client."""

from datetime import UTC, datetime, timedelta

from event_booking.dtos import AttendeeBookingDTO, BookingDTO, ConstraintsResult

MIN_DAYS_BETWEEN_BOOKINGS = 7
MAX_BOOKINGS_PER_MONTH = 2
MAX_BOOKINGS_PER_YEAR = 10
ACTIVE_STATUSES = {"accepted", "rescheduled"}


def analyze_on_create(booking: BookingDTO, attendee_bookings: list[AttendeeBookingDTO]) -> ConstraintsResult:
    """Validate whether a new booking is allowed given the attendee's booking history."""
    active = [b for b in attendee_bookings if b.status in ACTIVE_STATUSES]

    now = datetime.now(UTC)
    future_active = [b for b in active if b.start_time > now]
    if future_active:
        return ConstraintsResult(
            is_allowed=False,
            has_active_booking=True,
            active_booking_start=future_active[0].start_time,
            rejection_reasons=["Active future booking already exists"],
        )

    same_month = [
        b
        for b in active
        if b.start_time.year == booking.start_time.year and b.start_time.month == booking.start_time.month
    ]
    if len(same_month) >= MAX_BOOKINGS_PER_MONTH:
        return ConstraintsResult(
            is_allowed=False,
            rejection_type="month_limit",
            rejection_reasons=[f"Monthly limit of {MAX_BOOKINGS_PER_MONTH} bookings exceeded"],
        )

    same_year = [b for b in active if b.start_time.year == booking.start_time.year]
    if len(same_year) >= MAX_BOOKINGS_PER_YEAR:
        return ConstraintsResult(
            is_allowed=False,
            rejection_type="year_limit",
            rejection_reasons=[f"Yearly limit of {MAX_BOOKINGS_PER_YEAR} bookings exceeded"],
        )

    if active:
        latest = max(active, key=lambda b: b.start_time)
        delta_days = (booking.start_time - latest.start_time).days
        if delta_days < MIN_DAYS_BETWEEN_BOOKINGS:
            available_from = latest.start_time + timedelta(days=MIN_DAYS_BETWEEN_BOOKINGS)
            return ConstraintsResult(
                is_allowed=False,
                rejection_type="min_interval",
                available_from=available_from,
                rejection_reasons=[f"Minimum interval of {MIN_DAYS_BETWEEN_BOOKINGS} days between bookings not met"],
            )

    return ConstraintsResult(is_allowed=True)
