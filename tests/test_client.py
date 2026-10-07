import asyncio
import json

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


def test_recent_assets_search_and_thumbnail() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-api-key"] == "secret"
        if request.url.path.endswith("/search/metadata"):
            body = json.loads(request.content)
            assert body == {
                "filter": {"type": {"in": ["IMAGE", "VIDEO"]}, "visibility": {"eq": "timeline"},
                           "trashedAt": {"eq": None},
                           "takenAt": {"gte": "2026-10-01T00:00:00.000Z", "lt": "2026-10-08T00:00:00.000Z"}},
                "orderBy": {"field": "fileCreatedAt", "direction": "desc"}, "size": 5,
                "withExif": True,
            }
            return httpx.Response(200, json={"assets": {"items": [
                {"id": "11111111-1111-4111-8111-111111111111", "originalFileName": "a.jpg",
                 "type": "IMAGE", "duration": None, "fileCreatedAt": "2026-10-07T02:00:00Z",
                 "exifInfo": {"city": "Shanghai", "country": "China", "latitude": 31.2, "longitude": 121.4},
                 "originalPath": "/private/photo.jpg"},
                {"id": "22222222-2222-4222-8222-222222222222", "type": "VIDEO", "duration": 123456, "exifInfo": None},
            ], "nextCursor": "cursor-2"}})
        assert request.url.path.endswith("/assets/11111111-1111-4111-8111-111111111111/thumbnail") or request.url.path.endswith("/assets/22222222-2222-4222-8222-222222222222/thumbnail")
        assert request.url.params["size"] == "thumbnail"
        if "11111111" in request.url.path:
            return httpx.Response(200, content=b"\xff\xd8\xffimage")
        return httpx.Response(404)

    async def run():
        async with ImmichClient(Config("http://localhost:2283/api", "secret"), transport=httpx.MockTransport(handler)) as client:
            return await client.get_recent_assets(start_at="2026-10-01T08:00:00+08:00", end_before="2026-10-08T08:00:00+08:00")

    page = asyncio.run(run())
    assert len(requests) == 3
    assert len(page.assets) == 2
    assert page.assets[0].filename == "a.jpg"
    assert page.assets[0].asset_type == "image"
    assert page.assets[0].duration_ms is None
    assert page.assets[0].location.city == "Shanghai"
    assert page.assets[0].thumbnail_status == "included"
    assert page.assets[0].thumbnail_mime_type == "image/jpeg"
    assert page.assets[1].filename is None
    assert page.assets[1].asset_type == "video"
    assert page.assets[1].duration_ms == 123456
    assert page.assets[1].location is None
    assert page.assets[1].thumbnail_status == "unavailable"
    assert page.next_page_token


def test_recent_assets_continuation_and_no_thumbnail() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/search/metadata")
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"assets": {"items": [], "nextCursor": "next" if len(bodies) == 1 else None}})

    async def run():
        async with ImmichClient(Config("http://localhost:2283/api", "secret"), transport=httpx.MockTransport(handler)) as client:
            first = await client.get_recent_assets(limit=3, include_thumbnail=False)
            second = await client.get_recent_assets(page_token=first.next_page_token)
            return second

    page = asyncio.run(run())
    assert len(bodies) == 2
    assert bodies[0]["size"] == bodies[1]["size"] == 3
    assert bodies[1]["cursor"] == "next"
    assert page.next_page_token is None


def test_recent_assets_keeps_metadata_when_thumbnail_permission_is_denied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search/metadata"):
            return httpx.Response(200, json={"assets": {"items": [
                {"id": "11111111-1111-4111-8111-111111111111", "originalFileName": "clip.mp4",
                 "type": "VIDEO", "fileCreatedAt": "2026-10-07T02:00:00Z"},
            ], "nextCursor": None}})
        assert request.url.path.endswith("/thumbnail")
        assert request.url.params["size"] == "thumbnail"
        return httpx.Response(403)

    async def run():
        async with ImmichClient(Config("http://localhost:2283/api", "secret"), transport=httpx.MockTransport(handler)) as client:
            return await client.get_recent_assets()

    asset = asyncio.run(run()).assets[0]
    assert asset.filename == "clip.mp4"
    assert asset.asset_type == "video"
    assert asset.duration_ms is None
    assert asset.thumbnail_status == "permission_denied"
    assert asset.thumbnail_data is None


@pytest.mark.parametrize("kwargs", [
    {"limit": 11}, {"start_at": "2026-10-01"},
    {"start_at": "2026-10-08T00:00:00Z", "end_before": "2026-10-01T00:00:00Z"},
    {"page_token": "bad-token"},
    {"page_token": ""},
])
def test_recent_assets_rejects_invalid_input(kwargs: dict) -> None:
    async def run() -> None:
        async with ImmichClient(Config("http://localhost:2283/api", "secret")) as client:
            await client.get_recent_assets(**kwargs)

    with pytest.raises(ValueError):
        asyncio.run(run())


