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
    assert policy.pipeline.development_timebox is not None
    development_phase = policy.pipeline.phases["development"]
    assert development_phase.parallelization is not None
    phases = dict(policy.pipeline.phases)
    phases["development"] = development_phase.model_copy(
        update={
            "parallelization": development_phase.parallelization.model_copy(
                update={"max_parallel_workers": 3}
            )
        }
    )
    pipeline = policy.pipeline.model_copy(
        update={
            "development_timebox": policy.pipeline.development_timebox.model_copy(
                update={"duration_seconds": 2520.0}
            ),
            "phases": phases,
        }
    )
    path = materialize_prompt_for_phase(
        PromptPhaseContext(
            "planning",
            workspace,
            pipeline,
            SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            tmp_path,
        ),
        PromptPhaseOptions(artifacts_policy=policy.artifacts),
    )
    rendered = workspace.read(path)
    assert "ralph_submit_md_artifact" in rendered
    assert "ralph_verify_md_artifact" in rendered
    assert "executor guidance, not a required document shape" in rendered
    assert "42 minutes" in rendered
    assert "3 concurrent workers" in rendered
    assert "max_work_units" not in rendered
