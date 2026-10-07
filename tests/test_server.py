import asyncio
import base64
from pathlib import Path

import pytest
from mcp.cli.cli import _import_server
from mcp.server.mcpserver.exceptions import ToolError

from immich_mcp.client import Asset, ImmichClient, ImmichError, Location, RecentAssetsPage, ServerInfo
from immich_mcp.server import mcp


def test_only_requested_tools_are_registered() -> None:
    assert [tool.name for tool in mcp._tool_manager.list_tools()] == ["get_server_info", "get_recent_assets", "search_assets"]


def test_mcp_dev_can_import_server_file() -> None:
    server_file = Path(__file__).resolve().parents[1] / "src" / "immich_mcp" / "server.py"
    imported = _import_server(server_file)
    assert [tool.name for tool in imported._tool_manager.list_tools()] == ["get_server_info", "get_recent_assets", "search_assets"]


def test_get_server_info_returns_small_result(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://localhost:2283\nIMMICH_API_KEY=secret\n")

    async def fake_get_server_info(_self: ImmichClient) -> ServerInfo:
        return ServerInfo(version="v3.2.0", build="release")

    monkeypatch.setattr(ImmichClient, "get_server_info", fake_get_server_info)
    result = asyncio.run(mcp.call_tool("get_server_info", {}))
    assert result.structured_content == {"version": "v3.2.0", "build": "release"}


def test_recent_assets_tool_returns_metadata_and_native_image(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://localhost:2283\nIMMICH_API_KEY=secret\n")

    async def fake_recent(_self: ImmichClient, **kwargs) -> RecentAssetsPage:
        assert kwargs["limit"] == 5
        assert kwargs["include_thumbnail"] is True
        return RecentAssetsPage([
            Asset("asset-1", "a.jpg", "image", "2026-10-07T02:00:00Z",
                  Location("Shanghai", None, "China", 31.2, 121.4), "included",
                  b"\xff\xd8\xffimage", "image/jpeg"),
            Asset("asset-2", None, "video", None, None, "unavailable", duration_ms=123456),
        ], "next-token")

    monkeypatch.setattr(ImmichClient, "get_recent_assets", fake_recent)
    result = asyncio.run(mcp.call_tool("get_recent_assets", {}))
    assert result.structured_content["next_page_token"] == "next-token"
    assert result.structured_content["assets"][0]["thumbnail_content_index"] == 1
    assert result.structured_content["assets"][1]["thumbnail_content_index"] is None
    assert [asset["type"] for asset in result.structured_content["assets"]] == ["image", "video"]
    assert "duration_ms" not in result.structured_content["assets"][0]
    assert result.structured_content["assets"][1]["duration_ms"] == 123456
    assert len(result.content) == 2
    assert result.content[1].type == "image"
    assert base64.b64decode(result.content[1].data) == b"\xff\xd8\xffimage"


def test_recent_assets_tool_reports_thumbnail_permission_without_failing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://localhost:2283\nIMMICH_API_KEY=secret\n")

    async def fake_recent(_self: ImmichClient, **_kwargs) -> RecentAssetsPage:
        return RecentAssetsPage([
            Asset("asset-1", "clip.mp4", "video", "2026-10-07T02:00:00Z", None, "permission_denied"),
        ], None)

    monkeypatch.setattr(ImmichClient, "get_recent_assets", fake_recent)
    result = asyncio.run(mcp.call_tool("get_recent_assets", {}))
    assert result.structured_content["assets"][0]["thumbnail_status"] == "permission_denied"
    assert result.structured_content["assets"][0]["duration_ms"] is None
    assert result.structured_content["warnings"] == ["Thumbnails need the asset.view API key permission."]
    assert len(result.content) == 1


def test_search_assets_tool_reuses_asset_output_and_images(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://localhost:2283\nIMMICH_API_KEY=secret\n")

    async def fake_search(_self: ImmichClient, **kwargs) -> RecentAssetsPage:
        assert kwargs["query"] == "a dog running"
        assert kwargs["media_type"] == "video"
        return RecentAssetsPage([
            Asset("asset-1", "dog.mp4", "video", "2026-10-07T02:00:00Z", None,
                  "included", b"\xff\xd8\xffimage", "image/jpeg", 2000),
        ], None)

    monkeypatch.setattr(ImmichClient, "search_assets", fake_search)
    result = asyncio.run(mcp.call_tool("search_assets", {"query": "a dog running", "media_type": "video"}))
    assert result.structured_content["assets"][0]["type"] == "video"
    assert result.structured_content["assets"][0]["duration_ms"] == 2000
    assert result.structured_content["assets"][0]["thumbnail_content_index"] == 1
    assert result.content[1].type == "image"


def test_search_assets_tool_handles_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://localhost:2283\nIMMICH_API_KEY=secret\n")

    async def fake_search(_self: ImmichClient, **_kwargs) -> RecentAssetsPage:
        return RecentAssetsPage([], None)

    monkeypatch.setattr(ImmichClient, "search_assets", fake_search)
    result = asyncio.run(mcp.call_tool("search_assets", {"query": "unlikely scene"}))
    assert result.structured_content == {"assets": [], "next_page_token": None}
    assert len(result.content) == 1


def test_search_assets_tool_reports_safe_api_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://localhost:2283\nIMMICH_API_KEY=secret\n")

    async def fake_search(_self: ImmichClient, **_kwargs) -> RecentAssetsPage:
        raise ImmichError("Immich search returned HTTP 403")

    monkeypatch.setattr(ImmichClient, "search_assets", fake_search)
    with pytest.raises(ToolError, match="HTTP 403") as exc:
        asyncio.run(mcp.call_tool("search_assets", {"query": "dog"}))
    assert "secret" not in str(exc.value)
