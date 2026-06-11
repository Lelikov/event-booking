"""Service configuration via environment variables."""

from pydantic import AmqpDsn, Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    debug: bool = False
    log_level: str = "INFO"

    # Cal.com PostgreSQL
    calcom_postgres_dsn: PostgresDsn = Field(strict=True)

    # RabbitMQ (queue name/args come from event_schemas.queues.BOOKING_LIFECYCLE_BOOKING_QUEUE).
    # No default: guest:guest credentials must never be implicit.
    rabbit_url: AmqpDsn = Field(strict=True)
    rabbit_exchange: str = "events"

    # event-receiver (publish events). Required: without it the service silently produces zero output.
    events_endpoint_url: str = Field(strict=True)
    events_api_key: str | None = None
    events_source: str = "booking"
    events_timeout_seconds: float = 5.0

    # Jitsi JWT. sub must be the fixed tenant/domain — never the wildcard '*'.
    jitsi_jwt_secret: str = Field(strict=True)
    jitsi_jwt_aud: str = Field(strict=True)
    jitsi_jwt_iss: str = Field(strict=True)
    jitsi_jwt_sub: str = Field(strict=True)
    meeting_host_url: str = "http://localhost:8080"

    # GetStream Chat
    chat_api_key: str = Field(strict=True)
    chat_api_secret: str = Field(strict=True)
    chat_user_id_encryption_key: str = Field(strict=True)
    chat_timeout_seconds: float = 6.0

    # Shortify
    shortener_url: str = Field(strict=True)
    shortener_api_key: str | None = None

    # Booking constraints
    is_enable_booking_constraints: bool = False

    # Reminder scheduler
    reminder_interval_seconds: int = 300
    reminder_shift_from_minutes: int = 55
    reminder_shift_to_minutes: int = 65
