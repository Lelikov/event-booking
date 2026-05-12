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

    # RabbitMQ
    rabbit_url: AmqpDsn = "amqp://guest:guest@localhost:5672/"
    rabbit_exchange: str = "events"
    booking_lifecycle_queue: str = "events.booking.lifecycle"

    # event-receiver (publish events)
    events_endpoint_url: str | None = None
    events_api_key: str | None = None
    events_source: str = "booking"
    events_timeout_seconds: float = 5.0

    # Jitsi JWT
    jitsi_jwt_secret: str = Field(strict=True)
    jitsi_jwt_aud: str = Field(strict=True)
    jitsi_jwt_iss: str = Field(strict=True)
    meeting_host_url: str = "http://localhost:8080"

    # GetStream Chat
    chat_api_key: str = Field(strict=True)
    chat_api_secret: str = Field(strict=True)
    chat_user_id_encryption_key: str = Field(strict=True)

    # Shortify
    shortener_url: str = Field(strict=True)
    shortener_api_key: str | None = None

    # Booking constraints
    is_enable_booking_constraints: bool = False

    # Reminder scheduler
    reminder_interval_seconds: int = 300
    reminder_shift_from_minutes: int = 55
    reminder_shift_to_minutes: int = 65
