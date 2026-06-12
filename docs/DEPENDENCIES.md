# event-booking Dependencies

## Depends On

### RabbitMQ

| Property | Value |
|----------|-------|
| Role | Message ingress (booking lifecycle events) |
| Protocol | AMQP 0-9-1 |
| Config | `RABBIT_URL` (required), `RABBIT_EXCHANGE`; queue spec from `event_schemas.queues.BOOKING_LIFECYCLE_BOOKING_QUEUE` |
| Client | FastStream `RabbitBroker` |
| Connection lifetime | App-scoped (created in lifespan, closed on shutdown) |

**Failure modes:**
- **Connection refused at startup:** Service fails to start (FastStream broker.start() raises).
- **Connection lost at runtime:** FastStream handles reconnection internally. Messages may be redelivered after reconnect.
- **Queue does not exist:** Consumer declares queue on startup with DLQ arguments. If exchange does not exist, binding fails.
- **Queue full/memory limit:** RabbitMQ applies flow control or rejects new messages upstream.

Reference: `config.py:22-24`, `main.py:28-35`, `consumer.py:52-68`

---

### Cal.com PostgreSQL

| Property | Value |
|----------|-------|
| Role | Persistent store for booking and attendee records (read/write) |
| Protocol | PostgreSQL wire protocol via asyncpg |
| Config | `CALCOM_POSTGRES_DSN` |
| Client | `asyncpg.Pool` (created in DI, lifecycle managed by lifespan) |
| Connection lifetime | App-scoped pool |

**Tables accessed:**
- `bookings` -- booking records (uid, start_time, end_time, organizer, client, etc.)
- `attendees` -- participant records (email, role, etc.)

**Failure modes:**
- **Connection refused at startup:** Service fails to start (pool creation raises).
- **Connection lost at runtime (transient):** asyncpg pool reconnects transparently for subsequent `acquire()` calls. In-flight queries fail and propagate.
- **Pool exhaustion:** `acquire()` blocks until a connection is available. Under sustained RabbitMQ load, this can stall event processing.
- **Database schema missing:** Queries fail with table not found errors. Schema must be pre-created (not managed by Alembic migrations).

Reference: `config.py:19`, `adapters/db.py:1-50`, `ioc.py`

---

### event-receiver (HTTP service)

| Property | Value |
|----------|-------|
| Role | Publish events to RabbitMQ (audit events, notifications) |
| Protocol | HTTP REST |
| Endpoint | `POST /event/booking` |
| Auth | Bearer token (`Authorization: {EVENTS_API_KEY} (raw API key, no Bearer prefix)`) |
| Config | `EVENTS_ENDPOINT_URL`, `EVENTS_API_KEY`, `EVENTS_TIMEOUT_SECONDS` |
| Client | httpx `AsyncClient` (timeout: 5s default) |
| Connection lifetime | App-scoped |

**Request format:**
- Method: `POST`
- Headers: `Authorization: <api_key> (raw, no Bearer prefix)`, `Content-Type: application/json`
- Body: JSON CloudEvent object with `type`, `source`, `data` fields

**Response codes:**
- 202 Accepted: Event accepted for publishing
- 400 Bad Request: Invalid payload
- 401 Unauthorized: Invalid or missing API key
- 500 Server Error: event-receiver failure

**Failure modes:**
- **API key invalid:** Persistent 401 responses. All event publishes fail until key is corrected in config.
- **Timeout (>5s):** httpx raises `TimeoutException`. Caught by `EventPublisher`, exception propagated to caller (message rejected and dead-lettered to the service DLQ).
- **HTTP 5xx / connection error:** Transient error. Event publish fails; message rejected and dead-lettered to the service DLQ.
- **event-receiver fully down for extended period:** Notifications and audit events accumulate in memory; if publishing queue grows unbounded, memory pressure may increase.

Reference: `config.py:26-30`, `adapters/events.py:1-60`, `ioc.py`

---

### event-admin (HTTP service, optional)

