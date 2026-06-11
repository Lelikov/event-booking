"""Tests for BookingController orchestrator."""

from unittest.mock import AsyncMock, MagicMock

from event_schemas.types import EventType

from event_booking.controllers.booking import CLIENT_PREFIX, BookingController
from event_booking.dtos import ConstraintsResult
from tests.factories import make_booking, make_client, make_user


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
        mock_chat_controller.has_messages = AsyncMock(return_value=False)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_created(booking.uid, ce_id="ce-1")

        mock_chat_controller.create_chat.assert_called_once()
        chat_args = mock_chat_controller.create_chat.call_args
        assert chat_args.args == (booking.uid, booking.user.email, booking.client.email)
        assert mock_chat_controller.send_message.call_count == 2  # noqa: PLR2004

        assert mock_meeting_controller.create_meeting_url.call_count == 2  # noqa: PLR2004
        calls = mock_meeting_controller.create_meeting_url.call_args_list
        prefixes = {c.kwargs["external_id_prefix"] for c in calls}
        assert "" in prefixes
        assert CLIENT_PREFIX in prefixes

    async def test_each_participant_gets_own_meeting_url(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        """The client must never receive the organizer's personal (moderator) URL."""
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_chat_controller.has_messages = AsyncMock(return_value=False)
        urls = {"": "https://short.test/org", CLIENT_PREFIX: "https://short.test/client"}
        mock_meeting_controller.create_meeting_url = AsyncMock(
            side_effect=lambda **kwargs: urls[kwargs["external_id_prefix"]]
        )

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_created(booking.uid, ce_id="ce-1")

        assert mock_events.send_notification_command.call_count == 2  # noqa: PLR2004
        by_role = {
            call.kwargs["recipients"][0]["role"]: call.kwargs
            for call in mock_events.send_notification_command.call_args_list
        }
        assert by_role["organizer"]["template_data"]["meeting_url"] == "https://short.test/org"
        assert by_role["client"]["template_data"]["meeting_url"] == "https://short.test/client"
        assert by_role["client"]["recipients"] == [{"email": booking.client.email, "role": "client"}]

    async def test_recipient_locale_propagates_from_calcom_rows(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        """users.locale / Attendee.locale reach notification.send_requested recipients."""
        booking = make_booking(user=make_user(locale="ru"), client=make_client(locale="en"))
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_chat_controller.has_messages = AsyncMock(return_value=False)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_created(booking.uid, ce_id="ce-1")

        by_role = {
            call.kwargs["recipients"][0]["role"]: call.kwargs["recipients"][0]
            for call in mock_events.send_notification_command.call_args_list
        }
        assert by_role["organizer"]["locale"] == "ru"
        assert by_role["client"]["locale"] == "en"

    async def test_recipient_without_locale_omits_the_key(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_chat_controller.has_messages = AsyncMock(return_value=False)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_created(booking.uid, ce_id="ce-1")

        for call in mock_events.send_notification_command.call_args_list:
            assert all("locale" not in recipient for recipient in call.kwargs["recipients"])

    async def test_writes_client_url_to_calcom_video_call_url(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_chat_controller.has_messages = AsyncMock(return_value=False)
        urls = {"": "https://short.test/org", CLIENT_PREFIX: "https://short.test/client"}
        mock_meeting_controller.create_meeting_url = AsyncMock(
            side_effect=lambda **kwargs: urls[kwargs["external_id_prefix"]]
        )

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_created(booking.uid, ce_id="ce-1")

        mock_db.update_booking_video_url.assert_called_once_with(booking.uid, "https://short.test/client")

    async def test_skips_welcome_messages_on_redelivery(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_chat_controller.has_messages = AsyncMock(return_value=True)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_created(booking.uid, ce_id="ce-1")

        mock_chat_controller.create_chat.assert_called_once()
        mock_chat_controller.send_message.assert_not_called()

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

        await controller.handle_created(booking.uid, ce_id="ce-1")

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
        assert event_kwargs["event"] == EventType.BOOKING_REJECTED
        assert event_kwargs["data"] == {
            "client_email": booking.client.email,
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

        await controller.handle_cancelled(booking.uid, cancellation_reason="No longer needed", ce_id="ce-9")

        assert mock_events.send_notification_command.call_count == 2  # noqa: PLR2004
        for call in mock_events.send_notification_command.call_args_list:
            assert call.kwargs["trigger_event"] == "BOOKING_CANCELLED"
            assert call.kwargs["template_data"]["cancellation_reason"] == "No longer needed"

        mock_chat_controller.delete_chat.assert_called_once()
        delete_args = mock_chat_controller.delete_chat.call_args
        assert delete_args.args == (booking.uid, booking.uid)

        assert mock_meeting_controller.delete_meeting_url.call_count == 2  # noqa: PLR2004
        delete_calls = mock_meeting_controller.delete_meeting_url.call_args_list
        prefixes_deleted = {c.kwargs["external_id_prefix"] for c in delete_calls}
        assert "" in prefixes_deleted
        assert CLIENT_PREFIX in prefixes_deleted


class TestHandleRescheduled:
    async def test_cleans_up_previous_uid_and_moves_urls(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_chat_controller.has_messages = AsyncMock(return_value=False)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        previous_start = "2026-06-10T10:00:00+00:00"
        await controller.handle_rescheduled(
            booking.uid,
            previous_start_time=previous_start,
            previous_booking_uid="old-booking-uid",
            ce_id="ce-2",
        )

        # The orphaned chat of the OLD uid is deleted, a chat for the new uid is created
        delete_args = mock_chat_controller.delete_chat.call_args
        assert delete_args.args == ("old-booking-uid", booking.uid)
        mock_chat_controller.create_chat.assert_called_once()

        # Short URLs are MOVED from the old external ids to the new uid
        url_calls = mock_meeting_controller.create_meeting_url.call_args_list
        assert len(url_calls) == 2  # noqa: PLR2004
        for c in url_calls:
            assert c.kwargs["previous_booking_uid"] == "old-booking-uid"

        assert mock_events.send_notification_command.call_count == 2  # noqa: PLR2004
        for call in mock_events.send_notification_command.call_args_list:
            assert call.kwargs["trigger_event"] == "BOOKING_RESCHEDULED"
            assert call.kwargs["template_data"]["previous_start_time"] == previous_start
            assert "meeting_url" in call.kwargs["template_data"]

    async def test_falls_back_to_from_reschedule_column(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking(from_reschedule="column-old-uid")
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_chat_controller.has_messages = AsyncMock(return_value=False)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_rescheduled(booking.uid, ce_id="ce-3")

        delete_args = mock_chat_controller.delete_chat.call_args
        assert delete_args.args == ("column-old-uid", booking.uid)


class TestHandleReassigned:
    async def test_hard_deletes_and_recreates_chat(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_chat_controller.has_messages = AsyncMock(return_value=False)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_reassigned(booking.uid, previous_organizer_email="old@test.com", ce_id="ce-4")

        # Soft-deleted channels cannot be recreated under the same id — must hard delete
        delete_args = mock_chat_controller.delete_chat.call_args
        assert delete_args.kwargs["hard"] is True
        mock_chat_controller.create_chat.assert_called_once()

        url_calls = mock_meeting_controller.create_meeting_url.call_args_list
        assert len(url_calls) == 2  # noqa: PLR2004
        for c in url_calls:
            assert c.kwargs["replace_existing"] is True

        assert mock_events.send_notification_command.call_count == 2  # noqa: PLR2004
        for call in mock_events.send_notification_command.call_args_list:
            assert call.kwargs["trigger_event"] == "BOOKING_REASSIGNED"
            assert call.kwargs["template_data"]["previous_organizer_email"] == "old@test.com"


class TestDedupeKeys:
    async def test_follow_up_events_carry_ce_scoped_dedupe_keys(
        self,
        mock_db: AsyncMock,
        mock_events: AsyncMock,
        mock_chat_controller: AsyncMock,
        mock_meeting_controller: AsyncMock,
        mock_constraints_analyzer: MagicMock,
    ) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_chat_controller.has_messages = AsyncMock(return_value=False)

        controller = make_controller(
            mock_db=mock_db,
            mock_events=mock_events,
            mock_chat_controller=mock_chat_controller,
            mock_meeting_controller=mock_meeting_controller,
            mock_constraints_analyzer=mock_constraints_analyzer,
        )

        await controller.handle_created(booking.uid, ce_id="ce-77")

        chat_key = mock_chat_controller.create_chat.call_args.kwargs["dedupe_key"]
        assert chat_key.startswith("ce-77:")
        for call in mock_events.send_notification_command.call_args_list:
            assert call.kwargs["dedupe_key"].startswith("ce-77:")
