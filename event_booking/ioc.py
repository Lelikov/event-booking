"""Dishka dependency injection container for event-booking service.

Scope layout:
- APP: settings, engine, sessionmaker, broker, HTTP publisher, GetStream/shortener
  adapters and the stateless controllers built on them.
- REQUEST: AsyncSession and everything that touches it (SqlExecutor, db adapter,
  MeetingController, BookingController). The consumer opens one REQUEST scope per
  RabbitMQ message; the scheduler opens one per poll tick. Sessions are never
  shared across concurrent work units.
"""

from collections.abc import AsyncIterator

from dishka import AsyncContainer, Provider, Scope, provide
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from event_booking.adapters.db import BookingDatabaseAdapter
from event_booking.adapters.events import EventPublisher
from event_booking.adapters.get_stream import GetStreamAdapter
from event_booking.adapters.shortener import UrlShortenerAdapter
from event_booking.adapters.sql import SqlExecutor
from event_booking.config import Settings
from event_booking.consumer import BookingConsumer
from event_booking.controllers.booking import BookingController
from event_booking.controllers.chat import ChatController
from event_booking.controllers.constraints import analyze_on_create
from event_booking.controllers.meeting import MeetingController
from event_booking.dtos import AttendeeBookingDTO, BookingDTO, ConstraintsResult
from event_booking.interfaces.constraints import IBookingConstraintsAnalyzer
from event_booking.scheduler import ReminderScheduler

BROKER_GRACEFUL_TIMEOUT_SECONDS = 30.0


class _ConstraintsAnalyzerAdapter:
    """Wraps the module-level analyze_on_create function behind the Protocol interface."""

    def analyze_on_create(
        self, *, booking: BookingDTO, attendee_bookings: list[AttendeeBookingDTO]
    ) -> ConstraintsResult:
        return analyze_on_create(booking=booking, attendee_bookings=attendee_bookings)


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def get_settings(self) -> Settings:
        return Settings()  # type: ignore[call-arg]

    @provide
    async def get_engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        engine = create_async_engine(str(settings.calcom_postgres_dsn), echo=settings.debug)
        yield engine
        await engine.dispose()

    @provide
    def get_sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(engine, expire_on_commit=False)

    @provide(scope=Scope.REQUEST)
    async def get_session(self, factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    @provide(scope=Scope.REQUEST)
    def get_sql_executor(self, session: AsyncSession) -> SqlExecutor:
        return SqlExecutor(session)

    @provide(scope=Scope.REQUEST)
    def get_db_adapter(self, executor: SqlExecutor) -> BookingDatabaseAdapter:
        return BookingDatabaseAdapter(executor)

    @provide
    def get_rabbit_broker(self, settings: Settings) -> RabbitBroker:
        return RabbitBroker(str(settings.rabbit_url), graceful_timeout=BROKER_GRACEFUL_TIMEOUT_SECONDS)

    @provide
    def get_rabbit_exchange(self, settings: Settings) -> RabbitExchange:
        return RabbitExchange(settings.rabbit_exchange, type=ExchangeType.TOPIC, durable=True)

    @provide
    def get_event_publisher(self, settings: Settings) -> EventPublisher:
        return EventPublisher(
            endpoint_url=settings.events_endpoint_url,
            api_key=settings.events_api_key,
            source=settings.events_source,
            timeout_seconds=settings.events_timeout_seconds,
        )

    @provide
    def get_get_stream_adapter(self, settings: Settings) -> GetStreamAdapter:
        return GetStreamAdapter(
            chat_api_key=settings.chat_api_key,
            chat_api_secret=settings.chat_api_secret,
            user_id_encryption_key=settings.chat_user_id_encryption_key,
            timeout_seconds=settings.chat_timeout_seconds,
        )

    @provide
    def get_chat_controller(self, chat_client: GetStreamAdapter, events: EventPublisher) -> ChatController:
        return ChatController(chat_client=chat_client, events=events)

    @provide
    def get_url_shortener(self, settings: Settings) -> UrlShortenerAdapter:
        return UrlShortenerAdapter(base_url=settings.shortener_url, api_key=settings.shortener_api_key)

    @provide
    def get_constraints_analyzer(self) -> IBookingConstraintsAnalyzer:
        return _ConstraintsAnalyzerAdapter()  # type: ignore[return-value]

    @provide(scope=Scope.REQUEST)
    def get_meeting_controller(
        self,
        shortener: UrlShortenerAdapter,
        chat_client: GetStreamAdapter,
        db: BookingDatabaseAdapter,
        events: EventPublisher,
        settings: Settings,
    ) -> MeetingController:
        return MeetingController(
            shortener=shortener,
            chat_client=chat_client,
            db=db,
            events=events,
            jitsi_jwt_secret=settings.jitsi_jwt_secret,
            jitsi_jwt_aud=settings.jitsi_jwt_aud,
            jitsi_jwt_iss=settings.jitsi_jwt_iss,
            meeting_host_url=settings.meeting_host_url,
        )

    @provide(scope=Scope.REQUEST)
    def get_booking_controller(  # noqa: PLR0913
        self,
        db: BookingDatabaseAdapter,
        events: EventPublisher,
        chat_controller: ChatController,
        meeting_controller: MeetingController,
        constraints_analyzer: IBookingConstraintsAnalyzer,
        settings: Settings,
    ) -> BookingController:
        return BookingController(
            db=db,
            events=events,
            chat_controller=chat_controller,
            meeting_controller=meeting_controller,
            constraints_analyzer=constraints_analyzer,
            is_enable_constraints=settings.is_enable_booking_constraints,
        )

    @provide
    def get_booking_consumer(self, container: AsyncContainer) -> BookingConsumer:
        return BookingConsumer(container)

    @provide
    def get_reminder_scheduler(
        self,
        container: AsyncContainer,
        events: EventPublisher,
        settings: Settings,
    ) -> ReminderScheduler:
        return ReminderScheduler(
            container=container,
            events=events,
            interval_seconds=settings.reminder_interval_seconds,
            shift_from_minutes=settings.reminder_shift_from_minutes,
            shift_to_minutes=settings.reminder_shift_to_minutes,
        )
