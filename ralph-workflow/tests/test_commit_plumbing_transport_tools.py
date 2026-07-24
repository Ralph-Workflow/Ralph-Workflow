"""Transport-qualified tool names for standalone commit plumbing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.pipeline.plumbing.commit_plumbing import (
    _commit_prompt_for_agent,
    _submit_artifact_tool_names_for_transport,
)
from ralph.prompts.template_registry import TemplateRegistry

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("transport", "expected"),
    [
        (AgentTransport.CLAUDE, "mcp__ralph__ralph_submit_md_artifact"),
        (AgentTransport.CODEX, "mcp__ralph__ralph_submit_md_artifact"),
        (AgentTransport.CURSOR, "mcp__ralph__ralph_submit_md_artifact"),
        (AgentTransport.OPENCODE, "ralph_ralph_submit_md_artifact"),
    ],
)
def test_submit_tool_name_matches_transport(
    transport: AgentTransport,
    expected: str,
) -> None:
    assert _submit_artifact_tool_names_for_transport(transport)[0] == expected


@pytest.mark.parametrize(
    ("transport", "tool_prefix"),
    [
        (AgentTransport.CLAUDE, "mcp__ralph__"),
        (AgentTransport.CODEX, "mcp__ralph__"),
        (AgentTransport.CURSOR, "mcp__ralph__"),
        (AgentTransport.OPENCODE, "ralph_"),
    ],
)
def test_commit_prompt_qualifies_every_state_changing_tool(
    tmp_path: Path,
    transport: AgentTransport,
    tool_prefix: str,
) -> None:
    prompt = _commit_prompt_for_agent(
        AgentConfig(cmd="agent", transport=transport, json_parser="generic"),
        "diff --git a/app.py b/app.py\n+hello",
        template_registry=TemplateRegistry(),
        repo_root=tmp_path,
    )

    assert f"{tool_prefix}ralph_submit_md_artifact" in prompt
    assert f"{tool_prefix}declare_complete" in prompt
    assert f"{tool_prefix}write_file" in prompt
    assert f'`{tool_prefix}declare_complete(summary="commit_message")`' in prompt
