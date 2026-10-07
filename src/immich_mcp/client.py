"""Minimal async client for read-only Immich requests."""

import asyncio
import base64
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal

import httpx

from .config import Config


class ImmichError(Exception):
    """A safe, user-facing Immich request error."""


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

    async def get_server_info(self) -> ServerInfo:
        try:
            response = await self._http.get("server/about")
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ImmichError(f"Immich returned HTTP {exc.response.status_code}") from None
        except httpx.RequestError:
            raise ImmichError("Could not connect to Immich") from None
        except ValueError:
            raise ImmichError("Immich returned invalid JSON") from None

        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            raise ImmichError("Immich returned unexpected server information")
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
        try:
            response = await self._http.post("search/metadata", json=body)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ImmichError(f"Immich search returned HTTP {exc.response.status_code}") from None
        except httpx.RequestError:
            raise ImmichError("Could not search Immich") from None
        except ValueError:
            raise ImmichError("Immich search returned invalid JSON") from None
        assets = data.get("assets") if isinstance(data, dict) else None
        items = assets.get("items") if isinstance(assets, dict) else None
        if not isinstance(items, list) or "nextCursor" not in assets:
            raise ImmichError("Immich returned unexpected search results or does not support cursor pagination")
        next_cursor = assets["nextCursor"]
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ImmichError("Immich returned an invalid search cursor")
        recent_assets: list[Asset] = []
        for item in items[:limit]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                continue
            raw_type = item.get("type")
            if raw_type not in ("IMAGE", "VIDEO"):
                continue
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
        next_page_token = _make_token(next_cursor, start_at, end_before, limit, include_thumbnail) if next_cursor else None
        return RecentAssetsPage(assets=recent_assets, next_page_token=next_page_token)

    async def _get_thumbnail(self, asset_id: str) -> tuple[str, bytes | None, str | None]:
        try:
            response = await self._http.get(f"assets/{asset_id}/thumbnail", params={"size": "thumbnail"})
        except httpx.RequestError:
            return "unavailable", None, None
        if response.status_code in (401, 403):
            return "permission_denied", None, None
        if response.status_code != 200 or not response.content or len(response.content) > 1_000_000:
            return "unavailable", None, None
        mime = _image_mime(response.content)
        if mime is None:
            return "unavailable", None, None
        return "included", response.content, mime
