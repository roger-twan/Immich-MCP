# Tool design

## `get_recent_assets`

Use cases: inspect the latest images and videos, scan a capture-time window, and continue
through a short list without flooding the agent context. The tool is read-only.

Inputs: `limit` (1–10, default 5), `include_thumbnail` (default `true`),
optional inclusive `start_at`, optional exclusive `end_before`, and optional
`page_token`. Date inputs must be RFC 3339 timestamps with timezone offsets.
Continuation uses `page_token` alone. The opaque token preserves the search
filters and limit and carries Immich's cursor.

The client calls [Immich metadata search](https://api.immich.app/) via
`POST /api/search/metadata` (`searchAssets`, `asset.read`). It filters to
`IMAGE` or `VIDEO`, timeline visibility, and non-trashed assets; filters `takenAt` when
requested; sorts by `fileCreatedAt` descending; and requests at most 10 assets.
It requests EXIF metadata so locations are available when stored in Immich.
The response's `assets.nextCursor` becomes `next_page_token`. The cursor and
filter/order fields are documented in Immich's
[OpenAPI specification](https://raw.githubusercontent.com/immich-app/immich/main/open-api/immich-openapi-specs.json).
The cursor form requires Immich 3.2.0 or later.

For each result, the client maps `id`, `originalFileName`, `type`,
`fileCreatedAt`, and optional EXIF city/state/country/coordinates into a small
media record. The Immich type is mapped to `image` or `video`; a missing or
unsupported type is reported as an unexpected upstream response. Video
`duration` maps to `duration_ms` and is `null` if unavailable;
images omit that field. Missing optional metadata becomes `null`. Paths, owner details, hashes,
dimensions, camera information, and the raw API response are omitted.

When requested, the client calls
`GET /api/assets/{id}/thumbnail?size=thumbnail` (`viewAsset`, `asset.view`).
It rejects redirects and images above 1 MB, accepts JPEG/PNG/WebP by their
bytes, and never calls the original-download endpoint. A missing or failed
thumbnail leaves the media metadata intact with a `thumbnail_status`. Video
assets use a still thumbnail; this tool does not return playable video.
Permission failures are marked `permission_denied` and produce a warning.
HTTP 429 on a thumbnail is marked `rate_limited` and also produces a warning.

The MCP tool returns a JSON text block plus native MCP `ImageContent` blocks.
It base64-encodes each accepted thumbnail and supplies the detected MIME type.
`thumbnail_content_index` points to the image block in the `content` array.
Hosts that support image content can render it; others can still read the
metadata and status. Search, authentication, network, and unexpected-response
errors become tool errors without revealing the API key.

## `search_assets`

Use cases: find scenes by natural-language description, search a capture-time
period, or narrow to images or videos in a city or country. At least one
criterion is required to distinguish this from `get_recent_assets`. Inputs are
`query`, `media_type`, `start_at`, `end_before`, `city`, `country`, `limit`,
`include_thumbnail`, and `page_token`. Limits are 1–10, default 5; thumbnails
default on. Location filters match the stored city/country metadata. People
names are not an input: Immich's current filter takes person IDs, while name
lookup is a separate API operation.

When `query` is supplied, the client calls
[`POST /api/search/smart`](https://raw.githubusercontent.com/immich-app/immich/main/open-api/immich-openapi-specs.json)
(`searchSmart`, `asset.read`). The `query` is a natural-language semantic
search, ranked by relevance. The request includes the same `SearchFilter` for
media type, capture time, location, timeline visibility, and non-trashed assets.
It requests EXIF details for location in returned assets. Immich smart search
needs its machine-learning search configured and indexed. The documented
`SmartSearchDto` has no cursor input, so semantic search returns at most one
bounded page and `next_page_token: null`.

Without `query`, the client calls `POST /api/search/metadata` with the same
filters and descending `fileCreatedAt` order. It converts `assets.nextCursor`
into an opaque `page_token` that preserves the filters, limit, and thumbnail
choice. The next call passes only that token. Metadata search requires at
least one of media type, date, city, or country. Cursor pagination requires
Immich 3.2.0 or later.

Both modes reuse the existing asset mapping, thumbnail fetch, and MCP image
content construction. Empty results return `assets: []`; failed thumbnail
requests keep the asset metadata. HTTP and response-shape errors become tool
errors without exposing the API key. Neither mode downloads originals.
The smart-search HTTP request has a 60-second timeout because a model may need
time to load or run; metadata search uses the client's 10-second timeout.
Timeouts and other request failures have distinct user-facing errors.

## Request failures and retries

The client gives server info, metadata search, and thumbnail requests a
10-second timeout; smart search has a 60-second timeout. It makes at most two
attempts per read-only request. The second attempt is limited to short
connection failures, 10-second read timeouts, and HTTP 429/502/503/504 when
there is no long `Retry-After` delay. Smart-search read timeouts are not
retried because the operation may already have run for 60 seconds. HTTP
401/403, 500, other 4xx responses, malformed JSON, and invalid required
response fields are not retried. HTTP 500 is marked retryable for a later
agent decision, but there is no immediate retry.

Tool errors have stable `code`, `message`, and `retryable` fields, with HTTP
`status` and parsed `retry_after_seconds` where applicable. They omit the
upstream body and internal request details. Invalid optional asset fields
become `null`; missing IDs, invalid IDs or types, and invalid pagination
fields produce `unexpected_response`. An empty valid `items` list returns
`assets: []`. Thumbnail errors, invalid image bytes, oversized images, and
redirects only affect that asset's thumbnail status; the metadata result
remains available.

## Resource: `immich://library/stats`

This static, read-only Resource has no inputs. It describes assets owned by
the authenticated Immich user, rather than instance-wide administrator
statistics. The client calls
[`GET /api/assets/statistics`, `GET /api/users/me`, `GET /api/libraries`, and
`GET /api/libraries/{id}/statistics`](https://raw.githubusercontent.com/immich-app/immich/main/open-api/immich-openapi-specs.json).
The asset response provides `total`, `images`, and `videos`. The user response
provides `id` and uploaded-asset quota usage in bytes. The library list is
filtered by `ownerId`; each owned external library's statistics contributes
its `usage` bytes. Immich [excludes external libraries from quota
usage](https://docs.immich.app/administration/server-stats/), so adding these
values is necessary for a complete owned-library storage total. The library
endpoints are admin-only and need `library.read` and `library.statistics`, in
addition to `asset.statistics` and `user.read` for the first two endpoints.
The client maps the result to `total_assets`, `image_count`, `video_count`,
`total_storage_bytes`, and decimal GB string `total_storage_gb`. It does not
expose the other API fields or fetch individual assets. Missing or invalid
required values, including a null `quotaUsageInBytes`, produce a safe
`unexpected_response` error. A library permission failure also fails the
read rather than returning an incomplete total.

The MCP layer publishes one `application/json` text resource. Hosts can list
and read the stable URI as context; an input-free snapshot does not need a
tool invocation schema.
