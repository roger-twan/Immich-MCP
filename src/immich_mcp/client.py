"""Minimal async client for read-only Immich requests."""

import asyncio
import base64
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal
from uuid import UUID

import httpx

from .config import Config


class ImmichError(Exception):
    """A safe, user-facing Immich request error."""

    def __init__(
        self, message: str, *, code: str = "immich_error", status: int | None = None,
        retryable: bool = False, retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"code": self.code, "message": str(self), "retryable": self.retryable}
        if self.status is not None:
            result["status"] = self.status
        if self.retry_after_seconds is not None:
            result["retry_after_seconds"] = self.retry_after_seconds
        return result


@dataclass(frozen=True)
class ServerInfo:
    version: str
    build: str | None = None


@dataclass(frozen=True)
class Location:
    city: str | None
    state: str | None
    country: str | None
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class Asset:
    id: str
    filename: str | None
    asset_type: Literal["image", "video"]
    taken_at: str | None
    location: Location | None
    thumbnail_status: str
    thumbnail_data: bytes | None = None
    thumbnail_mime_type: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class RecentAssetsPage:
    assets: list[Asset]
    next_page_token: str | None


def _timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Date bounds must be RFC 3339 timestamps with a timezone offset") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Date bounds must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _make_token(cursor: str, start_at: str | None, end_before: str | None, limit: int, include_thumbnail: bool) -> str:
    payload = {"v": 1, "cursor": cursor, "start_at": start_at, "end_before": end_before,
               "limit": limit, "include_thumbnail": include_thumbnail}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def _read_token(token: str) -> tuple[str, str | None, str | None, int, bool]:
    try:
        if len(token) > 4096 or not token:
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError
        cursor = payload["cursor"]
        start_at = payload["start_at"]
        end_before = payload["end_before"]
        limit = payload["limit"]
        include_thumbnail = payload["include_thumbnail"]
        if (not isinstance(cursor, str) or not cursor or
            (start_at is not None and not isinstance(start_at, str)) or
            (end_before is not None and not isinstance(end_before, str)) or
            type(limit) is not int or not 1 <= limit <= 10 or
            type(include_thumbnail) is not bool):
            raise ValueError
        return cursor, start_at, end_before, limit, include_thumbnail
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, base64.binascii.Error):
        raise ValueError("Invalid page_token") from None


def _make_search_token(cursor: str, options: dict[str, object]) -> str:
    payload = {"v": 1, "kind": "search", "cursor": cursor, "options": options}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def _read_search_token(token: str) -> tuple[str, dict[str, object]]:
    try:
        if not token or len(token) > 4096:
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("kind") != "search":
            raise ValueError
        cursor, options = payload["cursor"], payload["options"]
        if not isinstance(cursor, str) or not cursor or not isinstance(options, dict):
            raise ValueError
        if set(options) != {"media_type", "start_at", "end_before", "city", "country", "limit", "include_thumbnail"}:
            raise ValueError
        if (options["media_type"] not in ("all", "image", "video") or
            any(options[key] is not None and not isinstance(options[key], str)
                for key in ("start_at", "end_before", "city", "country")) or
            type(options["limit"]) is not int or not 1 <= options["limit"] <= 10 or
            type(options["include_thumbnail"]) is not bool):
            raise ValueError
        return cursor, options
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, base64.binascii.Error):
        raise ValueError("Invalid page_token") from None


def _location(exif: object) -> Location | None:
    if not isinstance(exif, dict):
        return None
    names = [exif.get(key) if isinstance(exif.get(key), str) else None for key in ("city", "state", "country")]
    lat, lon = exif.get("latitude"), exif.get("longitude")
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and
            not isinstance(lat, bool) and not isinstance(lon, bool) and
            -90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    if not any(names) and lat is None:
        return None
    return Location(*names, float(lat) if lat is not None else None, float(lon) if lon is not None else None)


def _image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds):
        return None
    return round(max(0, seconds), 3)


