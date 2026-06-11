# event-booking — Audit Findings

## audit-v2 (2026-06-11)

Full audit of the service; all findings fixed (history extracted from the monorepo root branch `feat/event-booking-service` into this standalone repo)
unless marked otherwise. Cross-references: `../../docs/audit/v2/findings/event-booking.json`,
`../../docs/audit/v2/fixes/event-booking.json`, `../../docs/audit/v2/CONTRACT_DECISIONS.md`.

### CRITICAL

| Finding | Resolution |
|---|---|
| APP-scoped AsyncSession shared by concurrent messages and the scheduler | Fixed (b039979): per-message REQUEST-scoped sessions, rollback on error, graceful shutdown |
| Constraints analyzer counted the new booking against itself — every booking rejected and DELETED | Fixed (8a9f469): history query excludes the current booking id; analyzer filters defensively; rejection now marks cal.com `status='rejected'` (never DELETE) |
| event-booking and event-saver competed on the same queue | Fixed (3def0f7, contracts wave): own queue `events.booking.lifecycle.booking` from `event_schemas.queues` |
| Queue declared with conflicting x-dead-letter-exchange args (406 at startup) | Fixed (3def0f7): canonical `QueueSpec.arguments` everywhere |

### HIGH

| Finding | Resolution |
|---|---|
| Handler exceptions dead-lettered into a missing exchange — events lost | Fixed (3def0f7): `ensure_dead_letter_topology` declares `events.dlx` + per-queue DLQ (24h TTL) idempotently |
| Naive cal.com `timestamp(3)` vs aware datetimes crashed scheduler and constraints | Fixed (8a9f469): db adapter is the tz boundary — rows leave aware UTC, bind params converted to naive UTC |
| No saga/compensation on partial failure (orphaned chat etc.) | Fixed (540191b): idempotent-resume model — every step idempotent, failures propagate, DLQ replay resumes without duplicates |
| Client received the ORGANIZER's personal meeting URL | Fixed (540191b): per-recipient notification commands; each participant gets only their own tokenized URL |
| GetStream chat JWT never expired (`expiration` kwarg became a claim) | Fixed (0e09e39): `create_token(..., exp=...)` |
| Reminder scheduler guaranteed duplicates (10-min window / 5-min poll, no cursor) | Fixed (f977cd1): persistent `bookingReminderSentAt` marker in cal.com `Booking.metadata` + deterministic dedupe keys |
| Published payloads drifted from event-schemas models | Fixed (3def0f7, contracts wave): canonical payloads for meeting.url_*, chat.*, booking.rejected |
| Consumer read contract fields from top-level CloudEvent data instead of the `{original, normalized}` envelope | Fixed (3def0f7): `unwrap_payload` |

### MEDIUM

| Finding | Resolution |
|---|---|
| No idempotency on redelivery (duplicate welcomes/notifications) | Fixed (540191b): ce-id-scoped deterministic dedupe keys (UUIDv5 CloudEvent ids); welcome skipped when channel has messages |
| Reassignment failed on soft-deleted GetStream channels | Fixed (0e09e39 + 540191b): hard delete before recreation |
| Reschedule orphaned the old uid's chat and short URLs | Fixed (540191b): old chat deleted via `previous_booking_uid` (fallback `fromReschedule`); short URLs moved to the new uid |
| Multi-attendee bookings returned an arbitrary attendee row | Fixed (8a9f469): `DISTINCT ON (b.id) ... ORDER BY a.id` — deterministic first attendee |
| EventPublisher ignored HTTP status, fire-and-forget | Fixed (e425a1d): raises `EventPublishError` on non-2xx/transport errors |
| `EVENTS_ENDPOINT_URL=None` silently disabled all output | Fixed (e425a1d): required setting; publisher refuses empty endpoint |
| Sync GetStream SDK blocked the event loop; no timeout | Fixed (0e09e39): `asyncio.to_thread` offload; `CHAT_TIMEOUT_SECONDS` |
| `update_booking_video_url` dead code; reschedule notifications lacked meeting_url | Fixed (540191b): client short URL written to cal.com `metadata.videoCallUrl` (client-role token only — safe to surface in cal.com); reschedule notifications carry per-recipient URLs |
| Constraint rejection hard-DELETEd cal.com rows | Fixed (8a9f469): `status='rejected'` + `rejectionReason` update instead |
| Scheduler never emitted `booking.reminder_sent` | Fixed (f977cd1): emitted with canonical payload, routed to `events.booking.lifecycle` |
| Missing tests for riskiest paths | Fixed: reassignment, constraint self-inclusion, naive datetimes, dedupe keys, DI wiring, queue-spec contract tests added |

### LOW

| Finding | Resolution |
|---|---|
| Deterministic AES-CBC with fixed zero IV for chat user IDs | Fixed (0e09e39): AES-GCM with deterministic HMAC-derived nonce (SIV-style); format compatible with event-receiver's decoder |
| `ACTIVE_STATUSES` contained invalid `'rescheduled'` status | Fixed (8a9f469): `accepted`/`pending`/`awaiting_host` |
| Jitsi JWT `role` not a recognized claim; `sub: '*'` wildcard | Fixed (540191b): `context.user.moderator` for the organizer; `sub` from required `JITSI_JWT_SUB` |
| No CLAUDE.md; root service table omits event-booking | CLAUDE.md added; root CLAUDE.md table is outside this fixer's staging scope (tracked in fixes JSON) |
| Documentation drift (wrong queues/endpoint/auth/requeue claims/nonexistent files) | Fixed: docs synced in this audit pass |
| Dead code and convention violations (unused DB methods, mutable BookingDTO, `object`-typed chat controller) | Fixed across 8a9f469 / 0e09e39 / 540191b |
| Default RabbitMQ DSN embedded guest:guest | Fixed (e425a1d): `RABBIT_URL` is required |

### Delivery semantics (post-audit)

At-least-once consumption; failures reject to `events.booking.lifecycle.booking.dlq`
(24h TTL, manual replay). All side effects are idempotent and all published events
carry deterministic ids when triggered by a consumed message, so replays and
redeliveries do not duplicate chats, URLs, or notifications.
