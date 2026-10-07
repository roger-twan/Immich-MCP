# Immich MCP

Small read-only MCP server for Immich. The first tool is `get_server_info`,
which calls the authenticated `GET /api/server/about` endpoint and returns the
server version and optional build identifier. The API key needs the `server.about`
permission. No photo tools are implemented yet.

## Setup

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp -n .env.example .env
# Edit .env and set your Immich URL and API key.
uv run immich-mcp
```

`IMMICH_URL` may be the server root or end in `/api`. The server reads these
settings only from `.env` in its working directory.
The process uses standard MCP stdio transport, so configure your MCP host to
run `uv run immich-mcp` with this directory as its working directory. Invoke
`get_server_info` with no inputs; a successful result includes
`{"version": "...", "build": "..."}`. The build may be `null`. `.env` is gitignored; never commit
the API key.

Run tests with `uv run pytest`.

For MCP Inspector development, run `uv run mcp dev src/immich_mcp/server.py`
from the project directory.
