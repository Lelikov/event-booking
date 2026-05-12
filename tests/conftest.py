"""Shared pytest fixtures for event-booking tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from event_booking.dtos import ConstraintsResult


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_events() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_chat_controller() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_meeting_controller() -> AsyncMock:
    mock = AsyncMock()
    mock.create_meeting_url = AsyncMock(return_value="https://short.test/abc")
    return mock


@pytest.fixture
def mock_constraints_analyzer() -> MagicMock:
    mock = MagicMock()
    mock.analyze_on_create = MagicMock(return_value=ConstraintsResult(is_allowed=True))
    return mock
