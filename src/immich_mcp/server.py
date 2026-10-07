"""MCP protocol entry point, kept separate from the Immich HTTP client."""

import base64
import json
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ImageContent, TextContent
from pydantic import Field

from immich_mcp.client import ImmichClient, ImmichError, RecentAssetsPage
from immich_mcp.config import Config


mcp = MCPServer("Immich")


def _tool_error(exc: ValueError | ImmichError) -> ToolError:
    if isinstance(exc, ImmichError):
        details = exc.to_dict()
    else:
        code = "configuration_error" if str(exc).startswith(("IMMICH_URL", "IMMICH_API_KEY")) else "invalid_input"
        details = {"code": code, "message": str(exc), "retryable": False}
    return ToolError(json.dumps(details, ensure_ascii=False))


@mcp.tool()
async def get_server_info() -> dict[str, str | None]:
    """Get the Immich server version and optional build identifier."""
    try:
        async with ImmichClient(Config.from_dotenv()) as client:
            info = await client.get_server_info()
    except (ValueError, ImmichError) as exc:
        raise _tool_error(exc) from None
    return {"version": info.version, "build": info.build}


@mcp.tool()
async def get_recent_assets(
    limit: Annotated[int, Field(ge=1, le=10)] = 5,
    include_thumbnail: bool = True,
    start_at: str | None = None,
    end_before: str | None = None,
    page_token: str | None = None,
) -> CallToolResult:
    """List newest captured images and videos. Date bounds require RFC 3339 offsets; continue with page_token alone."""
    try:
        async with ImmichClient(Config.from_dotenv()) as client:
            page = await client.get_recent_assets(
                limit=limit, include_thumbnail=include_thumbnail,
                start_at=start_at, end_before=end_before, page_token=page_token,
            )
    except (ValueError, ImmichError) as exc:
        raise _tool_error(exc) from None
    return _asset_result(page)


@mcp.tool()
async def search_assets(
    query: str | None = None,
    media_type: Literal["all", "image", "video"] = "all",
    start_at: str | None = None,
    end_before: str | None = None,
    city: str | None = None,
    country: str | None = None,
    limit: Annotated[int, Field(ge=1, le=10)] = 5,
    include_thumbnail: bool = True,
    page_token: str | None = None,
) -> CallToolResult:
    """Search images and videos. A query uses semantic relevance; filters alone sort newest first. Continue with page_token alone."""
    try:
        async with ImmichClient(Config.from_dotenv()) as client:
            page = await client.search_assets(
                query=query, media_type=media_type, start_at=start_at, end_before=end_before,
                city=city, country=country, limit=limit, include_thumbnail=include_thumbnail,
                page_token=page_token,
            )
    except (ValueError, ImmichError) as exc:
        raise _tool_error(exc) from None
    return _asset_result(page)


def _asset_result(page: RecentAssetsPage) -> CallToolResult:

    assets: list[dict[str, object]] = []
    images: list[ImageContent] = []
    for asset in page.assets:
        image_content_index = None
        if asset.thumbnail_data is not None and asset.thumbnail_mime_type is not None:
            image_content_index = len(images) + 1  # The first content block is the JSON text.
            images.append(ImageContent(
                type="image",
                data=base64.b64encode(asset.thumbnail_data).decode("ascii"),
                mime_type=asset.thumbnail_mime_type,
            ))
        item: dict[str, object] = {
            "id": asset.id,
            "filename": asset.filename,
            "type": asset.asset_type,
            "taken_at": asset.taken_at,
            "location": vars(asset.location) if asset.location else None,
            "thumbnail_status": asset.thumbnail_status,
            "thumbnail_content_index": image_content_index,
        }
        if asset.asset_type == "video":
            item["duration_ms"] = asset.duration_ms
        assets.append(item)
    result: dict[str, object] = {"assets": assets, "next_page_token": page.next_page_token}
    if any(asset.thumbnail_status == "permission_denied" for asset in page.assets):
        result["warnings"] = ["Thumbnails need the asset.view API key permission."]
    if any(asset.thumbnail_status == "rate_limited" for asset in page.assets):
        result.setdefault("warnings", []).append("Some thumbnails were rate limited by Immich.")
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False)), *images],
        structured_content=result,
    )


def main() -> None:
    mcp.run()
