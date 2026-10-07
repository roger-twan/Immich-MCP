import asyncio

import httpx
import pytest

from immich_mcp.client import ImmichClient, ImmichError, ServerInfo
from immich_mcp.config import Config


def test_get_server_info_sends_key_and_parses_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://localhost:2283/api/server/about"
        assert request.method == "GET"
        assert request.headers["x-api-key"] == "secret"
        return httpx.Response(200, json={"version": "v3.2.0", "build": "release", "licensed": False})

    async def run() -> ServerInfo:
        async with ImmichClient(Config("http://localhost:2283/api", "secret"), transport=httpx.MockTransport(handler)) as client:
            return await client.get_server_info()

    assert asyncio.run(run()) == ServerInfo(version="v3.2.0", build="release")


@pytest.mark.parametrize("response", [httpx.Response(401), httpx.Response(403)])
def test_get_server_info_rejects_auth_errors_without_leaking_key(response: httpx.Response) -> None:
    async def run() -> None:
        async with ImmichClient(Config("http://localhost:2283/api", "secret"), transport=httpx.MockTransport(lambda _: response)) as client:
            await client.get_server_info()

    with pytest.raises(ImmichError, match="HTTP") as exc:
        asyncio.run(run())
    assert "secret" not in str(exc.value)


def test_get_server_info_rejects_unexpected_response() -> None:
    async def run() -> None:
        async with ImmichClient(Config("http://localhost:2283/api", "secret"), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"other": True}))) as client:
            await client.get_server_info()

    with pytest.raises(ImmichError, match="unexpected"):
        asyncio.run(run())
