from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

from git import Repo as GitRepo

import ralph.prompts.materialize as materialize_module
from ralph.mcp.artifacts.history import (
    history_dir_for_artifact,
    history_index_path,
)
from ralph.pipeline.cycle_baseline import write_cycle_baseline
from ralph.policy.loader import load_policy
from ralph.prompts.debug_dump import media_session_path, multimodal_sidecar_path
from ralph.prompts.materialize import (
    MultimodalSidecarEntry,
    PromptPhaseContext,
    PromptPhaseOptions,
    collect_media_entries_for_phase,
    materialize_prompt_for_phase,
    resolve_planning_history_path,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    import pytest



class _ArtifactSubmitSession:
    session_id = "test-session"
    drain = "planning_analysis"

    def check_capability(self, capability: str) -> object:
        return capability == "artifact.submit"




PLANNING_EDIT_GET_DRAFT_TEXT = (
    "Use `ralph_get_plan_draft` to inspect the current finalized plan "
    "or staged draft before editing."
)
PLANNING_EDIT_DEFECT_SCOPE_TEXT = (
    "Before revising any section, classify the feedback scope as one of:"
)
PLANNING_EDIT_GLOBAL_REDERIVATION_TEXT = (
    "If any feedback item reveals repo-wide incompleteness, invalid inventory, incorrect paths, "
    "narrow verification, or prompt-to-plan traceability gaps, you MUST re-derive the plan"
)
PLANNING_EDIT_FINALIZE_TEXT = (
    "Use `ralph_finalize_plan` after revising the affected sections so "
    "the updated plan replaces the prior finalized plan."
)
PLANNING_EDIT_SELF_AUDIT_TEXT = "Before `ralph_finalize_plan`, perform this self-audit:"
PLANNING_EDIT_RISK_COVERAGE_TEXT = (
    "- Risk coverage: concrete risks, mitigations, and edge cases are represented"
)
PLANNING_EDIT_PARALLELIZATION_TEXT = (
    "- Parallelization safety: any parallel work remains disjoint, realistic, and policy-compliant"
)
PLANNING_EDIT_MAINTAINABILITY_TEXT = (
    "- Maintainability and handoff quality: the plan stays concise, "
    "non-redundant, and explicit for development handoff"
)
PLANNING_EDIT_SCOPE_INVALIDATION_TEXT = (
    "If the ORIGINAL REQUEST has repository-wide acceptance criteria and the current plan "
    "narrowed scope before running repository-wide discovery"
)
PLANNING_EDIT_DISCOVERY_FIRST_TEXT = (
    "replace the summary, scope, and early steps so Step 1 becomes repo-wide discovery"
)
PLANNING_EDIT_SCOPE_DERIVATION_TEXT = (
    "- Scope derivation: when the task is repo-wide, implementation scope comes from an "
    "explicit repo-wide discovery step rather than a guessed subsystem"
)
PLANNING_EDIT_PASS_TARGET_TEXT = (
    "Your target is to submit the strongest revised plan you can so the next planning-analysis pass"
)
PLANNING_EDIT_NO_KNOWN_GAPS_TEXT = (
    "Do not finalize a draft that still has any known unresolved analyzer finding"
)
PLANNING_EDIT_DEPENDENT_SECTION_REWRITE_TEXT = (
    "If fixing one section changes the truth of another section, replace every dependent section"
)
PLANNING_EDIT_NEXT_ANALYZER_TEXT = (
    "Before finalizing, proactively search for any additional repo-grounded failure"
)
PLANNING_EDIT_SURFACED_BLOCKER_TEXT = (
    "If a canonical verification command or repo-wide audit already surfaces a blocker "
    "during replanning"
)
PLANNING_EDIT_RULE_CATEGORY_TEXT = (
    "When the ORIGINAL REQUEST imposes repo-wide structural rules, build a repo-wide inventory"
)
PLANNING_EDIT_NO_EXCEPTION_TEXT = (
    "Do not preserve prompt-violating tests, files, or workflows as justified exceptions"
)
PLANNING_EDIT_STARTING_POINT_TEXT = (
    "Treat the planning-analysis feedback as a starting point, not as the full list of issues"
)
PLANNING_EDIT_NOT_LOCAL_PATCH_TEXT = (
    "Do not localize your revision pass to only the sections explicitly cited by the analyzer"
)
PLANNING_EDIT_SELF_ANALYSIS_TEXT = (
    "You must perform your own repo-grounded analysis before finalizing"
)
PLANNING_EDIT_ISSUE_MAPPING_TEXT = (
    "Every analyzer issue must map to concrete revised sections or an explicit verified reason"
)
PLANNING_ANALYSIS_MCP_REMEDIATION_TEXT = (
    "When describing remediation, target the planner's MCP revision workflow"
)
PLANNING_ANALYSIS_SECTION_RESUBMIT_TEXT = (
    "Exact plan sections to resubmit via the MCP plan-edit tools."
)

if TYPE_CHECKING:
    from pathlib import Path


MINIMAL_PLAN_HANDOFF = (
    "# Execution Plan\n\n"
    "1. Add regression coverage.\n"
    "2. Tighten non-planning prompt preconditions.\n"
)


def _write_plan_handoff(workspace: MemoryWorkspace) -> None:
    workspace.write(".agent/PLAN.md", MINIMAL_PLAN_HANDOFF)


def _make_sidecar_entry(
    *,
    artifact_id: str = "abc123",
    uri: str = "ralph://media/abc123",
    modality: str = "image",
    title: str = "screenshot.png",
    mime_type: str = "image/png",
    delivery: str = "inline_image",
) -> MultimodalSidecarEntry:
    return MultimodalSidecarEntry(
        artifact_id=artifact_id,
        uri=uri,
        mime_type=mime_type,
        title=title,
        modality=modality,
        delivery=delivery,
        reason="Claude supports inline image delivery",
        source_path=".agent/tmp/media/screenshot.png",
        cache_path=".agent/tmp/media/screenshot.png",
        source_uri="",
        block_type="",
    )


def test_pending_diff_shows_only_uncommitted_work(tmp_git_repo: Path) -> None:
    with GitRepo(tmp_git_repo) as repo:
        baseline_sha = repo.head.commit.hexsha
        write_cycle_baseline(tmp_git_repo, baseline_sha)

        (tmp_git_repo / "committed.py").write_text("committed = True\n")
        repo.index.add(["committed.py"])
        repo.index.commit("mid-cycle commit")

        (tmp_git_repo / "pending.py").write_text("pending = True\n")
        repo.index.add(["pending.py"])

    diff = materialize_module._pending_diff(tmp_git_repo)

    assert "pending.py" in diff
    assert "committed.py" not in diff


def test_commit_phase_prompt_excludes_mid_cycle_committed_files(
    tmp_git_repo: Path,
) -> None:
    with GitRepo(tmp_git_repo) as repo:
        baseline_sha = repo.head.commit.hexsha
        write_cycle_baseline(tmp_git_repo, baseline_sha)

        (tmp_git_repo / "already_committed.py").write_text("x = 1\n")
        repo.index.add(["already_committed.py"])
        repo.index.commit("earlier dev commit")

        (tmp_git_repo / "new_pending.py").write_text("y = 2\n")
        repo.index.add(["new_pending.py"])

    policy = load_policy(tmp_git_repo / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_git_repo))
    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_commit",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
            workspace_root=tmp_git_repo,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "new_pending.py" in rendered
    assert "already_committed.py" not in rendered


