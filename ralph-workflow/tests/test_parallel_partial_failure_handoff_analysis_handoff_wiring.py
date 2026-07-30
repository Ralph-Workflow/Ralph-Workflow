"""Tests for honest partial-failure reporting in the parallel development summary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.fan_out import write_parallel_development_summary
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
        write_parallel_development_summary(
            scope,
            effect,
            state,
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
        write_parallel_development_summary(
            scope,
            effect,
            state,
        )

        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists()
        content = handoff_path.read_text()
        assert "any_failed: true" in content
        assert "all_succeeded: false" in content

    def test_analysis_handoff_not_clobbered_by_parallel_summary(self, tmp_path: Path) -> None:
        """The normal development-result Markdown is not overwritten by the parallel summary."""
        dev_result_path = tmp_path / ".agent" / "artifacts" / "development_result.md"
        dev_result_path.parent.mkdir(parents=True, exist_ok=True)
        original_content = (
            "---\ntype: development_result\nstatus: complete\n---\n\n"
            "# Result\n\nOriginal serial development.\n"
        )
        dev_result_path.write_text(original_content, encoding="utf-8")

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
        )

        summary_path = tmp_path / ".agent" / "artifacts" / "parallel_development_summary.md"
        assert summary_path.exists()

        assert dev_result_path.read_text(encoding="utf-8") == original_content
