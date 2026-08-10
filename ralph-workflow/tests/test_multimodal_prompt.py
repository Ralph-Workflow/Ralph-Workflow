"""Tests for the multimodal-aware smoke prompt builder (S-3).

These tests pin the byte-for-byte invariants of
:func:`ralph.pipeline.plumbing.smoke_plumbing._build_smoke_prompt`:

- The default (``multimodal=False``) render is byte-identical to the
  pre-change baseline so every existing smoke run is unchanged.
- ``multimodal=True`` appends the contract bullets from
  :func:`ralph.pipeline.plumbing.smoke_multimodal.multimodal_prompt_requirements`
  in the exact shape the grader depends on (fixture path, the
  replay-the-handle instruction, the three output-token lines).

This file is NOT marked ``pytest.mark.smoke`` because it tests the
prompt-builder API, not a real agent invocation.
"""

from __future__ import annotations

from pathlib import Path

from ralph.config.enums import AgentTransport
from ralph.pipeline.plumbing import smoke_plumbing as smoke_module


def test_build_smoke_prompt_multimodal_false_renders_baseline_byte_for_byte() -> None:
    """``multimodal=False`` renders the prompt identically to the no-flag default.

    Every current smoke run renders the prompt through the default
    (non-multimodal) path. Adding the optional flag must keep the
    default render byte-identical to the pre-change baseline so no
    existing smoke run is unexpectedly impacted by the new code.
    """
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
    """``multimodal=True`` appends every bullet the multimodal grader relies on."""
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
    # The handle the agent is told to issue a second read_media call against.
    assert "ralph://media" in flagged
    # The three tokens the agent must write into the output file.
    assert "MEDIA_RECEIPT" in flagged
    assert "DIMENSIONS" in flagged
    assert "MEDIA_SHA256" in flagged
    # Both media tool names the grader credits at WIRE.
    assert "read_media" in flagged
    assert "read_image" in flagged
