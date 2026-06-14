"""Shared pytest fixtures for event-booking tests."""

import os

os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from unittest.mock import AsyncMock, MagicMock

import pytest

from event_booking.dtos import ConstraintsResult


class FakeContainer:
    """Minimal stand-in for dishka AsyncContainer: every get() returns the same object."""

    def __init__(self, resolved: object) -> None:
        self._resolved = resolved
        self.entered_scopes = 0

    def __call__(self) -> FakeContainer:
        return self

    async def __aenter__(self) -> FakeContainer:
        self.entered_scopes += 1
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def get(self, _dependency: type) -> object:
        return self._resolved


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


@pytest.fixture
def mock_blacklist_checker() -> AsyncMock:
    mock = AsyncMock()
    mock.is_blacklisted = AsyncMock(return_value=False)
    return mock
