# event-booking API Contracts

## HTTP Endpoints

This service has a minimal HTTP API:

### `GET /health`

**Purpose:** Health check endpoint for load balancers and orchestrators.

**Request:**
```
GET /health HTTP/1.1
Host: localhost:8000
```

**Response (200 OK):**
```json
{"status": "ok"}
```

**Implementation:** `main.py:59-61`

---

## RabbitMQ Consumed

### Queue

| Property | Value |
|----------|-------|
| Queue name | `events.booking.lifecycle` |
| Exchange | `events` (topic) |
| Routing key | `events.booking.lifecycle` |
| Durable | yes |
| Arguments | `x-max-priority: 10`, `x-dead-letter-exchange: {queue}.dlq` |
| Declare | `True` (queue declared by consumer on startup) |

Reference: `consumer.py:52-68`, `config.py:24`

---

### CloudEvent Format (binary mode)

Messages arrive as binary-mode CloudEvents per the CloudEvents 1.0 specification. Headers carry CE attributes; body is JSON.

**Required headers:**

| Header | Example | Source | Used for |
|--------|---------|--------|----------|
| `ce-type` | `booking.created` | Must match a key in `HANDLED_EVENTS` | Event routing |
| `ce-specversion` | `"1.0"` | CloudEvents spec version | Validation |
| `ce-id` | `"uuid-string"` | Unique event identifier | Logging, idempotency (future) |
| `ce-time` | `2026-05-13T12:00:00Z` | Event creation timestamp | Logging |
| `ce-source` | `"event-receiver"` | Source service | Logging |
| `ce-booking_id` | `"booking-uid"` | Booking identifier | Extracted to dispatch |

**Typical parsed payload (body):**
```json
{
  "booking_uid": "abc-123-def",
  "start_time": "2026-05-15T14:00:00Z",
  "end_time": "2026-05-15T15:00:00Z",
  "previous_start_time": "2026-05-15T13:00:00Z",
  "previous_organizer_email": "old@example.com",
  "cancellation_reason": "Client requested"
}
```

---

### Accepted Event Types

Only events whose `ce-type` matches a member of `HANDLED_EVENTS` are processed.
Unknown types are logged at WARNING level and the message is ACKed (skipped).

Reference: `consumer.py:15-22`

| Event Type | Handler | Expected Data Fields |
|---|---|---|
| `booking.created` | `BookingController.handle_created()` | `booking_uid` |
| `booking.rescheduled` | `BookingController.handle_rescheduled()` | `booking_uid`, `previous_start_time` (optional) |
| `booking.reassigned` | `BookingController.handle_reassigned()` | `booking_uid`, `previous_organizer_email` (optional) |
| `booking.cancelled` | `BookingController.handle_cancelled()` | `booking_uid`, `cancellation_reason` (optional) |

---

## RabbitMQ Published

### Events Published via event-receiver

After processing a booking event, event-booking publishes the following events
to event-receiver's `POST /event/cloudevents` endpoint. All events are published
with `source: "booking"` and appropriate `type` and `data` fields.

#### Constraints Violation Events

**Event Type:** `booking.rejected`

**Published when:** `IS_ENABLE_BOOKING_CONSTRAINTS=true` and constraint analysis fails.

**Payload schema:**
```json
{
  "booking_uid": "string (UUID)",
  "rejection_reasons": ["string", ...]
}
```

**Destination:** Routed by event-receiver to `events.booking.lifecycle` queue.
**Consumers:** event-saver (audit), event-notifier (via routing rules).

Reference: `controllers/booking.py:54-60`

---

#### Notification Commands

**Event Type:** `notification.send_requested`

**Published when:** After successful processing (chat + meeting URL created, or booking rescheduled/reassigned/cancelled).

**Payload schema (booking.created/rescheduled/reassigned/cancelled):**
```json
{
  "booking_uid": "string (UUID)",
  "trigger_event": "BOOKING_CREATED | BOOKING_RESCHEDULED | BOOKING_REASSIGNED | BOOKING_CANCELLED",
  "recipients": [
    {
      "user_id": "uuid",
      "role": "organizer | client"
    },
    ...
  ],
  "template_data": {
    "booking_id": "uuid",
    "start_time": "2026-05-15T14:00:00Z",
    "end_time": "2026-05-15T15:00:00Z",
    "organizer_email": "organizer@example.com",
    "client_email": "client@example.com",
    "meeting_url": "https://short.link/abc123",
    ...
  }
}
```

**Destination:** Routed by event-receiver to `events.notification.commands` queue.
**Consumer:** event-notifier (sends email/Telegram notifications).

Reference: `controllers/booking.py:100-150`, `adapters/events.py`

---

#### Audit Events

**Event Type:** `meeting.url_created`

**Published when:** After successful Jitsi JWT + Shortify URL generation.

**Payload schema:**
```json
{
  "booking_uid": "string (UUID)",
  "meeting_url": "string (shortened URL)",
  "recipient_role": "organizer | client"
}
```

**Destination:** Routed to `events.notification.delivery` queue.

