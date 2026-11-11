from __future__ import annotations

from pathlib import Path

from ralph.config.enums import AgentTransport
from ralph.pipeline.plumbing import smoke_plumbing as smoke_module


def test_build_smoke_prompt_multimodal_false_renders_baseline_byte_for_byte() -> None:
    baseline = smoke_module._build_smoke_prompt(
        Path("tmp/interactive-claude-smoke/todo-list.js").as_posix(),
        submit_artifact_tool_name="mcp__ralph__ralph_submit_md_artifact",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    flagged = smoke_module._build_smoke_prompt(
        Path("tmp/interactive-claude-smoke/todo-list.js").as_posix(),
        submit_artifact_tool_name="mcp__ralph__ralph_submit_md_artifact",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        multimodal=False,
    )
    assert flagged == baseline


def test_build_smoke_prompt_multimodal_true_appends_contract_bullets() -> None:
    baseline = smoke_module._build_smoke_prompt(
        Path("tmp/interactive-claude-smoke/todo-list.js").as_posix(),
        submit_artifact_tool_name="mcp__ralph__ralph_submit_md_artifact",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    flagged = smoke_module._build_smoke_prompt(
        Path("tmp/interactive-claude-smoke/todo-list.js").as_posix(),
        submit_artifact_tool_name="mcp__ralph__ralph_submit_md_artifact",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        multimodal=True,
        multimodal_fixture_relpath="smoke-fixture.png",
    )
    assert flagged != baseline
    assert "smoke-fixture.png" in flagged
    assert "ralph://media" in flagged
    assert "MEDIA_RECEIPT" in flagged
    assert "DIMENSIONS" in flagged
    assert "MEDIA_SHA256" in flagged
    assert "read_media" in flagged
    assert "read_image" in flagged
