from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.prompts.materialize as materialize_module
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


def test_materialize_commit_phase_tolerates_empty_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    monkeypatch.setattr(materialize_module, "_git_diff", lambda _workspace_root: "")

    prompt_path = materialize_prompt_for_phase(
        phase="development_commit",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
        workspace_root=tmp_path,
    )

    assert prompt_path == ".agent/tmp/development_commit_prompt.md"
    rendered = workspace.read(prompt_path)
    assert "DIFF:" in rendered


def test_materialize_development_prompt_formats_wrapped_plan_for_execution(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement unattended planning recovery")
    workspace.write(
        ".agent/artifacts/plan.json",
        (
            '{"type":"plan","content":'
            '{"summary":{"context":"Fix planner handoff","scope_items":['
            '{"text":"retry invalid planning output"},'
            '{"text":"pass plan clearly to development"}]},'
            '"steps":['
            '{"number":1,"title":"Add regression tests",'
            '"content":"Cover planning and handoff failures"},'
            '{"number":2,"title":"Fix pipeline routing",'
            '"content":"Use phase handlers and policy transitions"}'
            "],"
            '"critical_files":{"primary_files":['
            '{"path":"ralph/pipeline/runner.py","action":"modify",'
            '"why":"Wire planning results into reducer"}'
            "]},"
            '"risks_mitigations":['
            '{"risk":"Retry loop regression","mitigation":"Add targeted tests"}],'
            '"verification_strategy":['
            '{"method":"pytest","expected_outcome":"planning retries recover cleanly"}]'
            "}}"
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "Fix planner handoff" in rendered
    assert "Add regression tests" in rendered
    assert "ralph/pipeline/runner.py" in rendered
    assert '"type":"plan"' not in rendered


def test_materialize_development_prefers_structured_plan_artifact_over_plan_md(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement unattended planning recovery")
    workspace.write(".agent/PLAN.md", "STALE PLAN")
    workspace.write(
        ".agent/artifacts/plan.json",
        (
            '{"type":"plan","content":'
            '{"summary":{"context":"Fresh structured plan","scope_items":['
            '{"text":"Use policy-driven handoff"},'
            '{"text":"Reject missing plan artifacts"}]},'
            '"steps":[{"number":1,"title":"Honor policy","content":"Route via policy bundle"}],'
            '"critical_files":{"primary_files":[]},'
            '"risks_mitigations":[],"verification_strategy":[]}}'
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "Fresh structured plan" in rendered
    assert "STALE PLAN" not in rendered
