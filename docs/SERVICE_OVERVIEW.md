# event-booking Service Overview

> **Maturity: EARLY / PRE-PRODUCTION**
>
> This service is a core domain orchestrator for managing booking constraints, chat creation, and meeting URL generation. Audit-v2 (2026-06-11) hardening: idempotent-resume reliability model (no partial-failure orphans), persistent reminder marker, per-participant meeting URLs, AES-GCM chat user ids. See `docs/AUDIT.md`.

## Domain

Booking orchestration and enrichment service. Consumes booking lifecycle events from RabbitMQ, applies constraint validation, creates GetStream chat channels, generates Jitsi meeting URLs (JWT-signed), shortens the URLs via Shortify, and publishes notifications. Also maintains a background reminder scheduler to emit booking.reminder_sent events.

## Request Flow

```
RabbitMQ topic exchange ("events")
  queue: events.booking.lifecycle.booking (own queue; event-saver has events.booking.lifecycle.saver)
  binding key: events.booking.lifecycle
        |
        v
BookingConsumer                           (consumer.py:25-80)
  - parses CloudEvent (cloudevents-sdk from_http)
  - extracts booking_uid, event_type, and additional data
  - dispatches to BookingController based on event type
        |
        ▼ (booking.created, booking.rescheduled, booking.reassigned, booking.cancelled)
BookingController                         (controllers/booking.py:24-150)
  - [booking.created] Check constraints, create chat, generate meeting URLs, notify
  - [booking.rescheduled] Update meeting URLs, notify
  - [booking.reassigned] Recreate chat, regenerate URLs, notify
  - [booking.cancelled] Cleanup chat, publish cancellation audit event
        |
        ├──► ConstraintsAnalyzer (controllers/constraints.py)
        ├──► IBookingDatabaseAdapter (adapters/db.py) → Cal.com PostgreSQL
        ├──► ChatController (controllers/chat.py) → GetStream Chat API
        ├──► MeetingController (controllers/meeting.py) → Jitsi JWT + Shortify
        └──► EventPublisher (adapters/events.py) → event-receiver

ReminderScheduler                         (scheduler.py)
  - polls database every N seconds
  - finds upcoming bookings (55-65 minutes before start)
  - emits booking.reminder_sent events
        │
        └──► EventPublisher → event-receiver
```

## Responsibilities

- Consume booking lifecycle events from RabbitMQ (`events.booking.lifecycle.booking` queue, bound to routing key `events.booking.lifecycle`)
- Validate bookings against organizational constraints (when enabled)
- Reject bookings that violate constraints; publish `booking.rejected` events
- Create GetStream chat channels for accepted bookings (one channel per booking)
- Generate Jitsi meeting URLs with JWT-signed tokens
- Shorten URLs using Shortify API
- Publish audit events (`meeting.url_created`, `meeting.url_deleted`, `chat.created`, `chat.deleted`)
- Publish notification commands (`notification.send_requested`) to event-notifier
- Run background reminder scheduler: find upcoming bookings and emit `booking.reminder_sent` events
- Read/write Cal.com PostgreSQL database (maintains booking and attendee records)

## NOT Responsible For

- HTTP API (no external API beyond `/health` endpoint)
- User account management (handled by event-users)
- Transactional email sending (handled by event-notifier)
- Database migrations (not yet implemented; schema is pre-created in Cal.com)
- Message publishing to RabbitMQ directly (uses event-receiver via HTTP)

## Runtime Dependencies

| Dependency | Role | Connection |
|---|---|---|
| **RabbitMQ** | Message broker (consumer) | `RABBIT_URL` (AMQP, required), topic exchange `events`, queue `events.booking.lifecycle.booking` |
| **Cal.com PostgreSQL** | Persistent store (read/write) | `CALCOM_POSTGRES_DSN` (asyncpg), manages booking and attendee records |
| **event-receiver** | Event publishing (HTTP) | `EVENTS_ENDPOINT_URL`, `EVENTS_API_KEY` (Bearer token) |
| **GetStream Chat API** | Chat channel creation/deletion | `CHAT_API_KEY`, `CHAT_API_SECRET`, HTTP REST |
| **Jitsi** | Meeting URL generation (JWT only) | `JITSI_JWT_SECRET`, `JITSI_JWT_AUD`, `JITSI_JWT_ISS`, `JITSI_JWT_SUB`, no direct connection (JWT only) |
| **Shortify** | URL shortening service | `SHORTENER_URL`, `SHORTENER_API_KEY` (optional), HTTP REST |

