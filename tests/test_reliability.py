import asyncio
import json

import httpx
import pytest

from immich_mcp.client import ImmichClient, ImmichError
from immich_mcp.config import Config


CONFIG = Config("http://localhost:2283/api", "secret")
ASSET_ID = "11111111-1111-4111-8111-111111111111"


def run_server_info(handler):
    async def run():
        async with ImmichClient(CONFIG, transport=httpx.MockTransport(handler)) as client:
            return await client.get_server_info()

    return asyncio.run(run())


@pytest.mark.parametrize(("status", "code"), [(401, "authentication_failed"), (403, "permission_denied")])
def test_auth_failures_are_not_retried(status: int, code: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="upstream secret")

    with pytest.raises(ImmichError) as caught:
        run_server_info(handler)
    assert calls == 1
    assert caught.value.to_dict() == {
        "code": code, "message": f"Immich server info returned HTTP {status}",
        "retryable": False, "status": status,
    }
    assert "upstream secret" not in str(caught.value)


def test_short_rate_limit_retries_once_and_succeeds() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"version": "v3.2.0"})

    assert run_server_info(handler).version == "v3.2.0"
    assert calls == 2


def test_long_rate_limit_is_not_retried() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "30"})

    with pytest.raises(ImmichError) as caught:
        run_server_info(handler)
    assert calls == 1
    assert caught.value.to_dict() == {
        "code": "rate_limited", "message": "Immich server info returned HTTP 429",
        "retryable": True, "status": 429, "retry_after_seconds": 30.0,
    }


@pytest.mark.parametrize("status", [502, 503, 504])
def test_transient_server_errors_retry_once(status: int) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status) if calls == 1 else httpx.Response(200, json={"version": "v3.2.0"})

    assert run_server_info(handler).version == "v3.2.0"
    assert calls == 2


def test_http_500_is_reported_without_immediate_retry() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with pytest.raises(ImmichError) as caught:
        run_server_info(handler)
    assert calls == 1
    assert caught.value.code == "server_error"
    assert caught.value.retryable is True


def test_transient_connection_failure_retries_once() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("private host detail")
        return httpx.Response(200, json={"version": "v3.2.0"})

    assert run_server_info(handler).version == "v3.2.0"
    assert calls == 2


def test_connection_failure_after_retry_has_safe_code() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("private host detail")

    with pytest.raises(ImmichError) as caught:
        run_server_info(handler)
    assert calls == 2
    assert caught.value.code == "connection_error"
    assert caught.value.retryable is True
    assert "private host detail" not in str(caught.value)


def test_smart_read_timeout_is_not_retried() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private host detail")

    async def run():
        async with ImmichClient(CONFIG, transport=httpx.MockTransport(handler)) as client:
            await client.search_assets(query="dog")

    with pytest.raises(ImmichError) as caught:
        asyncio.run(run())
    assert calls == 1
    assert caught.value.code == "timeout"
    assert "60 seconds" in str(caught.value)


def test_short_read_timeout_retries_once() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("private host detail")
        return httpx.Response(200, json={"version": "v3.2.0"})

    assert run_server_info(handler).version == "v3.2.0"
    assert calls == 2


@pytest.mark.parametrize("payload, code", [
    ("not json", "invalid_json"),
    (json.dumps({"assets": {"items": "bad", "nextCursor": None}}), "unexpected_response"),
    (json.dumps({"assets": {"items": [], "nextCursor": 123}}), "unexpected_response"),
])
def test_malformed_search_response_is_a_safe_error(payload: str, code: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async def run():
        async with ImmichClient(CONFIG, transport=httpx.MockTransport(handler)) as client:
            await client.search_assets(media_type="image", include_thumbnail=False)

    with pytest.raises(ImmichError) as caught:
        asyncio.run(run())
    assert caught.value.code == code
    assert caught.value.retryable is False
    assert payload not in str(caught.value)


@pytest.mark.parametrize("item", [
    None,
    {"type": "IMAGE"},
    {"id": "not-a-uuid", "type": "IMAGE"},
    {"id": ASSET_ID},
    {"id": ASSET_ID, "type": "AUDIO"},
])
def test_malformed_required_asset_fields_are_not_silent_empty_results(item: object) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"assets": {"items": [item], "nextCursor": None}})

    async def run():
        async with ImmichClient(CONFIG, transport=httpx.MockTransport(handler)) as client:
            await client.get_recent_assets(include_thumbnail=False)

    with pytest.raises(ImmichError) as caught:
        asyncio.run(run())
    assert caught.value.code == "unexpected_response"
    assert caught.value.retryable is False


def test_thumbnail_rate_limit_and_failure_preserve_metadata() -> None:
    thumbnail_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal thumbnail_calls
        if request.url.path.endswith("/search/metadata"):
            return httpx.Response(200, json={"assets": {"items": [
                {"id": ASSET_ID, "type": "IMAGE", "originalFileName": "a.jpg"},
                {"id": "22222222-2222-4222-8222-222222222222", "type": "VIDEO", "duration": 1000},
            ], "nextCursor": None}})
        thumbnail_calls += 1
        if ASSET_ID in request.url.path:
            return httpx.Response(429, headers={"Retry-After": "30"})
        return httpx.Response(503, headers={"Retry-After": "30"})

    async def run():
        async with ImmichClient(CONFIG, transport=httpx.MockTransport(handler)) as client:
            return await client.get_recent_assets()

    page = asyncio.run(run())
    assert thumbnail_calls == 2
    assert [(asset.asset_type, asset.thumbnail_status) for asset in page.assets] == [
        ("image", "rate_limited"), ("video", "unavailable"),
    ]
    assert page.assets[0].filename == "a.jpg"
    assert page.assets[1].duration_ms == 1000


@pytest.mark.parametrize("thumbnail_response", [
    httpx.Response(302, headers={"Location": f"/api/assets/{ASSET_ID}/original"}),
    httpx.Response(200, content=b"not an image"),
    httpx.Response(200, content=b"\xff\xd8\xff" + b"a" * 1_000_000),
])
def test_bad_thumbnail_is_unavailable_without_losing_asset(
    thumbnail_response: httpx.Response,
) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/search/metadata"):
            return httpx.Response(200, json={"assets": {"items": [
                {"id": ASSET_ID, "type": "IMAGE", "originalFileName": "a.jpg"},
            ], "nextCursor": None}})
        return thumbnail_response

    async def run():
        async with ImmichClient(CONFIG, transport=httpx.MockTransport(handler)) as client:
            return await client.get_recent_assets()

    page = asyncio.run(run())
    assert len(page.assets) == 1
    assert page.assets[0].filename == "a.jpg"
    assert page.assets[0].thumbnail_status == "unavailable"
    assert page.assets[0].thumbnail_data is None
    assert paths == ["/api/search/metadata", f"/api/assets/{ASSET_ID}/thumbnail"]
