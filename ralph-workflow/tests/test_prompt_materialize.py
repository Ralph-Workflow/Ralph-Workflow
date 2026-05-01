from __future__ import annotations

import json
from typing import TYPE_CHECKING

from git import Repo as GitRepo

import ralph.prompts.materialize as materialize_module
from ralph.pipeline.cycle_baseline import write_cycle_baseline
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
        artifacts_policy=policy.artifacts,
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
                    "summary": {
                        "context": "ctx",
                        "scope_items": [
                            {"text": "a"},
                            {"text": "b"},
                            {"text": "c"},
                        ],
                    },
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
                    "critical_files": {
                        "primary_files": [{"path": "src/app.py", "action": "modify"}],
                        "reference_files": [],
                    },
                    "risks_mitigations": [{"risk": "regression", "mitigation": "add tests"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                    "work_units": [],
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
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


def test_materialize_development_prompt_reads_agent_plan_markdown_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement unattended planning recovery")
    workspace.write(
        ".agent/PLAN.md",
        "# Implementation Plan\n\n## Steps\n1. Add regression tests\n2. Fix pipeline routing\n",
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    current_prompt_path = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    assert str(current_prompt_path) in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered
    assert "Read the complete implementation plan from file at" in rendered
    assert "Add regression tests" not in rendered


def test_materialize_development_prompt_uses_analysis_feedback_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(
        ".agent/artifacts/development_analysis_decision.json",
        json.dumps(
            {
                "type": "development_analysis_decision",
                "content": {
                    "status": "request_changes",
                    "summary": "Need another iteration.",
                    "what_came_up_short": ["Feedback is hidden from the developer."],
                    "how_to_fix": ["Read the analysis handoff before editing."],
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "DEVELOPMENT_ANALYSIS_DECISION.md") in rendered
    assert "Read the complete analysis feedback from file at" in rendered
    assert "This file is the authoritative source for analysis feedback in this prompt." in rendered
    assert "Need another iteration." not in rendered


def test_materialize_development_analysis_uses_markdown_result_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Analyze the implementation")
    workspace.write(
        ".agent/artifacts/development_result.json",
        json.dumps(
            {
                "type": "development_result",
                "content": {
                    "status": "completed",
                    "summary": "Implemented the feature.",
                    "files_changed": "- src/app.py",
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development_analysis",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md") in rendered
    assert "Read the complete latest artifact from file at" in rendered
    assert "Implemented the feature." not in rendered


def test_materialize_fix_prompt_uses_markdown_issues_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Apply the fixes")
    workspace.write(
        ".agent/artifacts/issues.json",
        json.dumps(
            {
                "type": "issues",
                "content": {
                    "status": "issues_found",
                    "summary": "Review found gaps.",
                    "issues": [
                        {
                            "path": "ralph/pipeline/runner.py",
                            "severity": "high",
                            "summary": "Need better visibility.",
                        }
                    ],
                    "what_came_up_short": ["User cannot see the review handoff."],
                    "how_to_fix": ["Mirror issues.json to ISSUES.md."],
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        phase="fix",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.FIX),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "ISSUES.md") in rendered
    assert "Read the complete issues from file at" in rendered
    assert "Need better visibility." not in rendered


def test_materialize_fix_prompt_uses_review_analysis_feedback_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Apply the fixes")
    workspace.write(
        ".agent/artifacts/review_analysis_decision.json",
        json.dumps(
            {
                "type": "review_analysis_decision",
                "content": {
                    "status": "request_changes",
                    "summary": "Fixes are required.",
                    "what_came_up_short": ["The fixer cannot see review-analysis feedback."],
                    "how_to_fix": ["Read the review analysis handoff first."],
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        phase="fix",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.FIX),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "REVIEW_ANALYSIS_DECISION.md") in rendered
    assert "Read the complete analysis feedback from file at" in rendered
    assert "Fixes are required." not in rendered


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
            '{"text":"Reject missing plan artifacts"},'
            '{"text":"Expose the handoff to users"}]},'
            '"steps":[{"number":1,"title":"Honor policy","content":"Route via policy bundle"}],'
            '"critical_files":{"primary_files":['
            '{"path":"ralph/pipeline/runner.py","action":"modify"}]},'
            '"risks_mitigations":['
            '{"risk":"Stale markdown handoff",'
            '"mitigation":"Rewrite PLAN.md from plan.json"}],'
            '"verification_strategy":['
            '{"method":"pytest",'
            '"expected_outcome":"development prompt uses PLAN.md handoff"}]}}'
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered
    assert "Read the complete implementation plan from file at" in rendered
    assert (
        "This file is the authoritative source for implementation plan in this prompt." in rendered
    )
    assert "STALE PLAN" not in rendered
    assert "Fresh structured plan" not in rendered
    assert (tmp_path / ".agent" / "PLAN.md").read_text(encoding="utf-8") != "STALE PLAN"
    assert "Fresh structured plan" in (tmp_path / ".agent" / "PLAN.md").read_text(encoding="utf-8")
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
        artifacts_policy=policy.artifacts,
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


def test_git_diff_uses_start_commit_sha_when_present(tmp_git_repo: Path) -> None:
    repo = GitRepo(tmp_git_repo)
    baseline_sha = repo.head.commit.hexsha

    new_file = tmp_git_repo / "feature.py"
    new_file.write_text("x = 1\n")
    repo.index.add(["feature.py"])
    repo.index.commit("add feature")

    agent_dir = tmp_git_repo / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "start_commit").write_text(baseline_sha + "\n")

    diff = materialize_module._git_diff(tmp_git_repo)

    assert "feature.py" in diff


def test_git_diff_falls_back_to_head_when_start_commit_absent(tmp_git_repo: Path) -> None:
    repo = GitRepo(tmp_git_repo)
    uncommitted = tmp_git_repo / "work.py"
    uncommitted.write_text("y = 2\n")
    repo.index.add(["work.py"])

    diff = materialize_module._git_diff(tmp_git_repo)

    assert "work.py" in diff


def test_git_diff_cumulative_across_multiple_mid_cycle_commits(tmp_git_repo: Path) -> None:
    repo = GitRepo(tmp_git_repo)
    baseline_sha = repo.head.commit.hexsha
    (tmp_git_repo / "file_a.py").write_text("a = 1\n")
    repo.index.add(["file_a.py"])
    repo.index.commit("mid-cycle commit 1")
    (tmp_git_repo / "file_b.py").write_text("b = 2\n")
    repo.index.add(["file_b.py"])
    repo.index.commit("mid-cycle commit 2")
    (tmp_git_repo / "file_c.py").write_text("c = 3\n")
    repo.index.add(["file_c.py"])
    write_cycle_baseline(tmp_git_repo, baseline_sha)
    diff = materialize_module._git_diff(tmp_git_repo)
    assert "file_a.py" in diff
    assert "file_b.py" in diff
    assert "file_c.py" in diff

def test_git_diff_zero_mid_cycle_commits_only_uncommitted(tmp_git_repo: Path) -> None:
    repo = GitRepo(tmp_git_repo)
    baseline_sha = repo.head.commit.hexsha
    write_cycle_baseline(tmp_git_repo, baseline_sha)
    (tmp_git_repo / "uncommitted.py").write_text("u = 99\n")
    repo.index.add(["uncommitted.py"])
    diff = materialize_module._git_diff(tmp_git_repo)
    assert "uncommitted.py" in diff


def test_git_diff_many_mid_cycle_commits_no_uncommitted(tmp_git_repo: Path) -> None:
    repo = GitRepo(tmp_git_repo)
    baseline_sha = repo.head.commit.hexsha
    (tmp_git_repo / "commit_only_a.py").write_text("a = 1\n")
    repo.index.add(["commit_only_a.py"])
    repo.index.commit("mid-cycle commit A")
    (tmp_git_repo / "commit_only_b.py").write_text("b = 2\n")
    repo.index.add(["commit_only_b.py"])
    repo.index.commit("mid-cycle commit B")
    write_cycle_baseline(tmp_git_repo, baseline_sha)
    diff = materialize_module._git_diff(tmp_git_repo)
    assert "commit_only_a.py" in diff
    assert "commit_only_b.py" in diff


def test_git_diff_strips_lone_surrogates_from_gitpython_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    surrogate_diff = "diff --git a/file.txt b/file.txt\n@@\n+\udca4 byte\n"

    class _FakeGit:
        def diff(self, *_args: object, **_kwargs: object) -> str:
            return surrogate_diff

    class _FakeRepo:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.git = _FakeGit()

    monkeypatch.setattr(materialize_module, "Repo", _FakeRepo)

    diff = materialize_module._git_diff(tmp_path)

    assert "\udca4" not in diff
    diff.encode("utf-8")  # must not raise


def test_materialize_commit_phase_handles_surrogate_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    surrogate_diff = "diff --git a/x.py b/x.py\n+\udca4\n"
    monkeypatch.setattr(
        materialize_module,
        "_git_diff",
        lambda _workspace_root: surrogate_diff,
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development_commit",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "\udca4" not in rendered
    rendered.encode("utf-8")  # must not raise


def test_materialize_commit_phase_with_oversized_surrogate_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    big_surrogate = "\udca4" + ("x" * (100 * 1024 + 1))
    monkeypatch.setattr(
        materialize_module,
        "_git_diff",
        lambda _workspace_root: big_surrogate,
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development_commit",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "\udca4" not in rendered
    payload_path = tmp_path / ".agent" / "tmp" / "prompt_payloads" / "development_commit_diff.txt"
    assert payload_path.exists()
    written = payload_path.read_text(encoding="utf-8")
    assert "\udca4" not in written
