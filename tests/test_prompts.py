"""MCP Prompt registration and rendering tests."""

import asyncio

import pytest

from immich_mcp.server import mcp


def test_only_review_memories_prompt_is_registered() -> None:
    prompts = asyncio.run(mcp.list_prompts())
    assert [prompt.name for prompt in prompts] == ["review_memories"]
    assert [(argument.name, argument.required) for argument in prompts[0].arguments] == [("days", False)]
    assert "1–365" in prompts[0].arguments[0].description


def test_review_memories_defaults_to_30_days_and_guides_grounded_review() -> None:
    result = asyncio.run(mcp.get_prompt("review_memories"))
    assert len(result.messages) == 1
    message = result.messages[0]
    assert message.role == "user"
    prompt = message.content.text
    assert "past 30 days" in prompt
    assert "available Immich tools" in prompt
    assert "images and videos" in prompt
    assert "thumbnails" in prompt
    assert "events, activities, places, recurring people or subjects" in prompt
    assert "chronologically" in prompt
    assert "uncertain inferences" in prompt
    assert "Do not invent" in prompt
    assert "get_recent_assets" not in prompt
    assert "search_assets" not in prompt


def test_review_memories_accepts_custom_days() -> None:
    result = asyncio.run(mcp.get_prompt("review_memories", {"days": "7"}))
    assert "past 7 days" in result.messages[0].content.text


@pytest.mark.parametrize("days", ["0", "366", "not-a-number"])
def test_review_memories_rejects_invalid_days(days: str) -> None:
    with pytest.raises(ValueError):
        asyncio.run(mcp.get_prompt("review_memories", {"days": days}))
