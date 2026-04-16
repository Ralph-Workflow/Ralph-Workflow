from __future__ import annotations

import json
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
    current_prompt_path = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    assert str(current_prompt_path) in rendered
    assert current_prompt_path.read_text(encoding="utf-8") == "Plan the template migration"


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


def test_materialize_commit_phase_with_claude_prefix_includes_both_tool_aliases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    monkeypatch.setattr(
        materialize_module,
        "_git_diff",
        lambda _workspace_root: "diff --git a/app.py b/app.py\n+hello",
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development_commit",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(
            SessionDrain.COMMIT,
            tool_name_prefix="mcp__ralph__",
        ),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "mcp__ralph__ralph_submit_artifact" in rendered
    assert "or `ralph_submit_artifact`" in rendered


def test_materialize_development_phase_surfaces_bare_fallbacks_for_shared_mcp_tools(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {"context": "ctx", "scope_items": [{"text": "a"}]},
                    "steps": [
                        {
                            "number": 1,
                            "title": "step",
                            "content": "do it",
                            "step_type": "file_change",
                            "priority": "high",
                            "targets": [],
                            "depends_on": [],
                        }
                    ],
                    "critical_files": {"primary_files": [], "reference_files": []},
                    "risks_mitigations": [],
                    "verification_strategy": [],
                    "work_units": [],
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(
            SessionDrain.DEVELOPMENT,
            tool_name_prefix="mcp__ralph__",
        ),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "`mcp__ralph__write_file` or bare `write_file`" in rendered
    assert "`mcp__ralph__exec` or bare `exec`" in rendered
    assert "`mcp__ralph__report_progress` or bare `report_progress`" in rendered
    assert "`mcp__ralph__declare_complete` or bare `declare_complete`" in rendered


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
    current_prompt_path = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    assert str(current_prompt_path) in rendered
    assert (
        current_prompt_path.read_text(encoding="utf-8") == "Implement unattended planning recovery"
    )
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
    assert (tmp_path / ".agent" / "CURRENT_PROMPT.md").read_text(encoding="utf-8") == (
        "Implement unattended planning recovery"
    )


def test_materialize_planning_prompt_uses_file_reference_for_large_prompt(tmp_path: Path) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    large_prompt = "P" * (100 * 1024 + 1)
    workspace.write("PROMPT.md", large_prompt)

    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    payload_path = tmp_path / ".agent" / "tmp" / "prompt_payloads" / "planning_prompt.txt"
    assert str(tmp_path / ".agent" / "CURRENT_PROMPT.md") in rendered
    assert large_prompt not in rendered
    assert (tmp_path / ".agent" / "CURRENT_PROMPT.md").read_text(encoding="utf-8") == large_prompt
    assert not payload_path.exists()


def test_materialize_review_prompt_uses_file_reference_for_large_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Review the changes")
    large_diff = "D" * (100 * 1024 + 1)

    monkeypatch.setattr(materialize_module, "_git_diff", lambda _workspace_root: large_diff)

    prompt_path = materialize_prompt_for_phase(
        phase="review",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.REVIEW),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    payload_path = tmp_path / ".agent" / "tmp" / "prompt_payloads" / "review_diff.txt"
    assert "read the complete changes from file at" in rendered.lower()
    assert (tmp_path / ".agent" / "CURRENT_PROMPT.md").read_text(encoding="utf-8") == (
        "Review the changes"
    )
    assert large_diff not in rendered
    assert payload_path.read_text(encoding="utf-8") == large_diff