def test_pending_diff_falls_back_when_not_a_git_repo(tmp_path: Path) -> None:
    diff = materialize_module._pending_diff(tmp_path)
    assert diff == "(no diff available)"


def test_materialize_commit_phase_includes_untracked_files_for_initial_repo(
    tmp_path: Path,
) -> None:
    repo = GitRepo.init(tmp_path)
    try:
        policy = load_policy(tmp_path / ".agent")
        workspace = MemoryWorkspace(root=str(tmp_path))
        (tmp_path / "new_file.py").write_text("value = 1\n", encoding="utf-8")

        prompt_path = materialize_prompt_for_phase(
            PromptPhaseContext(
                phase="development_commit",
                workspace=workspace,
                pipeline_policy=policy.pipeline,
                session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
                workspace_root=tmp_path,
            ),
        )

        rendered = workspace.read(prompt_path)
        assert "new_file.py" in rendered
        assert "(no diff available)" not in rendered
    finally:
        repo.close()


def test_git_diff_strips_lone_surrogates_from_gitpython_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    surrogate_diff = "diff --git a/x.py b/x.py\n+\udca4\n"
    monkeypatch.setattr(
        materialize_module,
        "_pending_diff",
        lambda _workspace_root: surrogate_diff,
    )

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_commit",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
            workspace_root=tmp_path,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "\udca4" not in rendered
    rendered.encode("utf-8")  # must not raise


