"""Library statistics client and MCP Resource tests."""

import asyncio
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ResourceError

from immich_mcp.client import ImmichClient, ImmichError, LibraryStats
from immich_mcp.config import Config
from immich_mcp.server import mcp


CONFIG = Config("http://localhost:2283/api", "secret")
STATS = {"total": 12, "images": 8, "videos": 4, "ignored": "private"}
USER_ID = "11111111-1111-4111-8111-111111111111"
LIBRARY_ID = "22222222-2222-4222-8222-222222222222"
OTHER_LIBRARY_ID = "33333333-3333-4333-8333-333333333333"
SECOND_LIBRARY_ID = "44444444-4444-4444-8444-444444444444"
USER = {"id": USER_ID, "quotaUsageInBytes": 500_000_000, "email": "private@example.com"}


def run_client(handler):
    async def run():
        async with ImmichClient(CONFIG, transport=httpx.MockTransport(handler)) as client:
            return await client.get_library_stats()

    return asyncio.run(run())


def test_library_stats_includes_owned_external_usage_without_fetching_assets() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["x-api-key"] == "secret"
        paths.append(request.url.path)
        if request.url.path == "/api/assets/statistics":
            return httpx.Response(200, json=STATS)
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json=USER)
        if request.url.path == "/api/libraries":
            return httpx.Response(200, json=[
                {"id": LIBRARY_ID, "ownerId": USER_ID, "importPaths": ["/private/photos"]},
                {"id": OTHER_LIBRARY_ID, "ownerId": "another-user"},
                {"id": SECOND_LIBRARY_ID, "ownerId": USER_ID},
            ])
        if request.url.path == f"/api/libraries/{LIBRARY_ID}/statistics":
            return httpx.Response(200, json={"usage": 1_500_000_000, "photos": 8, "videos": 4})
        if request.url.path == f"/api/libraries/{SECOND_LIBRARY_ID}/statistics":
            return httpx.Response(200, json={"usage": 500_000_000})
        raise AssertionError("Unexpected endpoint")

    assert run_client(handler) == LibraryStats(12, 8, 4, 2_500_000_000, "2.50 GB")
    assert paths == ["/api/assets/statistics", "/api/users/me", "/api/libraries",
                     f"/api/libraries/{LIBRARY_ID}/statistics",
                     f"/api/libraries/{SECOND_LIBRARY_ID}/statistics"]


def test_zero_upload_usage_does_not_hide_external_library_size() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/assets/statistics":
            return httpx.Response(200, json=STATS)
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json={**USER, "quotaUsageInBytes": 0})
        if request.url.path == "/api/libraries":
            return httpx.Response(200, json=[{"id": LIBRARY_ID, "ownerId": USER_ID}])
        return httpx.Response(200, json={"usage": 2_000_000_000})

    assert run_client(handler).total_storage_bytes == 2_000_000_000


def test_empty_library_has_zero_counts_and_size() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/assets/statistics":
            payload = {"total": 0, "images": 0, "videos": 0}
        elif request.url.path == "/api/users/me":
            payload = {"id": USER_ID, "quotaUsageInBytes": 0}
        else:
            payload = []
        return httpx.Response(200, json=payload)

    assert run_client(handler) == LibraryStats(0, 0, 0, 0, "0.00 GB")


@pytest.mark.parametrize("stats, user", [
    ({"total": 1, "images": 1}, USER),
    ({"total": 1, "images": True, "videos": 0}, USER),
    ({"total": 1, "images": 1, "videos": 1}, USER),
    (STATS, {**USER, "quotaUsageInBytes": None}),
    (STATS, {**USER, "quotaUsageInBytes": -1}),
])
def test_malformed_statistics_are_safe_errors(stats: object, user: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/assets/statistics":
            return httpx.Response(200, json=stats)
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json=user)
        return httpx.Response(200, json=[])

    with pytest.raises(ImmichError) as caught:
        run_client(handler)
    assert caught.value.code == "unexpected_response"
    assert "private" not in str(caught.value)


def test_invalid_json_is_safe_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"broken") if request.url.path.endswith("statistics") else httpx.Response(200, json=USER)

    with pytest.raises(ImmichError) as caught:
        run_client(handler)
    assert caught.value.code == "invalid_json"


@pytest.mark.parametrize("libraries, library_stats", [
    ({"items": []}, {"usage": 10}),
    ([{"id": "bad/id", "ownerId": USER_ID}], {"usage": 10}),
    ([{"id": LIBRARY_ID, "ownerId": USER_ID}], {"usage": None}),
    ([{"id": LIBRARY_ID, "ownerId": USER_ID}], {"usage": -1}),
])
def test_invalid_external_library_data_never_produces_a_partial_total(
    libraries: object, library_stats: object,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/assets/statistics":
            return httpx.Response(200, json=STATS)
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json=USER)
        if request.url.path == "/api/libraries":
            return httpx.Response(200, json=libraries)
        return httpx.Response(200, json=library_stats)

    with pytest.raises(ImmichError) as caught:
        run_client(handler)
    assert caught.value.code == "unexpected_response"


def test_external_library_permission_failure_is_not_reported_as_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/assets/statistics":
            return httpx.Response(200, json=STATS)
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json=USER)
        return httpx.Response(403)

    with pytest.raises(ImmichError) as caught:
        run_client(handler)
    assert caught.value.code == "permission_denied"
    assert caught.value.status == 403


def test_resource_is_registered_once_and_returns_json(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://localhost:2283\nIMMICH_API_KEY=secret\n")

    async def fake_stats(_self: ImmichClient) -> LibraryStats:
        return LibraryStats(12, 8, 4, 2_500_000_000, "2.50 GB")

    monkeypatch.setattr(ImmichClient, "get_library_stats", fake_stats)
    resources = asyncio.run(mcp.list_resources())
    assert [str(resource.uri) for resource in resources] == ["immich://library/stats"]
    assert resources[0].mime_type == "application/json"
    contents = asyncio.run(mcp.read_resource("immich://library/stats"))
    assert len(contents) == 1
    assert contents[0].mime_type == "application/json"
    assert json.loads(contents[0].content) == {
        "total_assets": 12, "image_count": 8, "video_count": 4,
        "total_storage_bytes": 2_500_000_000, "total_storage_gb": "2.50 GB",
    }
    assert "private@example.com" not in contents[0].content


def test_resource_reports_permission_failure_without_secret(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://localhost:2283\nIMMICH_API_KEY=secret\n")

    async def denied(_self: ImmichClient) -> LibraryStats:
        raise ImmichError("Immich asset statistics returned HTTP 403", code="permission_denied", status=403)

    monkeypatch.setattr(ImmichClient, "get_library_stats", denied)
    with pytest.raises(ResourceError) as caught:
        asyncio.run(mcp.read_resource("immich://library/stats"))
    details = json.loads(str(caught.value))
    assert details == {"code": "permission_denied", "message": "Immich asset statistics returned HTTP 403",
                       "retryable": False, "status": 403}
    assert "secret" not in str(caught.value)
