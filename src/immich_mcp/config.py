"""Configuration loaded only from the local .env file."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values


@dataclass(frozen=True)
class Config:
    api_url: str
    api_key: str

    @classmethod
    def from_dotenv(cls) -> "Config":
        values = dotenv_values(Path.cwd() / ".env", interpolate=False)
        raw_url = (values.get("IMMICH_URL") or "").strip()
        api_key = (values.get("IMMICH_API_KEY") or "").strip()
        if not raw_url:
            raise ValueError("IMMICH_URL is required")
        if not api_key:
            raise ValueError("IMMICH_API_KEY is required")

        parsed = urlsplit(raw_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("IMMICH_URL must be an http(s) server URL without credentials, query, or fragment")

        path = parsed.path.rstrip("/")
        if not path.endswith("/api"):
            path += "/api"
        api_url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        return cls(api_url=api_url, api_key=api_key)
