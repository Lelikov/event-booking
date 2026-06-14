"""Structlog configuration for event-booking service."""

import logging
import sys

import structlog

from event_booking.telemetry import add_otel_trace_context


def setup_logging(log_level: str = "INFO", *, json: bool = False) -> None:
    """Configure structlog with optional JSON or console rendering."""
    level = logging.getLevelName(log_level.upper())

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_otel_trace_context,
            structlog.stdlib.add_log_level,
            # NOTE: structlog.stdlib.add_logger_name is incompatible with
            # PrintLoggerFactory (PrintLogger has no .name) — it crashes on
            # the first log call. Logger names are not emitted.
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )
