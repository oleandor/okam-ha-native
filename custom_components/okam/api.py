"""Non-blocking client for the local O-KAM native bridge API."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout


class OkamApiError(RuntimeError):
    """Bridge request failed."""


class OkamAuthError(OkamApiError):
    """Bridge rejected the API token."""


class OkamApi:
    def __init__(self, session: ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = ClientTimeout(total=15, connect=5)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        timeout = kwargs.pop("timeout", self._timeout)
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers,
                timeout=timeout,
                **kwargs,
            ) as response:
                if response.status == 401:
                    raise OkamAuthError("invalid bridge API token")
                response.raise_for_status()
                if response.content_type == "application/json":
                    return await response.json()
                return await response.read()
        except OkamAuthError:
            raise
        except (ClientError, TimeoutError) as exc:
            raise OkamApiError("unable to reach O-KAM bridge") from exc

    async def health(self) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{self.base_url}/health", timeout=self._timeout
            ) as response:
                response.raise_for_status()
                return await response.json()
        except (ClientError, TimeoutError) as exc:
            raise OkamApiError("unable to reach O-KAM bridge") from exc

    async def devices(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/devices")

    async def status(self, camera_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/cameras/{camera_id}/status")

    async def snapshot(self, camera_id: str) -> bytes:
        return await self._request(
            "GET",
            f"/api/cameras/{camera_id}/snapshot.jpg",
            timeout=ClientTimeout(total=90, connect=5),
        )

    async def configure(self, camera_id: str, idle_timeout: int) -> None:
        await self._request(
            "PATCH",
            f"/api/cameras/{camera_id}/config",
            json={"idle_timeout_seconds": idle_timeout},
        )

    async def stream_source(self, camera_id: str) -> str:
        result = await self._request("GET", f"/api/cameras/{camera_id}/stream/source")
        return str(result["stream_url"])
