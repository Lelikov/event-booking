"""Frozen dataclasses for inter-layer data transfer."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UserDTO:
    id: int
    name: str
    email: str
    locked: bool
    time_zone: str
    telegram_chat_id: int | None = None


@dataclass(frozen=True, slots=True)
class BookingClientDTO:
    name: str
    email: str
    time_zone: str


@dataclass(slots=True)
class BookingDTO:
    """Mutable: previous_booking is set after initial construction."""

    created_at: datetime
    end_time: datetime
    id: int
    start_time: datetime
    status: str
    title: str
    uid: str
    user: UserDTO | None = None
    client: BookingClientDTO | None = None
    metadata: dict | None = None
    previous_booking: BookingDTO | None = None
    event_type_slug: str | None = None
    from_reschedule: str | None = None


@dataclass(frozen=True, slots=True)
class AttendeeBookingDTO:
    booking_id: int
    booking_uid: str
    name: str
    email: str
    start_time: datetime
    end_time: datetime
    status: str


@dataclass(frozen=True, slots=True)
class ConstraintsResult:
    is_allowed: bool
    available_from: datetime | None = None
    has_active_booking: bool = False
    active_booking_start: datetime | None = None
    rejection_reasons: list[str] = field(default_factory=list)
    rejection_type: str | None = None