## Key Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `CALCOM_POSTGRES_DSN` | Yes | - | Cal.com PostgreSQL connection string |
| `RABBIT_URL` | Yes | - | RabbitMQ AMQP URL (no guest:guest default) |
| `RABBIT_EXCHANGE` | No | `events` | Exchange name (queue spec from `event_schemas.queues`) |
| `EVENTS_ENDPOINT_URL` | Yes | - | event-receiver endpoint (`POST /event/booking`); service refuses to start without it |
| `EVENTS_API_KEY` | No | - | API key for event-receiver (raw `Authorization` header) |
| `JITSI_JWT_SECRET` | Yes | - | Jitsi JWT signing secret |
| `JITSI_JWT_AUD` | Yes | - | Jitsi JWT audience claim |
| `JITSI_JWT_ISS` | Yes | - | Jitsi JWT issuer claim |
| `JITSI_JWT_SUB` | Yes | - | Jitsi JWT sub (fixed tenant/domain; never `*`) |
| `MEETING_HOST_URL` | No | `http://localhost:8080` | Base URL for Jitsi meeting links |
| `CHAT_API_KEY` | Yes | - | GetStream API key |
| `CHAT_API_SECRET` | Yes | - | GetStream API secret |
| `CHAT_USER_ID_ENCRYPTION_KEY` | Yes | - | AES-GCM key material for chat user ids (must match event-receiver) |
| `CHAT_TIMEOUT_SECONDS` | No | `6.0` | GetStream SDK request timeout |
| `CHAT_BASE_URL` | No | `https://chat.stream-io-api.com` | GetStream API base URL (override only for local mock stacks) |
| `SHORTENER_URL` | Yes | - | Base URL of Shortify service |
| `SHORTENER_API_KEY` | No | - | Shortify API key (optional) |
| `IS_ENABLE_BOOKING_CONSTRAINTS` | No | `False` | Enable/disable constraint validation |
| `REMINDER_INTERVAL_SECONDS` | No | `300` | Scheduler polling interval |
| `REMINDER_SHIFT_FROM_MINUTES` | No | `55` | Remind N minutes before start (min) |
| `REMINDER_SHIFT_TO_MINUTES` | No | `65` | Remind N minutes before start (max) |
| `DEBUG` | No | `False` | Enable debug mode |
| `LOG_LEVEL` | No | `INFO` | Structlog level |

Reference: `event_booking/config.py:7-54`

## Architecture

### Clean Architecture Layer Map

```mermaid
graph TB
    subgraph Entry["Entry Points"]
        MAIN["main.py<br/>(FastAPI lifespan)"]
        CONSUMER["consumer.py<br/>BookingConsumer"]
        SCHEDULER["scheduler.py<br/>ReminderScheduler"]
    end

    subgraph Application["Application/Control Layer"]
        BC["controllers/booking.py<br/>BookingController"]
        CC["controllers/chat.py<br/>ChatController"]
        MC["controllers/meeting.py<br/>MeetingController"]
        CA["controllers/constraints.py<br/>ConstraintsAnalyzer"]
    end

    subgraph Domain["Domain Models"]
        DTOs["dtos.py<br/>BookingDTO, ConstraintsResult"]
    end

    subgraph Infrastructure["Infrastructure Layer"]
        DB["adapters/db.py<br/>Cal.com Database Adapter"]
        EP["adapters/events.py<br/>Event Publisher (HTTP)"]
        GS["adapters/get_stream.py<br/>GetStream Client"]
        SH["adapters/shortener.py<br/>Shortify Client"]
        SQL["adapters/sql.py<br/>SqlExecutor"]
    end

    subgraph Interfaces["Interfaces (Protocols)"]
        IDB["IBookingDatabaseAdapter"]
        IEP["IEventPublisher"]
        IMC["IMeetingController"]
        ICA["IBookingConstraintsAnalyzer"]
    end

    subgraph DI["Dependency Injection"]
        IOC["ioc.py<br/>AppProvider (Dishka)"]
    end

    MAIN --> CONSUMER
    MAIN --> SCHEDULER
    CONSUMER --> BC
    BC --> IDB & IEP & IMC & ICA
    CC --> GS
    MC --> SH
    CA --> DTOs
    IOC --> MAIN
    DB --> SQL

    style Domain fill:#e8f5e9
    style Application fill:#e3f2fd
    style Infrastructure fill:#fff3e0
    style Entry fill:#fce4ec
```

