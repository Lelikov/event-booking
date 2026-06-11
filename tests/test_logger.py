"""Tests for structlog configuration."""

import pytest
import structlog

from event_booking.logger import setup_logging


@pytest.fixture(autouse=True)
def _reset_structlog():
    yield
    structlog.reset_defaults()


@pytest.mark.parametrize("json", [True, False])
def test_setup_logging_does_not_crash_on_log_call(capsys, json: bool) -> None:
    # Regression: structlog.stdlib.add_logger_name crashed with
    # PrintLoggerFactory (PrintLogger has no .name) on the FIRST log line,
    # taking the whole app down at startup.
    setup_logging(log_level="INFO", json=json)

    structlog.get_logger().info("hello", queue="events.booking.lifecycle.booking")

    out = capsys.readouterr().out
    assert "hello" in out
