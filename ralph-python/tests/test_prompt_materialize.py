from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.policy.loader import load_policy
from ralph.prompts.materialize import materialize_prompt_for_phase, prompt_file_for_phase
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path


def test_materialize_prompt_for_phase_renders_planning_prompt_to_agent_tmp(tmp_path: Path) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the template migration")

    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
    )

    assert prompt_path == ".agent/tmp/planning_prompt.md"
    rendered = workspace.read(prompt_path)
    assert "PLANNING MODE" in rendered
    assert "Plan the template migration" in rendered


def test_prompt_file_for_phase_uses_agent_tmp_file_name() -> None:
    assert prompt_file_for_phase("review_analysis") == ".agent/tmp/review_analysis_prompt.md"
