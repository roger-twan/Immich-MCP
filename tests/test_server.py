import asyncio
from pathlib import Path

import pytest
from mcp.cli.cli import _import_server

from immich_mcp.client import ImmichClient, ServerInfo
from immich_mcp.server import mcp


def test_only_get_server_info_is_registered() -> None:
    assert [tool.name for tool in mcp._tool_manager.list_tools()] == ["get_server_info"]


def test_mcp_dev_can_import_server_file() -> None:
    server_file = Path(__file__).resolve().parents[1] / "src" / "immich_mcp" / "server.py"
    imported = _import_server(server_file)
    assert [tool.name for tool in imported._tool_manager.list_tools()] == ["get_server_info"]


def test_get_server_info_returns_small_result(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://localhost:2283\nIMMICH_API_KEY=secret\n")

    async def fake_get_server_info(_self: ImmichClient) -> ServerInfo:
        return ServerInfo(version="v3.2.0", build="release")

    monkeypatch.setattr(ImmichClient, "get_server_info", fake_get_server_info)
    result = asyncio.run(mcp.call_tool("get_server_info", {}))
    assert result.structured_content == {"version": "v3.2.0", "build": "release"}