### File-to-Layer Mapping

| Layer | Files | Responsibility |
|---|---|---|
| Entry | `main.py`, `scheduler.py`, `consumer.py` | Lifespan, message routing, background tasks |
| Domain models | `dtos.py` | Data transfer objects (BookingDTO, ConstraintsResult) |
| Application | `controllers/booking.py`, `controllers/chat.py`, `controllers/meeting.py`, `controllers/constraints.py` | Orchestration and business logic |
| Infrastructure | `adapters/db.py`, `adapters/events.py`, `adapters/get_stream.py`, `adapters/shortener.py`, `adapters/sql.py` | Database, HTTP clients, external service integrations (Jitsi JWT is minted in `controllers/meeting.py`) |
| Interfaces | `interfaces/db.py`, `interfaces/events.py`, `interfaces/meeting.py`, `interfaces/constraints.py` | Protocol definitions for loose coupling |
| DI/Config | `ioc.py`, `config.py`, `logger.py` | Dependency injection, configuration, logging |

### Event Types Consumed

| Event Type | Queue | Source | Handler |
|---|---|---|---|
| `booking.created` | `events.booking.lifecycle` | event-receiver | `BookingController.handle_created()` |
| `booking.rescheduled` | `events.booking.lifecycle` | event-receiver | `BookingController.handle_rescheduled()` |
| `booking.reassigned` | `events.booking.lifecycle` | event-receiver | `BookingController.handle_reassigned()` |
| `booking.cancelled` | `events.booking.lifecycle` | event-receiver | `BookingController.handle_cancelled()` |

Reference: `consumer.py:15-48`

### Event Types Published

| Event Type | Destination | Purpose | When |
|---|---|---|---|
| `meeting.url_created` | event-receiver → `events.notification.delivery` (via routing) | Audit: meeting URL was generated | After successful Jitsi + Shortify generation |
| `meeting.url_deleted` | event-receiver → `events.notification.delivery` | Audit: meeting URL was revoked | On booking cancellation |
| `chat.created` | event-receiver → `events.notification.delivery` | Audit: GetStream channel created | After successful chat creation |
| `chat.deleted` | event-receiver → `events.notification.delivery` | Audit: GetStream channel deleted | On booking cancellation |
| `booking.rejected` | event-receiver → `events.booking.lifecycle` | Booking constraint violation | When constraints enabled and violation detected |
| `notification.send_requested` | event-receiver → `events.notification.commands` | Notify client/organizer | On booking created/rescheduled/reassigned/cancelled and reminder triggers |
| `booking.reminder_sent` | event-receiver → `events.booking.lifecycle` | Audit: reminder was triggered | From background scheduler (55-65 min before start) |

Reference: `controllers/booking.py`, `adapters/events.py`

### Booking Lifecycle in event-booking

1. **booking.created** event arrives
   - Load booking details from Cal.com database
   - [Optional] Run constraint analysis; reject if violation
   - Create GetStream chat channel (one per booking)
   - Generate Jitsi JWT + meeting URL
   - Shorten URL via Shortify
   - Publish `meeting.url_created` and `chat.created` audit events
   - Publish `notification.send_requested` to event-notifier (notify both parties)

