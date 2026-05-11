"""Tests for honest partial-failure reporting in the parallel development summary."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.runner import _write_parallel_development_summary
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.workspace.scope import WorkspaceScope

_EXIT_CODE_VERIFY_FAIL = 2

if TYPE_CHECKING:
    from pathlib import Path


def _make_scope(tmp_path: Path) -> WorkspaceScope:
    return WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))


def _make_effect(*unit_specs: tuple[str, list[str]]) -> FanOutEffect:
    units = tuple(
        WorkUnit(unit_id=uid, description=f"Unit {uid}", allowed_directories=dirs)
        for uid, dirs in unit_specs
    )
    return FanOutEffect(work_units=units, max_workers=len(units))


def _write_fake_artifact(tmp_path: Path, unit_id: str) -> None:
    artifact_dir = tmp_path / ".agent" / "workers" / unit_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "result.json").write_text(
        json.dumps(
            {
                "name": "development_result",
                "type": "development_result",
                "content": {"summary": f"done by {unit_id}"},
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "metadata": {},
            }
        )
    )


def _read_summary(tmp_path: Path) -> dict[str, object]:
    summary_path = tmp_path / ".agent" / "artifacts" / "parallel_development_summary.json"
    assert summary_path.exists(), f"Summary not written: {summary_path}"
    return json.loads(summary_path.read_text())


class TestCombinedSummaryHonesty:
    def test_any_failed_when_one_worker_fails(self, tmp_path: Path) -> None:
        """When unit-b fails (no artifact), combined summary must have any_failed=true."""
        effect = _make_effect(("unit-a", ["src/a"]), ("unit-b", ["src/b"]))

        # unit-a succeeded (write artifact), unit-b failed (no artifact)
        _write_fake_artifact(tmp_path, "unit-a")

        state = PipelineState(
            phase="development",
            worker_states={
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(
                    unit_id="unit-b",
                    status=WorkerStatus.FAILED,
                    error_message="Worker 'unit-b' produced no worker-local artifact evidence",
                ),
            },
        )
        scope = _make_scope(tmp_path)
        _write_parallel_development_summary(
            scope,
            effect,
            state,
            verify_ran=False,
            verify_passed=None,
            verify_exit_code=None,
        )

        summary = _read_summary(tmp_path)
        assert summary["any_failed"] is True
        assert summary["all_succeeded"] is False
        workers = {w["unit_id"]: w for w in summary["workers"]}
        assert workers["unit-a"]["status"] == "succeeded"
        assert workers["unit-b"]["status"] == "failed"

    def test_failed_worker_does_not_inherit_success_from_sibling(self, tmp_path: Path) -> None:
        """Worker B with no artifact must be reported as failed even when Worker A succeeded."""
        effect = _make_effect(("unit-a", ["src/a"]), ("unit-b", ["src/b"]))

        # unit-a writes artifact; unit-b writes nothing
        _write_fake_artifact(tmp_path, "unit-a")
        # unit-b artifact dir exists but is empty
        (tmp_path / ".agent" / "workers" / "unit-b" / "artifacts").mkdir(parents=True)

        state = PipelineState(
            phase="development",
            worker_states={
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(
                    unit_id="unit-b",
                    status=WorkerStatus.FAILED,
                    error_message="no artifact evidence",
                ),
            },
        )
        scope = _make_scope(tmp_path)
        _write_parallel_development_summary(
            scope,
            effect,
            state,
            verify_ran=False,
            verify_passed=None,
            verify_exit_code=None,
        )

        summary = _read_summary(tmp_path)
        workers = {w["unit_id"]: w for w in summary["workers"]}
        assert workers["unit-b"]["status"] == "failed"
        assert workers["unit-b"]["artifact_count"] == 0
        assert summary["all_succeeded"] is False

    def test_blocked_dependency_reported_in_summary(self, tmp_path: Path) -> None:
        """unit-b blocked because unit-a failed must appear with status='blocked'."""
        effect = _make_effect(("unit-a", ["src/a"]), ("unit-b", ["src/b"]))

        state = PipelineState(
            phase="development",
            worker_states={
                "unit-a": WorkerState(
                    unit_id="unit-a",
                    status=WorkerStatus.FAILED,
                    error_message="Worker failed",
                ),
                "unit-b": WorkerState(
                    unit_id="unit-b",
                    status=WorkerStatus.FAILED,
                    error_message="Blocked by failed dependencies: unit-a",
                ),
            },
        )
        scope = _make_scope(tmp_path)
        _write_parallel_development_summary(
            scope,
            effect,
            state,
            verify_ran=False,
            verify_passed=None,
            verify_exit_code=None,
        )

        summary = _read_summary(tmp_path)
        workers = {w["unit_id"]: w for w in summary["workers"]}
        assert workers["unit-b"]["status"] == "blocked", (
            "unit-b whose dependency failed must be reported as 'blocked'"
        )
        assert "unit-a" in (workers["unit-b"]["final_message"] or ""), (
            "blocked unit must name the failing dependency in final_message"
        )
        assert summary["any_failed"] is True

    def test_repo_wide_git_status_never_used_for_worker_success(self, tmp_path: Path) -> None:
        """Untracked files in repo must not make failed workers appear as succeeded."""
        effect = _make_effect(("unit-a", ["src/a"]), ("unit-b", ["src/b"]))

        # Create untracked files that could confuse a git-status-based check
        (tmp_path / "src" / "random_change.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "random_change.py").write_text("# untracked\n")

        # unit-b has no artifact; its worker_state is FAILED
        _write_fake_artifact(tmp_path, "unit-a")

        state = PipelineState(
            phase="development",
            worker_states={
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(
                    unit_id="unit-b",
                    status=WorkerStatus.FAILED,
                    error_message="no artifact",
                ),
            },
        )
        scope = _make_scope(tmp_path)
        _write_parallel_development_summary(
            scope,
            effect,
            state,
            verify_ran=False,
            verify_passed=None,
            verify_exit_code=None,
        )

        summary = _read_summary(tmp_path)
        workers = {w["unit_id"]: w for w in summary["workers"]}
        assert workers["unit-b"]["status"] == "failed", (
            "unit-b must remain failed regardless of untracked files in repo"
        )
        assert summary["any_failed"] is True

    def test_all_succeeded_true_when_all_workers_succeed(self, tmp_path: Path) -> None:
        effect = _make_effect(("unit-a", ["src/a"]), ("unit-b", ["src/b"]))
        _write_fake_artifact(tmp_path, "unit-a")
        _write_fake_artifact(tmp_path, "unit-b")

        state = PipelineState(
            phase="development",
            worker_states={
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.SUCCEEDED),
            },
        )
        scope = _make_scope(tmp_path)
        _write_parallel_development_summary(
            scope,
            effect,
            state,
            verify_ran=False,
            verify_passed=None,
            verify_exit_code=None,
        )

        summary = _read_summary(tmp_path)
        assert summary["all_succeeded"] is True
        assert summary["any_failed"] is False

    def test_verification_failure_adds_verify_entry(self, tmp_path: Path) -> None:
        """When verification ran and failed, summary must include __verify__ entry."""
        effect = _make_effect(("unit-a", ["src/a"]))
        _write_fake_artifact(tmp_path, "unit-a")

        state = PipelineState(
            phase="development",
            worker_states={
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
            },
        )
        scope = _make_scope(tmp_path)
        _write_parallel_development_summary(
            scope,
            effect,
            state,
            verify_ran=True,
            verify_passed=False,
            verify_exit_code=_EXIT_CODE_VERIFY_FAIL,
        )

        summary = _read_summary(tmp_path)
        assert summary["any_failed"] is True
        assert summary["all_succeeded"] is False
        workers = {w["unit_id"]: w for w in summary["workers"]}
        assert "__verify__" in workers
        assert workers["__verify__"]["status"] == "failed"
        assert summary["verification"]["ran"] is True
        assert summary["verification"]["passed"] is False
        assert summary["verification"]["exit_code"] == _EXIT_CODE_VERIFY_FAIL


class TestAnalysisHandoffWiring:
    """Verify that the parallel summary is wired into the analysis handoff path."""

    def test_development_result_md_written_after_fanout(self, tmp_path: Path) -> None:
        """After _write_parallel_development_summary runs, .agent/DEVELOPMENT_RESULT.md
        must exist so the analysis phase can read it through the normal fallback path."""
        effect = _make_effect(("unit-a", ["src/a"]), ("unit-b", ["src/b"]))
        _write_fake_artifact(tmp_path, "unit-a")
        _write_fake_artifact(tmp_path, "unit-b")

        state = PipelineState(
            phase="development",
            worker_states={
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.SUCCEEDED),
            },
        )
        scope = _make_scope(tmp_path)
        _write_parallel_development_summary(
            scope,
            effect,
            state,
            verify_ran=False,
            verify_passed=None,
            verify_exit_code=None,
        )

        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists(), (
            ".agent/DEVELOPMENT_RESULT.md must be written for analysis to consume"
        )
        content = handoff_path.read_text()
        assert "Parallel Development Summary" in content
        assert "unit-a" in content
        assert "unit-b" in content

    def test_development_result_md_content_reflects_failure(self, tmp_path: Path) -> None:
        """The DEVELOPMENT_RESULT.md handoff must truthfully reflect partial failure."""
        effect = _make_effect(("unit-a", ["src/a"]), ("unit-b", ["src/b"]))
        _write_fake_artifact(tmp_path, "unit-a")

        state = PipelineState(
            phase="development",
            worker_states={
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(
                    unit_id="unit-b",
                    status=WorkerStatus.FAILED,
                    error_message="no artifact evidence",
                ),
            },
        )
        scope = _make_scope(tmp_path)
        _write_parallel_development_summary(
            scope,
            effect,
            state,
            verify_ran=False,
            verify_passed=None,
            verify_exit_code=None,
        )

        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists()
        content = handoff_path.read_text()
        assert "any_failed: true" in content
        assert "all_succeeded: false" in content

    def test_analysis_handoff_not_clobbered_by_parallel_summary(self, tmp_path: Path) -> None:
        """The normal development_result.json is NOT overwritten by the parallel summary."""
        dev_result_path = tmp_path / ".agent" / "artifacts" / "development_result.json"
        dev_result_path.parent.mkdir(parents=True, exist_ok=True)
        original_content = json.dumps(
            {
                "name": "development_result",
                "type": "development_result",
                "content": {"summary": "original serial development"},
            }
        )
        dev_result_path.write_text(original_content)

        effect = _make_effect(("unit-a", ["src/a"]))
        _write_fake_artifact(tmp_path, "unit-a")

        state = PipelineState(
            phase="development",
            worker_states={
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
            },
        )
        scope = _make_scope(tmp_path)
        _write_parallel_development_summary(
            scope,
            effect,
            state,
            verify_ran=False,
            verify_passed=None,
            verify_exit_code=None,
        )

        # parallel_development_summary.json exists separately
        summary_path = tmp_path / ".agent" / "artifacts" / "parallel_development_summary.json"
        assert summary_path.exists()

        # Original development_result.json must be untouched
        assert dev_result_path.read_text() == original_content
