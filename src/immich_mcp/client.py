"""Minimal async client for read-only Immich requests."""

from dataclasses import dataclass

import httpx

from .config import Config


class ImmichError(Exception):
    """A safe, user-facing Immich request error."""


@dataclass(frozen=True)
class ServerInfo:
    version: str
    build: str | None = None


class ImmichClient:
    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=config.api_url.rstrip("/") + "/",
            headers={"x-api-key": config.api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(10.0),
            transport=transport,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "ImmichClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._http.aclose()

    async def get_server_info(self) -> ServerInfo:
        try:
            response = await self._http.get("server/about")
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ImmichError(f"Immich returned HTTP {exc.response.status_code}") from None
        except httpx.RequestError:
            raise ImmichError("Could not connect to Immich") from None
        except ValueError:
            raise ImmichError("Immich returned invalid JSON") from None

        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            raise ImmichError("Immich returned unexpected server information")
        build = data.get("build")
        return ServerInfo(version=data["version"], build=build if isinstance(build, str) else None)