2. **booking.rescheduled** event arrives (cal.com mints a NEW uid)
   - Delete the OLD uid's chat (from payload `previous_booking_uid`, fallback cal.com `fromReschedule`)
   - Create chat for the new uid (welcome skipped if channel already has messages)
   - MOVE short URLs from the old external ids onto the new uid (shortener update API, create fallback)
   - Write client URL to cal.com `Booking.metadata.videoCallUrl`
   - Publish per-recipient `notification.send_requested` with each recipient's own `meeting_url` and `previous_start_time`

3. **booking.reassigned** event arrives
   - HARD-delete the GetStream channel (soft-deleted channel ids cannot be recreated)
   - Recreate with new organizer; regenerate both participants' URLs in place
   - Publish `chat.deleted`/`chat.created` and per-recipient `notification.send_requested`

4. **booking.cancelled** event arrives
   - Publish per-recipient `notification.send_requested` with cancellation info
   - Delete GetStream chat channel and both short URLs
   - Publish `chat.deleted` and `meeting.url_deleted` audit events

5. **Reminder Scheduler** (background)
   - Polls Cal.com database every N seconds
   - Finds bookings with start_time in [now + 55 min, now + 65 min] WITHOUT the `bookingReminderSentAt` metadata marker
   - Publishes `notification.send_requested` (BOOKING_REMINDER) per booking with deterministic dedupe key, emits `booking.reminder_sent`, then writes the marker

### Reliability Model (idempotent resume)

Every step is idempotent: chat creation returns the existing channel, welcome
messages are skipped when the channel already has messages, short URLs are keyed
by external id, and all follow-up events carry deterministic dedupe ids derived
from the inbound CloudEvent id. Any failure propagates -> the message is rejected
and dead-letters to `events.booking.lifecycle.booking.dlq` (24h TTL) -> a DLQ
replay resumes exactly where the flow stopped without duplicating side effects.

## Known Limitations and Gaps

| Severity | Issue | Location | Status |
|---|---|---|---|
| HIGH | No rollback on partial failure | `controllers/booking.py` | Fixed (audit-v2): idempotent resume + DLQ replay |
| HIGH | Reminder scheduler duplicates | `scheduler.py` | Fixed (audit-v2): persistent metadata marker + dedupe keys |
| MEDIUM | Constraint analyzer covers a fixed rule set (active/month/year/interval) only | `controllers/constraints.py` | Open (by design; reschedules are not re-validated) |
| LOW | cal.com schema is external; no Alembic migrations (HARD INVARIANT: never alter or delete cal.com rows beyond status/metadata updates) | `adapters/db.py` | By design |
| LOW | DLQ replay is manual (no automatic retry policy) | `consumer.py` | Open (24h TTL parking lot) |

---

## Integration Points

### Cal.com PostgreSQL

- **Role:** Primary data store for bookings and attendees
- **Access pattern:** Read booking details on event arrival; update/delete on lifecycle events
- **Schema:** Pre-created (not managed by Alembic); see `adapters/db.py` for table names and columns
- **Failure mode:** If DB unavailable, event processing fails and message is requeued by RabbitMQ

### GetStream Chat API

- **Role:** Create/delete chat channels per booking
- **API:** REST with API key + secret authentication
- **Failure mode:** If GetStream is down, chat creation fails; booking is still processed but client cannot access chat

### Jitsi + Shortify

- **Role:** Generate meeting URLs with JWT tokens; shorten them
- **Jitsi:** JWT-only integration; no direct HTTP connection to Jitsi servers
- **Shortify:** HTTP REST API for URL shortening
- **Failure mode:** If Shortify is down, meeting URL generation fails; event processing may not complete

### event-receiver

- **Role:** Publishes events to RabbitMQ on behalf of event-booking
- **API:** `POST /event/cloudevents` with `Authorization: Bearer {token}` header
- **Failure mode:** If event-receiver is down, audit/notification events cannot be published; local event processing continues but downstream services miss updates

---

## Testing Strategy

- **Unit tests:** Controllers and domain logic (constraints, DTOs)
- **Integration tests:** Database adapter, GetStream client, event publisher (mocked HTTP)
- **Consumer tests:** CloudEvent parsing and event dispatch
- **Scheduler tests:** Background task scheduling and reminder emission

Reference: `tests/` directory