| Property | Value |
|----------|-------|
| Role | Source of the booking blacklist (currently-effective `client_email` values) |
| Protocol | HTTP REST |
| Endpoint | `GET /api/blacklist/active?field=client_email` |
| Auth | `Authorization: Bearer {BLACKLIST_SERVICE_TOKEN}` (static service token, constant-time compared by event-admin) |
| Config | `EVENT_ADMIN_API_URL`, `BLACKLIST_SERVICE_TOKEN`, `BLACKLIST_CACHE_TTL` (default 300 s), `BLACKLIST_TIMEOUT_SECONDS` (default 5 s), `BLACKLIST_ENABLED` (default true) |
| Client | httpx `AsyncClient` per refresh; APP-scoped in-memory TTL cache |

**Failure modes:**
- **Config absent (`EVENT_ADMIN_API_URL`/`BLACKLIST_SERVICE_TOKEN` unset):** blacklist disabled with a startup warning; booking keeps working in deployments without event-admin.
- **Refresh fails with cache present:** stale cache keeps serving (warning logged).
- **API down with no cache:** **fail-open** — emails treated as not blacklisted, error logged. The blacklist never blocks booking processing.

Reference: `adapters/blacklist.py`, `ioc.py` (`get_blacklist_checker`)

---

### GetStream Chat API

| Property | Value |
|----------|-------|
| Role | Create and delete chat channels per booking |
| Protocol | HTTPS REST |
| Base URL | `https://api.getstream.io` (implicit in client library) |
| Auth | API key + secret (HMAC signing of requests) |
| Config | `CHAT_API_KEY`, `CHAT_API_SECRET`, `CHAT_USER_ID_ENCRYPTION_KEY` |
| Client | GetStream Python SDK (HTTP wrapper) |

**Endpoints used:**
- `POST /channels/` -- create channel
- `DELETE /channels/{type}/{id}` -- delete channel
- `POST /channels/{type}/{id}/query` -- fetch channel details

**Failure modes:**
- **Invalid API credentials:** 401/403 responses. All chat operations fail. Service can still process other booking events but chat creation will fail.
- **HTTP 429 (Rate limit):** GetStream throttles requests. Retry with backoff required.
- **HTTP 5xx / timeout:** Transient error. Chat creation fails; booking event processing may halt if no fallback.
- **GetStream fully down:** All bookings that require chat creation fail. Downstream notifications won't have chat context.

Reference: `config.py:38-41`, `adapters/get_stream.py:1-100`, `controllers/chat.py`

---

### Jitsi (JWT only, no direct connection)

| Property | Value |
|----------|-------|
| Role | Generate JWT tokens for meeting authentication |
| Protocol | Local JWT encoding (no HTTP calls to Jitsi) |
| Config | `JITSI_JWT_SECRET`, `JITSI_JWT_AUD`, `JITSI_JWT_ISS`, `JITSI_JWT_SUB`, `MEETING_HOST_URL` |
| Client | PyJWT library |
| Connection lifetime | Stateless (no connection) |

**JWT claims:**
- `aud` (audience): from config `JITSI_JWT_AUD`
- `iss` (issuer): from config `JITSI_JWT_ISS`
- `sub` (subject): fixed tenant/domain from `JITSI_JWT_SUB` (never the wildcard `*`)
- `room`: booking UID
- `nbf`: booking start_time - 5 min buffer
- `exp` (expiration): booking end_time + 5 min buffer
- `context.user`: `{name, email, role, moderator}` — `moderator: true` for the organizer only

**Failure modes:**
- **Invalid JWT secret:** JWT tokens generated but will be rejected by Jitsi server. No local validation; failures only discovered when clients attempt to join meeting.
- **JWT library not available:** Service fails to start (import error).

Reference: `config.py`, `controllers/meeting.py` (no adapters/jitsi.py — JWT is minted in the meeting controller)

---

### Shortify URL Shortening Service

| Property | Value |
|----------|-------|
| Role | Shorten long meeting URLs |
| Protocol | HTTPS REST |
| Endpoint | `POST /api/shorten` (implied) |
| Auth | API key in request body or header (config-dependent) |
| Config | `SHORTENER_URL`, `SHORTENER_API_KEY` (optional) |
| Client | httpx `AsyncClient` (timeout: inherited from EVENTS_TIMEOUT_SECONDS) |
| Connection lifetime | App-scoped |

**Request/Response:**
- Request: `POST /api/shorten` with JSON `{"url": "..."}` or similar
- Response 200: `{"short_url": "..."}`
- Response 4xx/5xx: URL shortening failed

