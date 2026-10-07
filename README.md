# Immich MCP

A small read-only MCP server for Immich. It currently exposes `get_server_info`,
`get_recent_assets`, and `search_assets`.

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
`thumbnail_status` is `included`, `unavailable`, `permission_denied`, `rate_limited`, or
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

### `search_assets`

Search images and videos using a natural-language query or metadata filters.
Provide at least one search criterion. Inputs:

- `query`: optional natural-language description for semantic search, such as
  `a dog running on a beach`.
- `media_type`: `all` (default), `image`, or `video`.
- `start_at`, `end_before`: optional inclusive/exclusive capture-time bounds as
  RFC 3339 timestamps with timezone offsets.
- `city`, `country`: optional exact location metadata filters.
- `limit`: 1–10, default 5.
- `include_thumbnail`: boolean, default `true`.
- `page_token`: continuation token for filter-only searches. Pass it alone.

With `query`, Immich's semantic search returns relevance-ranked results. It
requires Immich smart search to be configured and indexed. The current smart
search request has no cursor input, so this tool returns one bounded set and no
continuation token. Without `query`, metadata search sorts newest first and
supports cursor pagination. Both modes return the same `assets` representation
as `get_recent_assets`, including optional thumbnail image blocks.
Smart search allows up to 60 seconds for the Immich machine-learning response;
metadata search retains a 10-second timeout. A timeout now produces a specific
tool error, so it can be distinguished from an HTTP error or connection failure.

## Reliability and errors

All Immich requests have explicit timeouts: 10 seconds for server info, metadata
search, and each thumbnail; 60 seconds for smart search. Each read-only request
may make one additional attempt for a short connection failure, a short read
timeout (except smart search), HTTP 429, or HTTP 502/503/504. A `Retry-After`
value above two seconds prevents an immediate retry. Authentication and
permission failures, most other HTTP errors, invalid JSON, and unexpected
response shapes are not retried. A failed thumbnail does not discard its asset
metadata; its status reports `unavailable`, `permission_denied`, or
`rate_limited` as appropriate.

Tool failures provide a concise JSON error with `code`, `message`, and
`retryable`; HTTP failures also include `status`, and a parsed retry delay is
included when relevant. Upstream response bodies and API keys are never placed
in these errors. An empty search is a successful result with `assets: []`.

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

Try `search_assets` with `{"query": "a dog running"}` for semantic search,
`{"query": "sunset", "media_type": "video", "limit": 3}` to narrow by media
type, or `{"city": "Shanghai", "include_thumbnail": false}` for a paginated
metadata search. A date search can use
`{"start_at": "2026-01-01T00:00:00Z", "end_before": "2027-01-01T00:00:00Z"}`.
When a filter-only result has a non-null `next_page_token`, continue with only
`{"page_token": "<returned token>"}`.

Run the automated tests with `uv run pytest`.
