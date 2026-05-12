"""Shortify URL shortener client."""

import httpx
import structlog

logger = structlog.get_logger(__name__)


class UrlShortenerAdapter:
    def __init__(self, *, base_url: str, api_key: str | None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    async def create_url(self, long_url: str, expires_at: float, not_before: float, external_id: str) -> str | None:
        if not self._api_key:
            logger.warning("Shortener API key not set, skipping")
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/urls/shorten",
                    json={
                        "long_url": long_url,
                        "expires_at": expires_at,
                        "not_before": not_before,
                        "external_id": external_id,
                    },
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                ident = data.get("ident", "")
                return f"{self._base_url}/{ident}"
        except Exception:
            logger.exception("Failed to shorten URL")
            return None

    async def get_url(self, external_id: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/api/v1/urls/external/{external_id}",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                ident = data.get("ident", "")
                return f"{self._base_url}/{ident}"
        except Exception:
            logger.exception("Failed to get URL")
            return None

    async def update_url_data(
        self,
        *,
        long_url: str,
        expires_at: float,
        not_before: float,
        new_external_id: str,
        old_external_id: str,
    ) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.patch(
                    f"{self._base_url}/api/v1/urls/external/{old_external_id}",
                    json={
                        "long_url": long_url,
                        "expires_at": expires_at,
                        "not_before": not_before,
                        "external_id": new_external_id,
                    },
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                ident = data.get("ident", "")
                return f"{self._base_url}/{ident}"
        except Exception:
            logger.exception("Failed to update URL")
            return None

    async def delete_url(self, *, external_id: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(
                    f"{self._base_url}/api/v1/urls/external/{external_id}",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return external_id
        except Exception:
            logger.exception("Failed to delete URL")
            return None