def test_materialize_commit_phase_with_oversized_surrogate_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    big_surrogate = "\udca4" + ("x" * (100 * 1024 + 1))
    monkeypatch.setattr(
        materialize_module,
        "_pending_diff",
        lambda _workspace_root: big_surrogate,
    )

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_commit",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
            workspace_root=tmp_path,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "\udca4" not in rendered
    payload_path = tmp_path / ".agent" / "tmp" / "prompt_payloads" / "development_commit_diff.txt"
    assert payload_path.exists()
    written = payload_path.read_text(encoding="utf-8")
    assert "\udca4" not in written


def test_development_analysis_prompt_renders_without_development_result(
    tmp_path: Path,
) -> None:
    """development_analysis prompt must render even when development_result.json is absent.

    development_result is required by default policy, but prompt generation must not
    crash when the artifact is absent on disk. This test exercises template rendering
    only, not artifact validation — the analysis agent must still receive a complete
    prompt even when development_result.json is missing.
    """
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "implement the feature")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "ctx",
                        "scope_items": [
                            {"text": "item one"},
                            {"text": "item two"},
                            {"text": "item three"},
                        ],
                    },
                    "steps": [{"number": 1, "title": "step", "content": "do it"}],
                    "critical_files": {"primary_files": [{"path": "src/a.py", "action": "modify"}]},
                    "risks_mitigations": [{"risk": "r", "mitigation": "m"}],
                    "verification_strategy": [{"method": "run tests", "expected_outcome": "pass"}],
                    "work_units": [
                        {"unit_id": "u1", "description": "do stuff", "allowed_directories": ["src"]}
                    ],
                },
            }
        ),
    )
    # Intentionally do NOT write development_result.json

    with patch.object(materialize_module, "_git_diff", return_value="diff --git a/x.py"):
        prompt_path = materialize_prompt_for_phase(
            PromptPhaseContext(
                phase="development_analysis",
                workspace=workspace,
                pipeline_policy=policy.pipeline,
                session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(
                artifacts_policy=policy.artifacts,
            ),
        )

    rendered = workspace.read(prompt_path)
    assert rendered, "Prompt must not be empty"
    # render_payload_path emits a file reference, not inlined content — check the path appears
    assert str(tmp_path / ".agent" / "CURRENT_PROMPT.md") in rendered, (
        "Prompt must reference the CURRENT_PROMPT path"
    )
    # Plan is referenced via its Markdown handoff (.agent/PLAN.md), not the JSON artifact path
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered, (
        "Prompt must reference the plan handoff path"
    )


def test_fresh_planning_clears_all_artifact_history_on_entry(
    tmp_path: Path,
) -> None:
    """Fresh planning entry clears all artifact history before a new planning cycle."""

    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the new feature")

    # Create history files on disk (bypass MemoryWorkspace)
    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    archived_plan_json = plan_hist_dir / "20260506T120000_plan.json"
    archived_plan_json.write_text('{"type":"plan"}', encoding="utf-8")
    plan_index_file = history_index_path(artifact_dir, "plan")
    plan_index_file.write_text("# History", encoding="utf-8")

    development_hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    development_hist_dir.mkdir(parents=True, exist_ok=True)
    archived_development_json = development_hist_dir / "20260506T120000_development_result.json"
    archived_development_json.write_text('{"type":"development_result"}', encoding="utf-8")
    development_index_file = history_index_path(artifact_dir, "development_result")
    development_index_file.write_text("# History", encoding="utf-8")

    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
        ),
    )

    assert not archived_plan_json.exists(), (
        "plan archive json must be removed on fresh planning entry"
    )
    assert not plan_index_file.exists(), (
        "plan history index must be removed on fresh planning entry"
    )
    assert not archived_development_json.exists(), (
        "development archive json must be removed on fresh planning entry"
    )
    assert not development_index_file.exists(), (
        "development history index must be removed on fresh planning entry"
    )


