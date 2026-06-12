"""Blacklist checker protocol."""

from typing import Protocol


class IBlacklistChecker(Protocol):
    async def is_blacklisted(self, email: str) -> bool: ...
