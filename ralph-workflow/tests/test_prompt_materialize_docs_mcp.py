from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

PLANNING_ACTIVE_TEXT = (
    "The `arabold/docs-mcp-server` is configured and reachable at `localhost:6280`."
)
PLANNING_ACTIVE_INSTRUCTION = (
    "1. Search documentation first using the docs-mcp search tool from your MCP tool list."
)
PLANNING_FALLBACK_HINT = (
    "> **Documentation hint:** Configuring `arabold/docs-mcp-server` on "
    "`localhost:6280` in `.agent/mcp.toml` improves library and API "
    "documentation lookup quality during planning."
)
DEVELOPER_ACTIVE_TEXT = (
    "The `arabold/docs-mcp-server` is configured and reachable at `localhost:6280`."
)
DEVELOPER_ACTIVE_INSTRUCTION = (
    "1. Search documentation using the docs-mcp search tool from your MCP tool list."
)
DEVELOPER_FALLBACK_HINT = (
    "> **Documentation hint:** Configuring `arabold/docs-mcp-server` on "
    "`localhost:6280` in `.agent/mcp.toml` improves library and API "
    "documentation lookup quality during development."
)

if TYPE_CHECKING:
    from pathlib import Path


def _render_materialized_prompt(
    *,
    phase: str,
    drain: SessionDrain,
    has_docs_mcp: bool,
    tmp_path: Path,
) -> str:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Inspect docs-mcp prompt materialization")
    workspace.write(
        ".agent/PLAN.md",
        "# Execution Plan\n\n1. Verify docs-mcp prompt materialization.\n",
    )

    with patch(
        "ralph.prompts.materialize.SkillManager.get_docs_mcp_available",
        return_value=has_docs_mcp,
    ):
        prompt_path = materialize_prompt_for_phase(
            PromptPhaseContext(
                phase=phase,
                workspace=workspace,
                pipeline_policy=policy.pipeline,
                session_caps=SessionCapabilities.defaults_for_drain(drain),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(
                artifacts_policy=policy.artifacts,
                previous_phase=None,
            ),
        )

    return workspace.read(prompt_path)


def test_planning_prompt_materialization_uses_docs_mcp_active_branch(
    tmp_path: Path,
) -> None:
    rendered = _render_materialized_prompt(
        phase="planning",
        drain=SessionDrain.PLANNING,
        has_docs_mcp=True,
        tmp_path=tmp_path,
    )

    assert PLANNING_ACTIVE_TEXT in rendered
    assert PLANNING_ACTIVE_INSTRUCTION in rendered
    assert PLANNING_FALLBACK_HINT not in rendered


def test_planning_prompt_materialization_uses_docs_mcp_fallback_branch(
    tmp_path: Path,
) -> None:
    rendered = _render_materialized_prompt(
        phase="planning",
        drain=SessionDrain.PLANNING,
        has_docs_mcp=False,
        tmp_path=tmp_path,
    )

    assert PLANNING_FALLBACK_HINT in rendered
    assert PLANNING_ACTIVE_INSTRUCTION not in rendered


def test_developer_prompt_materialization_uses_docs_mcp_active_branch(
    tmp_path: Path,
) -> None:
    rendered = _render_materialized_prompt(
        phase="development",
        drain=SessionDrain.DEVELOPMENT,
        has_docs_mcp=True,
        tmp_path=tmp_path,
    )

    assert DEVELOPER_ACTIVE_TEXT in rendered
    assert DEVELOPER_ACTIVE_INSTRUCTION in rendered
    assert DEVELOPER_FALLBACK_HINT not in rendered


def test_developer_prompt_materialization_uses_docs_mcp_fallback_branch(
    tmp_path: Path,
) -> None:
    rendered = _render_materialized_prompt(
        phase="development",
        drain=SessionDrain.DEVELOPMENT,
        has_docs_mcp=False,
        tmp_path=tmp_path,
    )

    assert DEVELOPER_FALLBACK_HINT in rendered
    assert DEVELOPER_ACTIVE_INSTRUCTION not in rendered
