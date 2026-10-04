"""Public prompt rendering checks for permissive plan submission."""

from __future__ import annotations

from pathlib import Path

from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace


def test_rendered_planning_prompt_names_canonical_tools(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan independent repository work with evidence.")
    policy = load_policy(tmp_path / ".agent")
    path = materialize_prompt_for_phase(
        PromptPhaseContext("planning", workspace, policy.pipeline, SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING), tmp_path),
        PromptPhaseOptions(artifacts_policy=policy.artifacts),
    )
    rendered = workspace.read(path)
    assert "ralph_submit_md_artifact" in rendered
    assert "ralph_verify_md_artifact" in rendered
    assert "recommended shape" in rendered
    assert "max_work_units" not in rendered
