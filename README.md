# Immich MCP

A small read-only MCP server for Immich. It currently exposes `get_server_info`
and `get_recent_assets`.

## Setup

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp -n .env.example .env
# Edit .env and set IMMICH_URL and IMMICH_API_KEY.
uv run immich-mcp
```

`IMMICH_URL` may be the server root or end in `/api`. Configuration is read only
from `.env` in the process working directory. The `.env` file is gitignored;
do not commit the API key. Give the key `server.about` for `get_server_info`,
`asset.read` for media metadata, and `asset.view` for thumbnails. Metadata can
still be returned when an individual thumbnail fails or `asset.view` is missing.

The server uses MCP stdio transport. Configure your host to run
`uv run immich-mcp` with this project as its working directory.

## Tools

### `get_server_info`

No inputs. Returns a `version` and optional `build` using Immich's
`GET /api/server/about` endpoint.

### `get_recent_assets`

Returns timeline images and videos ordered by capture time, newest first. Inputs:

- `limit`: 1–10, default 5.
- `include_thumbnail`: boolean, default `true`.
- `start_at`: optional inclusive RFC 3339 capture timestamp with timezone.
- `end_before`: optional exclusive RFC 3339 capture timestamp with timezone.
- `page_token`: optional continuation token from the previous result. Pass it
  alone; it preserves the previous limit, date bounds, and thumbnail choice.

The result contains `assets` and `next_page_token` (or `null` when there is no
next page). Each item has `id`, `filename`, `type` (`image` or `video`), `taken_at`, `location`,
`thumbnail_status`, and `thumbnail_content_index`. Missing metadata is `null`.
Video items also contain `duration_ms` (an integer or `null` when unavailable);
image items omit it.
Location, when available, contains city, state, country, latitude, and longitude.
`thumbnail_status` is `included`, `unavailable`, `permission_denied`, or
`not_requested`. When included, `thumbnail_content_index` identifies the
corresponding image block in the MCP tool result's `content` array; the JSON
text block is index 0. A host that supports MCP image content can display the
thumbnail. A video receives a still thumbnail image, not playable video. The
tool never requests the original asset.

The search uses Immich's `POST /api/search/metadata` with an image-or-video/timeline
filter, descending capture-time order, and cursor pagination. Thumbnail bytes
come from `GET /api/assets/{id}/thumbnail?size=thumbnail`. See
[tool design](docs/tool-design.md) for the mapping and API references. Cursor
pagination uses fields introduced in Immich 3.2.0; an older server returns a
clear unsupported-response error.

## Verify with MCP Inspector

```sh
uv run mcp dev src/immich_mcp/server.py
```

In Inspector, connect to the server, choose **Tools**, and call
`get_recent_assets` with `{}`. Expect up to five images or videos, newest first, with
metadata and image content blocks if thumbnails are available. Try
`{"include_thumbnail": false, "limit": 2}` to check metadata-only output.
If `next_page_token` is non-null, call again with only
`{"page_token": "<returned token>"}`. If your host does not render MCP image
blocks, check `thumbnail_status` and `thumbnail_content_index` in the result.

Run the automated tests with `uv run pytest`.