Reference: `controllers/meeting.py:50-60`

---

**Event Type:** `meeting.url_deleted`

**Published when:** On booking cancellation.

**Payload schema:**
```json
{
  "booking_uid": "string (UUID)",
  "recipient_role": "organizer | client"
}
```

**Destination:** Routed to `events.notification.delivery` queue.

Reference: `controllers/booking.py:130-140`

---

**Event Type:** `chat.created`

**Published when:** After GetStream chat channel is successfully created.

**Payload schema:**
```json
{
  "booking_uid": "string (UUID)",
  "organizer_id": "uuid",
  "client_id": "uuid"
}
```

**Destination:** Routed to `events.notification.delivery` queue.

Reference: `controllers/chat.py:40-60`

---

**Event Type:** `chat.deleted`

**Published when:** On booking cancellation or reassignment.

**Payload schema:**
```json
{
  "booking_uid": "string (UUID)"
}
```

**Destination:** Routed to `events.notification.delivery` queue.

Reference: `controllers/booking.py:135-145`

---

#### Reminder Events

**Event Type:** `booking.reminder_sent`

**Published when:** Background scheduler detects a booking 55-65 minutes before start.

**Payload schema:**
```json
{
  "booking_uid": "string (UUID)",
  "trigger_event": "BOOKING_REMINDER"
}
```

**Destination:** Routed by event-receiver to `events.booking.lifecycle` queue.
**Consumers:** event-saver (audit), event-notifier (sends reminder messages).

Reference: `scheduler.py:30-80`

---

## Event Publishing Configuration

All events are published via HTTP `POST /event/cloudevents` to event-receiver.

**Authentication:**
- Header: `Authorization: Bearer {EVENTS_API_KEY}`
- Key stored in config: `EVENTS_API_KEY`

**Timeout:**
- Default: 5.0 seconds (configurable via `EVENTS_TIMEOUT_SECONDS`)

**Failure handling:**
- If event-receiver returns HTTP error or request times out, exception is logged and propagated to RabbitMQ consumer
- RabbitMQ message is nacked and requeued for retry

**Implementation:** `adapters/events.py:1-60`

---

## Error Handling

### Unknown event type in consumer

- **Behavior:** Message is ACKed and skipped. Warning logged: `"Unknown event type received, ignoring"`
- **Reference:** `consumer.py:50-51`

### Malformed CloudEvent (unparseable headers/body)

- **Behavior:** Exception raised from `from_http()`, propagates to FastStream consumer
- **Recovery:** With DLQ binding, message is dead-lettered after rejection
- **Reference:** `consumer.py:70-80`

### Booking not found in Cal.com database

- **Behavior:** Handler logs warning and returns early without processing
- **Reference:** `controllers/booking.py:45-47`, `controllers/booking.py:67-69`

### Constraint analysis determines rejection

- **Behavior:** `notification.send_requested` published with `BOOKING_REJECTED` trigger; booking deleted from database
- **Reference:** `controllers/booking.py:49-60`

### GetStream chat creation fails

- **Behavior:** Exception propagates to consumer; RabbitMQ message nacked/requeued
- **Impact:** Booking is not processed; no notification sent
- **Reference:** `controllers/chat.py:20-90`

### Meeting URL generation fails (Jitsi or Shortify)

- **Behavior:** Exception propagates to consumer; RabbitMQ message nacked/requeued
- **Impact:** Booking has no meeting URL; notification not sent
- **Reference:** `controllers/meeting.py:10-80`

### Event publishing to event-receiver fails

- **Behavior:** Exception raised; propagates to consumer; RabbitMQ message nacked/requeued
- **Impact:** Audit/notification events not published; downstream services miss update
- **Reference:** `adapters/events.py:50-60`

---

## Integration Examples

### Publishing a Notification Request

```python
# From controllers/booking.py
await self._events.send_event(
    booking_uid=booking.uid,
    event=EventType.BOOKING_CREATED,
    data={
        "trigger_event": TriggerEvent.BOOKING_CREATED.value,
        "recipients": [
            {"user_id": organizer_id, "role": RecipientRole.ORGANIZER.value},
            {"user_id": client_id, "role": RecipientRole.CLIENT.value},
        ],
        "template_data": {
            "booking_id": booking.id,
            "start_time": booking.start_time,
            "meeting_url": meeting_url,
            ...
        }
    }
)
```

### Handling Booking Created Event

```python
# From consumer.py
message = RabbitMessage.from_http(...)
ce_headers = parse_cloudevents_headers(...)
booking_uid = ce_headers.get("booking_id")
event_type = ce_headers.get("type")

await booking_consumer.dispatch(event_type, booking_uid, payload)
```

---

## Future Extensions

1. **Idempotency:** Track processed event IDs in database to prevent re-processing duplicates
2. **Dead-letter queue consumer:** Monitor DLQ for failed events and alert operators
3. **Delivery result publishing:** Publish `booking.processed` event after successful completion
4. **Metrics:** Export event processing latency and error rates to monitoring system
