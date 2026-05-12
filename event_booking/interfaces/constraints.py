"""Booking constraints analyzer protocol."""

from typing import Protocol

from event_booking.dtos import AttendeeBookingDTO, BookingDTO, ConstraintsResult


class IBookingConstraintsAnalyzer(Protocol):
    def analyze_on_create(
        self, *, booking: BookingDTO, attendee_bookings: list[AttendeeBookingDTO]
    ) -> ConstraintsResult: ...
