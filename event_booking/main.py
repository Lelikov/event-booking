"""FastAPI application entry point for event-booking service."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from dishka import AsyncContainer, make_async_container
from event_schemas.queues import BOOKING_LIFECYCLE_BOOKING_QUEUE, USER_EMAIL_BOOKING_QUEUE
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from faststream.rabbit import RabbitBroker, RabbitExchange
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from event_booking import metrics
from event_booking.config import Settings
from event_booking.consumer import BookingConsumer, ensure_dead_letter_topology
from event_booking.ioc import AppProvider
from event_booking.logger import setup_logging
from event_booking.scheduler import ReminderScheduler
from event_booking.telemetry import instrument_asyncpg, instrument_fastapi, setup_tracing

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    container = make_async_container(AppProvider())
    app.state.container = container
    settings = await container.get(Settings)

    setup_logging(log_level=settings.log_level, json=not settings.debug)

    broker = await container.get(RabbitBroker)
    exchange = await container.get(RabbitExchange)
    consumer = await container.get(BookingConsumer)
    scheduler = await container.get(ReminderScheduler)

    consumer.register(broker, exchange, BOOKING_LIFECYCLE_BOOKING_QUEUE)
    consumer.register_user_email(broker, exchange, USER_EMAIL_BOOKING_QUEUE)

    await broker.start()
    logger.info("RabbitMQ broker started", queue=BOOKING_LIFECYCLE_BOOKING_QUEUE.name)

    await ensure_dead_letter_topology(broker, BOOKING_LIFECYCLE_BOOKING_QUEUE)
    await ensure_dead_letter_topology(broker, USER_EMAIL_BOOKING_QUEUE)

    scheduler_task = asyncio.create_task(scheduler.run_forever())
    logger.info("Reminder scheduler started")

    try:
        yield
    finally:
        scheduler.stop()
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task

        await broker.close()
        logger.info("RabbitMQ broker closed")

        await container.close()
        logger.info("DI container closed")


app = FastAPI(title="event-booking", lifespan=lifespan)
setup_tracing()
instrument_fastapi(app)
instrument_asyncpg()

READY_CHECK_TIMEOUT_SECONDS = 5.0
READY_CHECK_QUERY = "select 1"


async def _check_database(container: AsyncContainer) -> bool:
    """Verify cal.com PostgreSQL connectivity with a SELECT 1."""
    try:
        engine = await container.get(AsyncEngine)
        async with engine.connect() as connection:
            await connection.execute(text(READY_CHECK_QUERY))
    except Exception:
        logger.exception("Readiness check failed: cal.com database unreachable")
        return False
    return True


async def _check_rabbit(container: AsyncContainer) -> bool:
    """Verify the RabbitMQ connection is alive via broker ping."""
    try:
        broker = await container.get(RabbitBroker)
        return await broker.ping(timeout=READY_CHECK_TIMEOUT_SECONDS)
    except Exception:
        logger.exception("Readiness check failed: RabbitMQ unreachable")
        return False


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: the process is up and serving HTTP. No dependency calls."""
    return {"status": "ok"}


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    """Prometheus exposition endpoint (consumer RED + business counters)."""
    return metrics.metrics_response()


@app.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness probe: verifies cal.com PostgreSQL and the RabbitMQ connection."""
    container: AsyncContainer = request.app.state.container
    checks = {
        "database": await _check_database(container),
        "rabbitmq": await _check_rabbit(container),
    }
    if not all(checks.values()):
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
    return JSONResponse(status_code=200, content={"status": "ready", "checks": checks})
