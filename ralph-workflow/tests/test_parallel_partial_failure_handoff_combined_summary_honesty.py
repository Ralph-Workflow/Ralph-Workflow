"""Tests for honest partial-failure reporting in the parallel development summary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.fan_out import VerificationResult, write_parallel_development_summary
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
    (artifact_dir / "development_result.md").write_text(
        f"---\ntype: development_result\nstatus: complete\n---\n\n# Result\n\nDone by {unit_id}.\n",
        encoding="utf-8",
    )


def _read_summary(tmp_path: Path) -> str:
    summary_path = tmp_path / ".agent" / "artifacts" / "parallel_development_summary.md"
    assert summary_path.exists(), f"Summary not written: {summary_path}"
    return summary_path.read_text(encoding="utf-8")


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
        write_parallel_development_summary(
            scope,
            effect,
            state,
        )

        summary = _read_summary(tmp_path)
        assert "- any_failed: true" in summary
        assert "- all_succeeded: false" in summary
        assert "- **unit-a**: succeeded (1 artifact(s))" in summary
        assert "- **unit-b**: failed (0 artifact(s))" in summary

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
        write_parallel_development_summary(
            scope,
            effect,
            state,
        )

        summary = _read_summary(tmp_path)
        assert "- **unit-b**: failed (0 artifact(s))" in summary
        assert "- all_succeeded: false" in summary

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
        write_parallel_development_summary(
            scope,
            effect,
            state,
        )

        summary = _read_summary(tmp_path)
        assert "- **unit-b**: blocked (0 artifact(s))" in summary, (
            "unit-b whose dependency failed must be reported as 'blocked'"
        )
        assert "Blocked by failed dependencies: unit-a" in summary, (
            "blocked unit must name the failing dependency in final_message"
        )
        assert "- any_failed: true" in summary

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
        write_parallel_development_summary(
            scope,
            effect,
            state,
        )

        summary = _read_summary(tmp_path)
        assert "- **unit-b**: failed (0 artifact(s))" in summary, (
            "unit-b must remain failed regardless of untracked files in repo"
        )
        assert "- any_failed: true" in summary

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
        write_parallel_development_summary(
            scope,
            effect,
            state,
        )

        summary = _read_summary(tmp_path)
        assert "- all_succeeded: true" in summary
        assert "- any_failed: false" in summary

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
        write_parallel_development_summary(
            scope,
            effect,
            state,
            VerificationResult(ran=True, passed=False, exit_code=_EXIT_CODE_VERIFY_FAIL),
        )

        summary = _read_summary(tmp_path)
        assert "- any_failed: true" in summary
        assert "- all_succeeded: false" in summary
        assert "- **__verify__**: failed (0 artifact(s))" in summary
        assert "Ran: yes — failed (exit code 2)" in summary
