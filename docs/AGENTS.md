# Immich MCP

## Goal

Buila small, production-oriented MCP server for Immich.

## Tech Stack

- Python 3.13+
- uv
- Official MCP Python SDK
- pytest

Keep MCP protocol logic separate from immich API integration.

## Principles

- Read-only by default
- Do not expose every Immich API endpoint as an MCP tool.
- Design agent-oriented tools, not API wrappers.
- Never hardcode API key.
- Do not return raw Immich responses unless necessary.
- Use pagination an bounded result sizes.
- Use explicit timeouts.
- Retry only safe/idempotent operations.
- Keep implementations simple.
- Configuration must come from a local .env file only.

## Initial Tools

1. `get_recent_assets` - Get recent images and videos with optional date range and limit.
2. `search_assets` - Search assets by keyword.

Do not add additional tools without explaining why they are needed.

## Initial Resources

- `immich://library/stats` - Library statistics

## Development

Before implementing a significant change.

1. Explain the proposed design.
2. Identify the relevant Immich API endpoints.
3. Explain the MCP tool schema.
4. Then implement it.

Use current Immich v3 API documentation rather than guessing endpoints.