def test_resolve_planning_history_path_returns_empty_when_no_index(tmp_path: Path) -> None:
    """Returns empty string when no history index exists."""

    result = resolve_planning_history_path(tmp_path)
    assert result == ""


def test_resolve_planning_history_path_returns_path_when_index_exists(tmp_path: Path) -> None:
    """Returns the index path string when the history index file exists."""

    artifact_dir = tmp_path / ".agent" / "artifacts"
    index = history_index_path(artifact_dir, "plan")
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("# History", encoding="utf-8")

    result = resolve_planning_history_path(tmp_path)
    assert result == str(index)


def test_planning_loopback_from_analysis_preserves_history(
    tmp_path: Path,
) -> None:
    """Planning loopback from planning_analysis must not clear artifact history."""

    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the new feature")

    # Write plan + analysis feedback so the loopback prompt can render
    plan_artifact = {
        "type": "plan",
        "content": {
            "summary": {
                "context": "ctx",
                "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
            },
            "steps": [
                {
                    "number": 1,
                    "title": "t",
                    "content": "c",
                    "step_type": "file_change",
                    "priority": "high",
                    "targets": [{"path": "f.py", "action": "modify"}],
                    "depends_on": [],
                }
            ],
            "critical_files": {
                "primary_files": [{"path": "f.py", "action": "modify"}],
                "reference_files": [],
            },
            "risks_mitigations": [{"risk": "r", "mitigation": "m", "severity": "low"}],
            "verification_strategy": [{"method": "make test", "expected_outcome": "green"}],
        },
    }
    analysis_artifact = {
        "type": "planning_analysis_decision",
        "content": {
            "status": "request_changes",
            "summary": "Revise the plan.",
            "what_came_up_short": ["Verification is weak."],
            "how_to_fix": ["Add exact commands."],
        },
    }

    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(plan_artifact),
    )
    workspace.write(
        ".agent/artifacts/planning_analysis_decision.json",
        json.dumps(analysis_artifact),
    )

    # Create history files on disk (bypass MemoryWorkspace)
    artifact_dir = tmp_path / ".agent" / "artifacts"
    hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    hist_dir.mkdir(parents=True, exist_ok=True)
    archived_json = hist_dir / "20260506T120000_plan.json"
    archived_json.write_text('{"type":"plan"}', encoding="utf-8")
    index_file = history_index_path(artifact_dir, "plan")
    index_file.write_text("# History", encoding="utf-8")

    with patch.object(materialize_module, "_git_diff", return_value="diff"):
        materialize_prompt_for_phase(
            PromptPhaseContext(
                phase="planning",
                workspace=workspace,
                pipeline_policy=policy.pipeline,
                session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(
                artifacts_policy=policy.artifacts,
                previous_phase="planning_analysis",
            ),
        )

    assert archived_json.exists(), "archive json must be preserved on planning loopback"
    assert index_file.exists(), "history index must be preserved on planning loopback"


def test_missing_history_does_not_break_fresh_planning(
    tmp_path: Path,
) -> None:
    """Fresh planning entry with no prior history directory must not raise."""
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the new feature")

    # No history files exist at all — history directory does not exist
    artifact_dir = tmp_path / ".agent" / "artifacts"
    assert not (artifact_dir / "history").exists()

    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
        ),
    )
    # Must complete without error; no history is also fine
    assert not (artifact_dir / "history").exists()


