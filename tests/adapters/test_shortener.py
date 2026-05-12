"""Tests for UrlShortenerAdapter."""

import pytest

from event_booking.adapters.shortener import UrlShortenerAdapter


class TestCreateUrl:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self) -> None:
        adapter = UrlShortenerAdapter(base_url="http://short.test", api_key=None)
        result = await adapter.create_url("http://long.url", 999999.0, 0.0, "ext-1")
        assert result is None
