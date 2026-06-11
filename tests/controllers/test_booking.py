"""Tests for BookingController orchestrator."""

from unittest.mock import AsyncMock, MagicMock

from event_booking.controllers.booking import CLIENT_PREFIX, BookingController
from event_booking.dtos import ConstraintsResult
from tests.factories import make_booking


def make_controller(  # noqa: PLR0913
    *,
    mock_db: AsyncMock,
    mock_events: AsyncMock,
    mock_chat_controller: AsyncMock,
    mock_meeting_controller: AsyncMock,
    mock_constraints_analyzer: MagicMock,
    is_enable_constraints: bool = False,
) -> BookingController:
    return BookingController(
        db=mock_db,
        events=mock_events,
        chat_controller=mock_chat_controller,
        meeting_controller=mock_meeting_controller,
        constraints_analyzer=mock_constraints_analyzer,
        is_enable_constraints=is_enable_constraints,
    )


class TestHandleCreated:
    async def test_creates_chat_and_meeting_urls(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_created(booking.uid)

        mock_chat_controller.create_chat.assert_called_once_with(
            channel_id=booking.uid,
            organizer_id=booking.user.email,
            client_id=booking.client.email,
        )
        assert mock_chat_controller.send_message.call_count == 2  # noqa: PLR2004

        assert mock_meeting_controller.create_meeting_url.call_count == 2  # noqa: PLR2004
        calls = mock_meeting_controller.create_meeting_url.call_args_list
        prefixes = {c.kwargs["external_id_prefix"] for c in calls}
        assert "" in prefixes
        assert CLIENT_PREFIX in prefixes

        mock_events.send_notification_command.assert_called_once()
        notif_kwargs = mock_events.send_notification_command.call_args.kwargs
        assert notif_kwargs["trigger_event"] == "BOOKING_CREATED"
        assert notif_kwargs["booking_uid"] == booking.uid

    async def test_rejects_when_constraints_fail(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_db.get_attendee_bookings_by_email = AsyncMock(return_value=[])
        mock_constraints_analyzer.analyze_on_create = MagicMock(
            return_value=ConstraintsResult(
                is_allowed=False,
                rejection_reasons=["Active future booking already exists"],
            )
        )

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
            is_enable_constraints=True,
        )

        await controller.handle_created(booking.uid)

        mock_db.get_attendee_bookings_by_email.assert_called_once_with(
            email=booking.client.email,
            exclude_booking_id=booking.id,
        )
        mock_db.reject_booking.assert_called_once_with(
            booking_id=booking.id,
            reason="Active future booking already exists",
        )
        mock_events.send_notification_command.assert_called_once()
        notif_kwargs = mock_events.send_notification_command.call_args.kwargs
        assert notif_kwargs["trigger_event"] == "BOOKING_REJECTED"

        mock_events.send_event.assert_called_once()
        event_kwargs = mock_events.send_event.call_args.kwargs
        from event_schemas.types import EventType

        assert event_kwargs["event"] == EventType.BOOKING_REJECTED
        booking_client_email = booking.client.email
        assert event_kwargs["data"] == {
            "client_email": booking_client_email,
            "rejection_type": None,
            "rejection_reasons": ["Active future booking already exists"],
            "has_active_booking": False,
        }

        mock_chat_controller.create_chat.assert_not_called()
        mock_meeting_controller.create_meeting_url.assert_not_called()

    async def test_returns_early_when_booking_not_found(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        mock_db.get_booking = AsyncMock(return_value=None)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_created("nonexistent-uid")

        mock_chat_controller.create_chat.assert_not_called()
        mock_meeting_controller.create_meeting_url.assert_not_called()
        mock_events.send_notification_command.assert_not_called()
        mock_events.send_event.assert_not_called()


class TestHandleCancelled:
    async def test_deletes_chat_and_meeting_urls(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_cancelled(booking.uid, cancellation_reason="No longer needed")

        mock_events.send_notification_command.assert_called_once()
        notif_kwargs = mock_events.send_notification_command.call_args.kwargs
        assert notif_kwargs["trigger_event"] == "BOOKING_CANCELLED"
        assert notif_kwargs["template_data"]["cancellation_reason"] == "No longer needed"

        mock_chat_controller.delete_chat.assert_called_once_with(
            channel_id=booking.uid,
            booking_uid=booking.uid,
        )

        assert mock_meeting_controller.delete_meeting_url.call_count == 2  # noqa: PLR2004
        delete_calls = mock_meeting_controller.delete_meeting_url.call_args_list
        prefixes_deleted = {c.kwargs["external_id_prefix"] for c in delete_calls}
        assert "" in prefixes_deleted
        assert CLIENT_PREFIX in prefixes_deleted


class TestHandleRescheduled:
    async def test_updates_meeting_urls_and_notifies(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking(from_reschedule="old-booking-uid")
        mock_db.get_booking = AsyncMock(return_value=booking)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        previous_start = "2026-06-10T10:00:00+00:00"
        await controller.handle_rescheduled(booking.uid, previous_start_time=previous_start)

        assert mock_meeting_controller.create_meeting_url.call_count == 2  # noqa: PLR2004
        url_calls = mock_meeting_controller.create_meeting_url.call_args_list
        for c in url_calls:
            assert c.kwargs["is_update_url_data"] is True

        mock_events.send_notification_command.assert_called_once()
        notif_kwargs = mock_events.send_notification_command.call_args.kwargs
        assert notif_kwargs["trigger_event"] == "BOOKING_RESCHEDULED"
        assert notif_kwargs["template_data"]["previous_start_time"] == previous_start