def test_multimodal_sidecar_path_is_deterministic_from_phase() -> None:
    assert (
        multimodal_sidecar_path("development") == ".agent/tmp/development_multimodal_handoff.json"
    )
    assert multimodal_sidecar_path("planning") == ".agent/tmp/planning_multimodal_handoff.json"
    assert multimodal_sidecar_path("foo/bar") == ".agent/tmp/foo_bar_multimodal_handoff.json"
    assert multimodal_sidecar_path("foo bar") == ".agent/tmp/foo_bar_multimodal_handoff.json"


def test_materialize_with_no_multimodal_entries_does_not_create_sidecar(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Build the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nStep 1.\n")

    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
            multimodal_entries=None,
        ),
    )

    assert not workspace.exists(multimodal_sidecar_path("development"))


def test_materialize_with_empty_multimodal_entries_does_not_create_sidecar(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Build the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nStep 1.\n")

    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
            multimodal_entries=[],
        ),
    )

    assert not workspace.exists(multimodal_sidecar_path("development"))


def test_materialize_with_multimodal_entries_creates_sidecar(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Build the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nStep 1.\n")

    entry = _make_sidecar_entry()
    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
            multimodal_entries=[entry],
        ),
    )

    sidecar_path = multimodal_sidecar_path("development")
    assert workspace.exists(sidecar_path)
    data = json.loads(workspace.read(sidecar_path))
    assert data["schema_version"] == "2"
    assert data["phase"] == "development"
    assert len(data["artifacts"]) == 1
    art = data["artifacts"][0]
    assert art["artifact_id"] == "abc123"
    assert art["uri"] == "ralph://media/abc123"
    assert art["mime_type"] == "image/png"
    assert art["title"] == "screenshot.png"
    assert art["modality"] == "image"
    assert art["delivery"] == "inline_image"
    assert art["reason"] == "Claude supports inline image delivery"
    assert "source_path" in art
    assert "cache_path" in art
    assert "source_uri" in art
    assert "block_type" in art


def test_materialize_sidecar_contains_all_artifacts(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Build the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nStep 1.\n")

    entries = [
        _make_sidecar_entry(
            artifact_id="img1",
            uri="ralph://media/img1",
            modality="image",
            title="screen.png",
        ),
        _make_sidecar_entry(
            artifact_id="pdf1",
            uri="ralph://media/pdf1",
            modality="pdf",
            title="doc.pdf",
            mime_type="application/pdf",
            delivery="resource_reference_replay",
        ),
    ]
    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
            multimodal_entries=entries,
        ),
    )

    data = json.loads(workspace.read(multimodal_sidecar_path("development")))
    assert len(data["artifacts"]) == 2
    assert data["artifacts"][0]["artifact_id"] == "img1"
    assert data["artifacts"][1]["artifact_id"] == "pdf1"


def test_stale_sidecar_is_cleared_on_text_only_run(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Build the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nStep 1.\n")

    # First run: multimodal
    entry = _make_sidecar_entry()
    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
            multimodal_entries=[entry],
        ),
    )
    sidecar_path = multimodal_sidecar_path("development")
    assert workspace.exists(sidecar_path)

    # Second run: text-only (no entries)
    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
            multimodal_entries=None,
        ),
    )

    assert not workspace.exists(sidecar_path), "Stale sidecar must be removed on text-only run"


def test_v1_sidecar_is_read_with_defaults_for_new_fields(
    tmp_path: Path,
) -> None:
    """v1 sidecars (no source_path/cache_path/source_uri/block_type) must load without error."""
    workspace = MemoryWorkspace(root=str(tmp_path))
    v1_payload = json.dumps(
        {
            "schema_version": "1",
            "phase": "development",
            "artifacts": [
                {
                    "artifact_id": "old-id",
                    "uri": "ralph://media/old-id",
                    "mime_type": "image/png",
                    "title": "old.png",
                    "modality": "image",
                    "delivery": "resource_reference",
                    "reason": "prior run",
                }
            ],
        }
    )

    workspace.write(media_session_path("development"), v1_payload)

    entries = collect_media_entries_for_phase(workspace, "development")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.artifact_id == "old-id"
    assert entry.delivery == "resource_reference"
    assert entry.source_path == ""
    assert entry.cache_path == ""
    assert entry.source_uri == ""
    assert entry.block_type == ""


