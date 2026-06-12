"""Prometheus metrics for event-booking.

Module-level metric objects (idiomatic for prometheus-client). Consumer RED
metrics are recorded by the RabbitMQ consumer; business counters by the owning
controllers/adapters. Exposed via GET /metrics on the health HTTP app.
"""

from time import perf_counter

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

MESSAGES_PROCESSED_TOTAL = Counter(
    "messages_processed_total",
    "Consumed RabbitMQ messages by queue, event type and outcome (ok, retried, rejected).",
    ["queue", "event_type", "outcome"],
)
MESSAGE_PROCESSING_SECONDS = Histogram(
    "message_processing_seconds",
    "Message processing duration in seconds by queue.",
    ["queue"],
)

REJECTIONS_TOTAL = Counter(
    "booking_rejections_total",
    "Bookings rejected by rejection type (constraints types or 'blacklisted').",
    ["rejection_type"],
)
BLACKLIST_CHECKS_TOTAL = Counter(
    "booking_blacklist_checks_total",
    "Blacklist checks by result: hit, miss, or fail_open (event-admin unreachable with no cache).",
    ["result"],
)
CHATS_CREATED_TOTAL = Counter(
    "booking_chats_created_total",
    "GetStream chat channels created (idempotent recreations included).",
)
MEETING_URLS_CREATED_TOTAL = Counter(
    "booking_meeting_urls_created_total",
    "Tokenized meeting short URLs created, by participant role.",
    ["role"],
)
REMINDERS_SENT_TOTAL = Counter(
    "booking_reminders_sent_total",
    "Booking reminders dispatched by the scheduler.",
)


def record_message(*, queue: str, event_type: str, outcome: str, started_at: float) -> None:
    MESSAGES_PROCESSED_TOTAL.labels(queue=queue, event_type=event_type, outcome=outcome).inc()
    MESSAGE_PROCESSING_SECONDS.labels(queue=queue).observe(perf_counter() - started_at)


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
