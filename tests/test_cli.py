"""CLI entry point and stdio transport tests."""

import asyncio
from importlib.metadata import distribution
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from immich_mcp import server


def test_console_script_targets_the_existing_main() -> None:
    scripts = {entry.name: entry.value for entry in distribution("immich-mcp").entry_points if entry.group == "console_scripts"}
    assert scripts["immich-mcp"] == "immich_mcp.server:main"


def test_main_runs_existing_server(monkeypatch) -> None:
    calls: list[object] = []

    def fake_run() -> None:
        calls.append(server.mcp)

    monkeypatch.setattr(server.mcp, "run", fake_run)
    server.main()
    assert calls == [server.mcp]


def test_uv_command_exposes_existing_mcp_capabilities() -> None:
    project_root = Path(__file__).resolve().parents[1]

    async def run() -> tuple[list[str], list[str], list[str]]:
        command = StdioServerParameters(command="uv", args=["run", "immich-mcp"], cwd=project_root)
        async with stdio_client(command) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                resources = await session.list_resources()
                prompts = await session.list_prompts()
                return (
                    [tool.name for tool in tools.tools],
                    [str(resource.uri) for resource in resources.resources],
                    [prompt.name for prompt in prompts.prompts],
                )

    tools, resources, prompts = asyncio.run(run())
    assert tools == ["get_server_info", "get_recent_assets", "search_assets"]
    assert resources == ["immich://library/stats"]
    assert prompts == ["review_memories"]
