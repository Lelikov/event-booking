# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`event-booking` is the booking orchestrator. It consumes booking lifecycle CloudEvents from
RabbitMQ (FastStream), reads/writes the **cal.com PostgreSQL database directly**, creates
GetStream chat channels, mints Jitsi JWTs + Shortify short URLs per participant, and publishes
follow-up CloudEvents back through event-receiver over HTTP.

**Tech Stack**: Python 3.14, FastAPI (health only), FastStream (RabbitMQ), Dishka DI, SQLAlchemy
(raw `text()` SQL), stream-chat, PyJWT, httpx, structlog, CloudEvents.

## HARD INVARIANTS

- **cal.com owns its schema.** This service NEVER creates migrations and NEVER `DELETE`s cal.com
  rows. Allowed writes: `Booking.status`/`rejectionReason` updates and `Booking.metadata` merges
  (`videoCallUrl`, `bookingReminderSentAt`).
- cal.com timestamps are `timestamp(3) without time zone` (naive UTC). `adapters/db.py` is the
  timezone boundary: rows leave as aware UTC, bind params are converted back to naive UTC.
  Never compare naive and aware datetimes elsewhere.
- The organizer's meeting URL (moderator JWT) must NEVER be delivered to the client. Each
  participant gets their own tokenized URL.
- GetStream user ids are AES-GCM-encrypted emails (deterministic HMAC-derived nonce); the wire
  format must stay decodable by event-receiver's `decode_getstream_user_id`.

## Development Commands

```bash
uv sync                      # install deps
uv run pytest                # tests
uv run ruff check --fix .    # lint
uv run ruff format .         # format
uvicorn event_booking.main:app --port 8990   # run (requires RabbitMQ + cal.com DB)
```

Required env: `CALCOM_POSTGRES_DSN`, `RABBIT_URL`, `EVENTS_ENDPOINT_URL`, `JITSI_JWT_SECRET`,
`JITSI_JWT_AUD`, `JITSI_JWT_ISS`, `JITSI_JWT_SUB`, `CHAT_API_KEY`, `CHAT_API_SECRET`,
`CHAT_USER_ID_ENCRYPTION_KEY`, `SHORTENER_URL`. See `event_booking/config.py`.

## Architecture

```
events.booking.lifecycle.booking (queue, spec from event_schemas.queues)
        │  CloudEvents binary mode, {original, normalized} envelope
        ▼
consumer.py BookingConsumer ── ce-bookingid + ce id (dedupe seed)
        ▼
controllers/booking.py BookingController        (REQUEST scope, one per message)
   ├── controllers/constraints.py  pure analyzer (booking.created only)
   ├── adapters/blacklist.py      BlacklistClient → event-admin /api/blacklist/active (TTL cache, fail-open)
   ├── controllers/chat.py     ChatController → adapters/get_stream.py (to_thread)
   ├── controllers/meeting.py  Jitsi JWT + adapters/shortener.py (Shortify)
   ├── adapters/db.py          cal.com PostgreSQL (raw SQL via SqlExecutor)
   └── adapters/events.py      EventPublisher → HTTP POST event-receiver /event/booking

scheduler.py ReminderScheduler — polls cal.com, persistent bookingReminderSentAt marker
```

- **Interfaces** (`interfaces/`): Protocols (`IBookingDatabaseAdapter`, `IChatClient`,
  `IChatController`, `IMeetingController`, `IEventPublisher`, `IUrlShortener`, `ISqlExecutor`).
- **DTOs** (`dtos.py`): frozen dataclasses only.
- **DI** (`ioc.py`): APP scope for stateless adapters/controllers; REQUEST scope for
  `AsyncSession` → `SqlExecutor` → db adapter → `BookingController`. One REQUEST scope per
  RabbitMQ message / scheduler tick.

## Reliability Model (idempotent resume)

No sagas/compensation. Every side effect is idempotent (chat create returns the existing
channel; welcomes skipped when the channel has messages; short URLs keyed by external id;
follow-up events get deterministic UUIDv5 ids from `ce_id`-scoped dedupe keys). Failures
raise → the message dead-letters to `events.booking.lifecycle.booking.dlq` (24h TTL) → replay
resumes without duplicates. `EventPublisher` raises `EventPublishError` on non-2xx.

## Event Handling Semantics

| Event | Behavior |
|---|---|
| `booking.created` | blacklist check FIRST (match → reject with `rejection_type='blacklisted'` + `BOOKING_REJECTED_BLACKLISTED` notification), then optional constraints (reject → cal.com `status='rejected'` + `booking.rejected`), chat + welcomes, per-participant URLs, client URL → `metadata.videoCallUrl`, per-recipient notifications |
| `booking.rescheduled` | new uid; delete OLD uid's chat (`previous_booking_uid` payload, fallback `fromReschedule`), recreate chat, MOVE short URLs to new uid, notify with URLs |
| `booking.reassigned` | HARD-delete channel (soft-deleted ids can't be recreated), recreate, regenerate URLs in place, notify |
| `booking.cancelled` | notify, delete chat + both short URLs |
| scheduler | `notification.send_requested` (BOOKING_REMINDER) + `booking.reminder_sent`, then mark `bookingReminderSentAt` |

## Code Style Rules

- **No `elif`**, avoid `else` — early returns, guard clauses, mapping dicts.
- Ruff line length 120; Protocol-based interfaces; frozen dataclass DTOs; raw `text()` SQL only.
- Every fix/feature needs a test (`tests/` mirrors package layout).

## Service Documentation

- `docs/SERVICE_OVERVIEW.md` — architecture, env vars, reliability model
- `docs/API_CONTRACTS.md` — consumed/published events and payloads
- `docs/DEPENDENCIES.md` — external services and failure modes
- `docs/AUDIT.md` — audit findings and resolutions

Cross-service contracts: `../docs/architecture/MESSAGE_CONTRACTS.md` and
`../docs/audit/v2/CONTRACT_DECISIONS.md` (canonical queues/envelope/payloads).

## Documentation Requirements

All code changes MUST include corresponding documentation updates:
- New/changed consumed or published events → `docs/API_CONTRACTS.md` + `../docs/architecture/MESSAGE_CONTRACTS.md`
- New/changed dependencies → `docs/DEPENDENCIES.md`
- Architectural changes → `docs/SERVICE_OVERVIEW.md`
- Bug fixes for audit findings → `docs/AUDIT.md`
