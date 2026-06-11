"""Tests for SqlExecutor rollback-on-error behavior."""

from unittest.mock import AsyncMock

import pytest

from event_booking.adapters.sql import SqlExecutor


class TestRollbackOnError:
    async def test_execute_rolls_back_on_failure(self) -> None:
        session = AsyncMock()
        session.execute.side_effect = RuntimeError("boom")
        executor = SqlExecutor(session)

        with pytest.raises(RuntimeError):
            await executor.execute("UPDATE x SET y = 1", {})

        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()

    async def test_execute_in_transaction_rolls_back_on_failure(self) -> None:
        session = AsyncMock()
        session.execute.side_effect = [None, RuntimeError("boom")]
        executor = SqlExecutor(session)

        with pytest.raises(RuntimeError):
            await executor.execute_in_transaction([("A", {}), ("B", {})])

        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()

    async def test_execute_commits_on_success(self) -> None:
        session = AsyncMock()
        executor = SqlExecutor(session)

        await executor.execute("UPDATE x SET y = 1", {})

        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