**Failure modes:**
- **Invalid API key:** 401/403 responses. All shortening requests fail.
- **Timeout (>5s):** httpx raises `TimeoutException`. Caught by `ShortenController`, meeting URL generation fails.
- **HTTP 429 / rate limit:** Shortening is throttled. Retry needed.
- **Shortify fully down:** URLs cannot be shortened; bookings may be rejected or long URLs stored instead.

Reference: `config.py:43-45`, `adapters/shortener.py:1-80`, `controllers/meeting.py`

---

## Provides To

### RabbitMQ (Published Events)

This service publishes the following events back to RabbitMQ via event-receiver:

| Event Type | Queue (implicit routing) | Recipients | Purpose |
|---|---|---|---|
| `booking.rejected` | `events.booking.lifecycle` | downstream (event-saver, event-notifier) | Constraint violation audit |
| `notification.send_requested` | `events.notification.commands` | event-notifier | Notify client/organizer on lifecycle changes or reminder |
| `meeting.url_created` | `events.notification.delivery` | event-saver (audit) | Audit: meeting URL generated |
| `meeting.url_deleted` | `events.notification.delivery` | event-saver (audit) | Audit: meeting URL revoked |
| `chat.created` | `events.notification.delivery` | event-saver (audit) | Audit: chat channel created |
| `chat.deleted` | `events.notification.delivery` | event-saver (audit) | Audit: chat channel deleted |
| `booking.reminder_sent` | `events.booking.lifecycle` | event-saver, event-notifier | Reminder was triggered |

Reference: `controllers/booking.py:100-150`, `adapters/events.py`

---

## Dependency Failure Impact Matrix

| Dependency | Impact on service | Recovery | Data Loss? |
|-----------|-------------------|----------|-----------|
| **RabbitMQ down** | No new events consumed; scheduler continues but cannot emit reminders | Automatic on reconnect | No; messages queued in RabbitMQ |
| **Cal.com DB down** | Cannot read bookings or update state; all event handlers fail | Automatic on pool reconnect; messages requeued | No; queries roll back |
| **event-receiver down** | Audit/notification events cannot be published | Automatic; messages requeue if publish fails | No; events stay in memory, requeue on timeout |
| **GetStream down** | Chat creation fails; bookings without chats | Manual recovery needed if partial chat creation | Partial: chat records in GetStream may be inconsistent |
| **Shortify down** | Meeting URL shortening fails; could fallback to long URL | Manual or automatic with fallback logic | No; full URL available as fallback |
| **Jitsi auth misconfigured** | JWT tokens generated but rejected by Jitsi at join time | Manual: update JWT config | No; tokens are regenerated per meeting |
| **All deps healthy** | Normal operation: ~500ms per event (DB reads, GetStream, Shortify, HTTP publish) | -- | No |

---

## Configuration Checklist

Before deploying event-booking, ensure:

- [ ] `CALCOM_POSTGRES_DSN` is set and Cal.com database is accessible
- [ ] `RABBIT_URL` points to a running RabbitMQ instance
- [ ] `EVENTS_ENDPOINT_URL` points to a running event-receiver instance
- [ ] `EVENTS_API_KEY` is valid in event-receiver's configuration
- [ ] `CHAT_API_KEY` and `CHAT_API_SECRET` are valid GetStream credentials
- [ ] `CHAT_USER_ID_ENCRYPTION_KEY` matches what GetStream has on file
- [ ] `JITSI_JWT_SECRET`, `JITSI_JWT_AUD`, `JITSI_JWT_ISS` are correct for your Jitsi deployment
- [ ] `SHORTENER_URL` and `SHORTENER_API_KEY` (if required) are correct
- [ ] `MEETING_HOST_URL` matches your Jitsi server's public URL
- [ ] Cal.com schema (`bookings`, `attendees` tables) exists and is accessible
- [ ] If `IS_ENABLE_BOOKING_CONSTRAINTS=true`, constraint analyzer configuration is reviewed

---

## Network Diagram

```
┌──────────────────┐
│  event-booking   │
└────────┬─────────┘
         │
    ┌────┴────┬────────────┬─────────────┬──────────┬──────────┐
    │          │            │             │          │          │
    v          v            v             v          v          v
 RabbitMQ  Cal.com DB  event-receiver  GetStream  Jitsi JWT  Shortify
  (AMQP)   (asyncpg)     (HTTP)         (HTTPS)    (local)    (HTTPS)
```
