"""Test factories for building DTOs with sensible defaults."""

from datetime import UTC, datetime, timedelta

from event_booking.dtos import BookingClientDTO, BookingDTO, UserDTO


def make_user(  # noqa: PLR0913
    *,
    id: int = 1,  # noqa: A002
    name: str = "Organizer",
    email: str = "organizer@test.com",
    time_zone: str = "Europe/Moscow",
    telegram_chat_id: int = 12345,
    locale: str | None = None,
) -> UserDTO:
    return UserDTO(
        id=id,
        name=name,
        email=email,
        locked=False,
        time_zone=time_zone,
        telegram_chat_id=telegram_chat_id,
        locale=locale,
    )


def make_client(
    *,
    name: str = "Client",
    email: str = "client@test.com",
    time_zone: str = "Europe/Kiev",
    locale: str | None = None,
) -> BookingClientDTO:
    return BookingClientDTO(name=name, email=email, time_zone=time_zone, locale=locale)


def make_booking(  # noqa: PLR0913
    *,
    uid: str = "booking-uid-123",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    user: UserDTO | None = None,
    client: BookingClientDTO | None = None,
    status: str = "accepted",
    from_reschedule: str | None = None,
) -> BookingDTO:
    st = start_time or datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    et = end_time or (st + timedelta(hours=1))
    return BookingDTO(
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        end_time=et,
        id=1,
        start_time=st,
        status=status,
        title="Test Booking",
        uid=uid,
        user=user or make_user(),
        client=client or make_client(),
        from_reschedule=from_reschedule,
    )
