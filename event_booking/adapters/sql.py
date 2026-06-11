"""Thin wrapper over SQLAlchemy AsyncSession for raw SQL."""

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession


class SqlExecutor:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fetch_one(self, query: str, values: dict) -> RowMapping | None:
        result = await self.session.execute(text(query), values)
        return result.mappings().first()

    async def fetch_all(self, query: str, values: dict) -> list[RowMapping]:
        result = await self.session.execute(text(query), values)
        return list(result.mappings().all())

    async def execute(self, query: str, values: dict) -> None:
        try:
            await self.session.execute(text(query), values)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    async def execute_in_transaction(self, statements: list[tuple[str, dict]]) -> None:
        try:
            for query, values in statements:
                await self.session.execute(text(query), values)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