def test_v2_sidecar_persists_all_new_fields(
    tmp_path: Path,
) -> None:
    """v2 entries must round-trip all new metadata fields through sidecar."""
    workspace = MemoryWorkspace(root=str(tmp_path))
    v2_payload = json.dumps(
        {
            "schema_version": "2",
            "phase": "development",
            "artifacts": [
                {
                    "artifact_id": "new-id",
                    "uri": "ralph://media/new-id",
                    "mime_type": "application/pdf",
                    "title": "doc.pdf",
                    "modality": "pdf",
                    "delivery": "typed_block",
                    "reason": "Claude typed PDF",
                    "source_path": "reports/doc.pdf",
                    "cache_path": ".agent/tmp/media/doc.pdf",
                    "source_uri": "",
                    "block_type": "pdf",
                }
            ],
        }
    )

    workspace.write(media_session_path("development"), v2_payload)

    entries = collect_media_entries_for_phase(workspace, "development")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.delivery == "typed_block"
    assert entry.source_path == "reports/doc.pdf"
    assert entry.cache_path == ".agent/tmp/media/doc.pdf"
    assert entry.block_type == "pdf"


def test_materialize_sidecar_preserves_delivery_reason_and_block_type_for_mixed_modalities() -> (
    None
):
    """Sidecar round-trip must preserve delivery, reason, and block_type for all modality classes.

    The managed-runtime path carries these fields from the MCP session index through
    the sidecar so prompt-materialization and invoke-time appendix code can use them
    without re-deriving capability information.
    """

    workspace = MemoryWorkspace()
    payload = json.dumps(
        {
            "schema_version": "2",
            "phase": "development",
            "artifacts": [
                {
                    "artifact_id": "img-rr",
                    "uri": "ralph://media/img-rr",
                    "mime_type": "image/png",
                    "title": "capture.png",
                    "modality": "image",
                    "delivery": "resource_reference_replay",
                    "reason": "unknown provider — defaulting to resource_reference_replay delivery",
                    "source_path": "",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": "",
                },
                {
                    "artifact_id": "pdf-tb",
                    "uri": "ralph://media/pdf-tb",
                    "mime_type": "application/pdf",
                    "title": "spec.pdf",
                    "modality": "pdf",
                    "delivery": "typed_block",
                    "reason": "'pdf' delivered as typed block 'pdf' for provider 'claude'",
                    "source_path": "docs/spec.pdf",
                    "cache_path": ".agent/tmp/media/spec.pdf",
                    "source_uri": "",
                    "block_type": "pdf",
                },
                {
                    "artifact_id": "aud-rr",
                    "uri": "ralph://media/aud-rr",
                    "mime_type": "audio/mpeg",
                    "title": "meeting.mp3",
                    "modality": "audio",
                    "delivery": "resource_reference_replay",
                    "reason": "unknown provider — defaulting to resource_reference_replay delivery",
                    "source_path": "audio/meeting.mp3",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": "",
                },
            ],
        }
    )
    workspace.write(media_session_path("development"), payload)

    entries = collect_media_entries_for_phase(workspace, "development")

    assert len(entries) == 3, f"Expected 3 entries, got {len(entries)}"

    by_modality = {e.modality: e for e in entries}

    # Image: resource_reference_replay delivery + empty block_type
    img = by_modality["image"]
    assert img.delivery == "resource_reference_replay"
    assert img.reason != "", "reason must not be empty for image entry"
    assert img.block_type == ""

    # PDF: typed_block delivery + non-empty block_type + source_path preserved
    pdf = by_modality["pdf"]
    assert pdf.delivery == "typed_block"
    assert pdf.block_type == "pdf"
    assert pdf.reason != "", "reason must not be empty for PDF typed_block"
    assert pdf.source_path == "docs/spec.pdf"
    assert pdf.cache_path == ".agent/tmp/media/spec.pdf"

    # Audio: resource_reference_replay delivery preserved
    aud = by_modality["audio"]
    assert aud.delivery == "resource_reference_replay"
    assert aud.modality == "audio"
    assert aud.block_type == ""
