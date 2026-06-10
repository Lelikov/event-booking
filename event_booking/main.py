"""FastAPI application entry point for event-booking service."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from dishka import make_async_container
from event_schemas.queues import BOOKING_LIFECYCLE_BOOKING_QUEUE
from fastapi import FastAPI
from faststream.rabbit import RabbitBroker, RabbitExchange

from event_booking.config import Settings
from event_booking.consumer import BookingConsumer, ensure_dead_letter_topology
from event_booking.ioc import AppProvider
from event_booking.logger import setup_logging
from event_booking.scheduler import ReminderScheduler

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    container = make_async_container(AppProvider())
    settings = await container.get(Settings)

    setup_logging(log_level=settings.log_level, json=not settings.debug)

    broker = await container.get(RabbitBroker)
    exchange = await container.get(RabbitExchange)
    consumer = await container.get(BookingConsumer)
    scheduler = await container.get(ReminderScheduler)

    consumer.register(broker, exchange, BOOKING_LIFECYCLE_BOOKING_QUEUE)

    await broker.start()
    logger.info("RabbitMQ broker started", queue=BOOKING_LIFECYCLE_BOOKING_QUEUE.name)

    await ensure_dead_letter_topology(broker, BOOKING_LIFECYCLE_BOOKING_QUEUE)

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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
