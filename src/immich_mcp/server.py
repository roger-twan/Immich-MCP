"""MCP protocol entry point, kept separate from the Immich HTTP client."""

from mcp.server.mcpserver import MCPServer

from immich_mcp.client import ImmichClient
from immich_mcp.config import Config


mcp = MCPServer("Immich")


@mcp.tool()
async def get_server_info() -> dict[str, str | None]:
    """Get the Immich server version and optional build identifier."""
    async with ImmichClient(Config.from_dotenv()) as client:
        info = await client.get_server_info()
    return {"version": info.version, "build": info.build}


def main() -> None:
    mcp.run()