class ImmichClient:
    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=config.api_url.rstrip("/") + "/",
            headers={"x-api-key": config.api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(10.0),
            transport=transport,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "ImmichClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, operation: str,
        json_body: dict[str, object] | None = None,
        params: dict[str, str] | None = None, timeout_seconds: float = 10.0,
    ) -> httpx.Response:
        # Every caller is a read-only request. One retry covers short transient failures.
        for attempt in range(2):
            try:
                response = await self._http.request(
                    method, path, json=json_body, params=params,
                    timeout=httpx.Timeout(timeout_seconds),
                )
            except httpx.TimeoutException as exc:
                can_retry = isinstance(exc, httpx.ConnectTimeout) or (
                    timeout_seconds <= 10 and isinstance(exc, httpx.ReadTimeout)
                )
                if attempt == 0 and can_retry:
                    await asyncio.sleep(0.2)
                    continue
                raise ImmichError(
                    f"Immich {operation} timed out after {timeout_seconds:g} seconds",
                    code="timeout", retryable=True,
                ) from None
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                raise ImmichError(
                    f"Immich {operation} request failed ({type(exc).__name__})",
                    code="connection_error", retryable=True,
                ) from None
            except httpx.RequestError as exc:
                raise ImmichError(
                    f"Immich {operation} request failed ({type(exc).__name__})",
                    code="request_error",
                ) from None

            if response.status_code == 200:
                return response
            status = response.status_code
            retry_after_header = response.headers.get("Retry-After")
            retry_after = _retry_after_seconds(retry_after_header)
            if status in (429, 502, 503, 504) and attempt == 0 and (
                retry_after_header is None or (retry_after is not None and retry_after <= 2)
            ):
                await asyncio.sleep(retry_after if retry_after is not None else 0.2)
                continue
            code = (
                "authentication_failed" if status == 401 else
                "permission_denied" if status == 403 else
                "rate_limited" if status == 429 else
                "server_error" if 500 <= status <= 599 else "http_error"
            )
            raise ImmichError(
                f"Immich {operation} returned HTTP {status}",
                code=code, status=status,
                retryable=status == 429 or 500 <= status <= 599,
                retry_after_seconds=retry_after if status in (429, 503) else None,
            )
        raise AssertionError("unreachable")

    async def get_server_info(self) -> ServerInfo:
        try:
            response = await self._request("GET", "server/about", operation="server info")
            data = response.json()
        except ValueError:
            raise ImmichError("Immich server info returned invalid JSON", code="invalid_json") from None

        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            raise ImmichError("Immich returned unexpected server information", code="unexpected_response")
        build = data.get("build")
        return ServerInfo(version=data["version"], build=build if isinstance(build, str) else None)

    async def get_recent_assets(
        self, *, limit: int = 5, include_thumbnail: bool = True,
        start_at: str | None = None, end_before: str | None = None,
        page_token: str | None = None,
    ) -> RecentAssetsPage:
        if page_token is not None:
            if start_at is not None or end_before is not None or limit != 5 or include_thumbnail is not True:
                raise ValueError("Use page_token alone to continue a search")
            cursor, start_at, end_before, limit, include_thumbnail = _read_token(page_token)
        else:
            cursor = None
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")
        start_at, end_before = _timestamp(start_at), _timestamp(end_before)
        if start_at and end_before and start_at >= end_before:
            raise ValueError("start_at must be before end_before")

        filters: dict[str, object] = {
            "type": {"in": ["IMAGE", "VIDEO"]},
            "visibility": {"eq": "timeline"},
            "trashedAt": {"eq": None},
        }
        if start_at or end_before:
            filters["takenAt"] = {key: value for key, value in (("gte", start_at), ("lt", end_before)) if value}
        body: dict[str, object] = {
            "filter": filters,
            "orderBy": {"field": "fileCreatedAt", "direction": "desc"},
            "size": limit,
            "withExif": True,
        }
        if cursor:
            body["cursor"] = cursor
        recent_assets, next_cursor = await self._search_page("search/metadata", body, limit, include_thumbnail)
        next_page_token = _make_token(next_cursor, start_at, end_before, limit, include_thumbnail) if next_cursor else None
        return RecentAssetsPage(assets=recent_assets, next_page_token=next_page_token)

    async def search_assets(
        self, *, query: str | None = None, media_type: Literal["all", "image", "video"] = "all",
        start_at: str | None = None, end_before: str | None = None,
        city: str | None = None, country: str | None = None,
        limit: int = 5, include_thumbnail: bool = True, page_token: str | None = None,
    ) -> RecentAssetsPage:
        if page_token is not None:
            if any(value is not None for value in (query, start_at, end_before, city, country)) or media_type != "all" or limit != 5 or include_thumbnail is not True:
                raise ValueError("Use page_token alone to continue a search")
            cursor, options = _read_search_token(page_token)
            media_type = options["media_type"]
            start_at, end_before = options["start_at"], options["end_before"]
            city, country = options["city"], options["country"]
            limit, include_thumbnail = options["limit"], options["include_thumbnail"]
        else:
            cursor = None
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")
        if media_type not in ("all", "image", "video"):
            raise ValueError("media_type must be all, image, or video")
        if type(include_thumbnail) is not bool:
            raise ValueError("include_thumbnail must be a boolean")
        for name, value, maximum in (("query", query, 300), ("city", city, 100), ("country", country, 100)):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > maximum):
                raise ValueError(f"{name} must be non-empty and at most {maximum} characters")
        query = query.strip() if query is not None else None
        city = city.strip() if city is not None else None
        country = country.strip() if country is not None else None
        start_at, end_before = _timestamp(start_at), _timestamp(end_before)
        if start_at and end_before and start_at >= end_before:
            raise ValueError("start_at must be before end_before")
        if not any((query, start_at, end_before, city, country)) and media_type == "all":
            raise ValueError("Provide a query or at least one search filter")

        filters: dict[str, object] = {
            "type": {"in": ["IMAGE", "VIDEO"]} if media_type == "all" else {"eq": media_type.upper()},
            "visibility": {"eq": "timeline"},
            "trashedAt": {"eq": None},
        }
        if start_at or end_before:
            filters["takenAt"] = {key: value for key, value in (("gte", start_at), ("lt", end_before)) if value}
        if city:
            filters["city"] = {"eq": city}
        if country:
            filters["country"] = {"eq": country}
        body: dict[str, object] = {"filter": filters, "size": limit, "withExif": True}
        if query:
            body["query"] = query
            endpoint = "search/smart"
        else:
            endpoint = "search/metadata"
            body["orderBy"] = {"field": "fileCreatedAt", "direction": "desc"}
            if cursor:
                body["cursor"] = cursor
        found_assets, next_cursor = await self._search_page(endpoint, body, limit, include_thumbnail,
                                                             require_cursor=not query)
        next_page_token = None
        if not query and next_cursor:
            options = {"media_type": media_type, "start_at": start_at, "end_before": end_before,
                       "city": city, "country": country, "limit": limit,
                       "include_thumbnail": include_thumbnail}
            next_page_token = _make_search_token(next_cursor, options)
        return RecentAssetsPage(assets=found_assets, next_page_token=next_page_token)

    async def _search_page(
        self, endpoint: str, body: dict[str, object], limit: int,
        include_thumbnail: bool, *, require_cursor: bool = True,
    ) -> tuple[list[Asset], str | None]:
        is_smart_search = endpoint == "search/smart"
        try:
            response = await self._request(
                "POST", endpoint, operation="smart search" if is_smart_search else "metadata search",
                json_body=body, timeout_seconds=60.0 if is_smart_search else 10.0,
            )
            data = response.json()
        except ValueError:
            raise ImmichError("Immich search returned invalid JSON", code="invalid_json") from None
        assets = data.get("assets") if isinstance(data, dict) else None
        items = assets.get("items") if isinstance(assets, dict) else None
        if not isinstance(items, list) or (require_cursor and "nextCursor" not in assets):
            raise ImmichError("Immich returned unexpected search results or does not support cursor pagination",
                              code="unexpected_response")
        next_cursor = assets.get("nextCursor")
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
            raise ImmichError("Immich returned an invalid search cursor", code="unexpected_response")
        recent_assets: list[Asset] = []
        for item in items[:limit]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ImmichError("Immich returned an asset with invalid required fields",
                                  code="unexpected_response")
            try:
                UUID(item["id"])
            except ValueError:
                raise ImmichError("Immich returned an asset with invalid required fields",
                                  code="unexpected_response") from None
            raw_type = item.get("type")
            if raw_type not in ("IMAGE", "VIDEO"):
                raise ImmichError("Immich returned an asset with invalid required fields",
                                  code="unexpected_response")
            asset_type: Literal["image", "video"] = "image" if raw_type == "IMAGE" else "video"
            duration = item.get("duration")
            duration_ms = duration if type(duration) is int and duration >= 0 else None
            recent_assets.append(Asset(
                id=item["id"],
                filename=item.get("originalFileName") if isinstance(item.get("originalFileName"), str) else None,
                asset_type=asset_type,
                taken_at=item.get("fileCreatedAt") if isinstance(item.get("fileCreatedAt"), str) else None,
                location=_location(item.get("exifInfo")),
                thumbnail_status="not_requested" if not include_thumbnail else "unavailable",
                duration_ms=duration_ms if asset_type == "video" else None,
            ))
        if include_thumbnail and recent_assets:
            semaphore = asyncio.Semaphore(4)

            async def fetch(asset: Asset) -> Asset:
                async with semaphore:
                    status, image, mime = await self._get_thumbnail(asset.id)
                    return replace(asset, thumbnail_status=status, thumbnail_data=image,
                                   thumbnail_mime_type=mime)

            recent_assets = list(await asyncio.gather(*(fetch(asset) for asset in recent_assets)))
        return recent_assets, next_cursor

    async def _get_thumbnail(self, asset_id: str) -> tuple[str, bytes | None, str | None]:
        try:
            response = await self._request(
                "GET", f"assets/{asset_id}/thumbnail", operation="thumbnail",
                params={"size": "thumbnail"},
            )
        except ImmichError as exc:
            if exc.code in ("authentication_failed", "permission_denied"):
                return "permission_denied", None, None
            if exc.code == "rate_limited":
                return "rate_limited", None, None
            return "unavailable", None, None
        if not response.content or len(response.content) > 1_000_000:
            return "unavailable", None, None
        mime = _image_mime(response.content)
        if mime is None:
            return "unavailable", None, None
        return "included", response.content, mime