def test_search_assets_semantic_query_with_filters_and_thumbnail() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-api-key"] == "secret"
        if request.url.path.endswith("/search/smart"):
            assert request.extensions["timeout"]["read"] == 60.0
            assert json.loads(request.content) == {
                "query": "a dog running", "size": 2, "withExif": True,
                "filter": {"type": {"eq": "VIDEO"}, "visibility": {"eq": "timeline"},
                           "trashedAt": {"eq": None},
                           "takenAt": {"gte": "2026-01-01T00:00:00.000Z"},
                           "city": {"eq": "Shanghai"}},
            }
            return httpx.Response(200, json={"assets": {"items": [
                {"id": "11111111-1111-4111-8111-111111111111", "type": "VIDEO",
                 "originalFileName": "dog.mp4", "duration": 2500,
                 "fileCreatedAt": "2026-09-01T10:00:00Z", "exifInfo": {"city": "Shanghai"}},
            ]}})
        assert request.url.path.endswith("/assets/11111111-1111-4111-8111-111111111111/thumbnail")
        assert request.url.params["size"] == "thumbnail"
        return httpx.Response(200, content=b"\xff\xd8\xffpreview")

    async def run():
        async with ImmichClient(Config("http://localhost:2283/api", "secret"), transport=httpx.MockTransport(handler)) as client:
            return await client.search_assets(query=" a dog running ", media_type="video", city=" Shanghai ",
                                              start_at="2026-01-01T08:00:00+08:00", limit=2)

    page = asyncio.run(run())
    assert len(requests) == 2
    assert page.assets[0].asset_type == "video"
    assert page.assets[0].duration_ms == 2500
    assert page.assets[0].thumbnail_status == "included"
    assert page.next_page_token is None


def test_search_assets_metadata_filters_and_cursor() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/search/metadata")
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json={"assets": {"items": [],
            "nextCursor": "cursor-2" if len(bodies) == 1 else None}})

    async def run():
        async with ImmichClient(Config("http://localhost:2283/api", "secret"), transport=httpx.MockTransport(handler)) as client:
            first = await client.search_assets(media_type="image", country="China", limit=3,
                                               include_thumbnail=False)
            second = await client.search_assets(page_token=first.next_page_token)
            return first, second

    first, second = asyncio.run(run())
    assert first.assets == second.assets == []
    assert bodies[0]["filter"]["type"] == {"eq": "IMAGE"}
    assert bodies[0]["filter"]["country"] == {"eq": "China"}
    assert bodies[0]["orderBy"] == {"field": "fileCreatedAt", "direction": "desc"}
    assert bodies[1]["cursor"] == "cursor-2"
    assert bodies[1]["size"] == 3
    assert second.next_page_token is None


@pytest.mark.parametrize("kwargs", [
    {}, {"query": " "}, {"media_type": "audio"}, {"limit": 11},
    {"city": " "}, {"page_token": "bad-token"},
    {"query": "dog", "page_token": "bad-token"},
])
def test_search_assets_rejects_invalid_input(kwargs: dict) -> None:
    async def run() -> None:
        async with ImmichClient(Config("http://localhost:2283/api", "secret")) as client:
            await client.search_assets(**kwargs)

    with pytest.raises(ValueError):
        asyncio.run(run())


def test_search_assets_reports_api_failure_without_key() -> None:
    async def run() -> None:
        async with ImmichClient(Config("http://localhost:2283/api", "secret"),
                                transport=httpx.MockTransport(lambda _: httpx.Response(403))) as client:
            await client.search_assets(query="dog")

    with pytest.raises(ImmichError, match="HTTP 403") as exc:
        asyncio.run(run())
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize(("query", "expected"), [
    ("dog", "smart search timed out after 60 seconds"),
    (None, "metadata search timed out after 10 seconds"),
])
def test_search_assets_reports_timeout_clearly(query: str | None, expected: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request timed out")

    async def run() -> None:
        async with ImmichClient(Config("http://localhost:2283/api", "secret"),
                                transport=httpx.MockTransport(handler)) as client:
            if query:
                await client.search_assets(query=query)
            else:
                await client.search_assets(media_type="image")

    with pytest.raises(ImmichError, match=expected):
        asyncio.run(run())


def test_search_assets_reports_request_error_category_without_details() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("private upstream details")

    async def run() -> None:
        async with ImmichClient(Config("http://localhost:2283/api", "secret"),
                                transport=httpx.MockTransport(handler)) as client:
            await client.search_assets(query="dog")

    with pytest.raises(ImmichError, match="RemoteProtocolError") as exc:
        asyncio.run(run())
    assert "private upstream details" not in str(exc.value)
