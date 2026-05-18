"""Integration tests for partial-failure reporting in parallel fan-out.

Verifies that when some workers succeed before a dependent worker fails,
per-unit status is reported correctly for all workers.
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ralph.display.parallel_display import ParallelDisplay

from rich.console import Console

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import (
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
)
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.workspace.scope import WorkspaceScope


def _seed_artifact(tmp_path: Path, unit_id: str) -> None:
    artifact_dir = tmp_path / ".agent" / "workers" / unit_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "development_result.json").write_text(
        json.dumps({
            "name": "development_result",
            "type": "development_result",
            "content": {"summary": f"Worker {unit_id} done", "changes": []},
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "metadata": {},
        })
    )


def _make_policy_bundle(max_workers: int = 2) -> MagicMock:
    bundle = MagicMock()
    dev_phase = MagicMock()
    dev_phase.drain = "development"
    bundle.pipeline.phases = {"development": dev_phase}
    bundle.pipeline.recovery.failed_route = "failed_terminal"
    return bundle


def _make_work_unit(uid: str, deps: list[str] | None = None) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        dependencies=list(deps or []),
        allowed_directories=[f"src/{uid}"],
    )


class _FakeDisplay:
    def __init__(self) -> None:
        self.console = Console(file=io.StringIO(), force_terminal=False, color_system=None)

    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status


class TestPartialFailureHandoffContent:
    """Runner-level tests: DEVELOPMENT_RESULT.md handoff content on partial failure.

    These tests verify that when 1 of 2 workers fails, the resulting handoff
    artifact contains both unit_ids, marks the right worker as failed, and
    reports phase state as "failed" (not "development_analysis").
    """

    def test_partial_failure_development_result_md_contains_both_unit_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When unit-b fails, DEVELOPMENT_RESULT.md must name BOTH unit-a and unit-b.

        Asserts:
        - .agent/DEVELOPMENT_RESULT.md exists and contains both unit_ids
        - unit-b (the failed one) is named in the failure context
        - unit-a (the success) is present and not blamed
        - any_failed: true and all_succeeded: false
        """

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")

        _seed_artifact(tmp_path, "unit-a")  # unit-b deliberately has no artifact

        scope = WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))
        initial_state = PipelineState(
            phase="development",
            work_units=(unit_a, unit_b),
        )

        partial_events = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="unit-b: no artifact"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return partial_events

        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr(ckpt, "save", lambda state: None)

        bundle = _make_policy_bundle(max_workers=2)
        effect = FanOutEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=False,
        )

        final_state = runner_module.execute_fan_out_sync(
            effect=effect,
            state=initial_state,
            display=cast("ParallelDisplay", _FakeDisplay()),
            policy_bundle=bundle,
            workspace_scope=scope,
        )

        # State must be terminal failure — never "development_analysis" on partial failure
        assert final_state.phase == "failed_terminal", (
            f"Partial failure must produce 'failed_terminal', got {final_state.phase!r}. "
            "'development_analysis' must only be reached when ALL workers succeed."
        )

        # DEVELOPMENT_RESULT.md must contain both unit_ids
        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists(), (
            ".agent/DEVELOPMENT_RESULT.md must be written on partial failure"
        )
        content = handoff_path.read_text()

        assert "unit-a" in content, "DEVELOPMENT_RESULT.md must name unit-a (the successful worker)"
        assert "unit-b" in content, "DEVELOPMENT_RESULT.md must name unit-b (the failed worker)"
        assert "any_failed: true" in content, "DEVELOPMENT_RESULT.md must report any_failed: true"
        assert "all_succeeded: false" in content, (
            "DEVELOPMENT_RESULT.md must report all_succeeded: false"
        )

    def test_parallel_development_summary_json_has_per_unit_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """parallel_development_summary.json lists per-unit status on partial failure.

        Asserts:
        - .agent/artifacts/parallel_development_summary.json exists
        - any_failed = true
        - all_succeeded = false
        - workers list contains entries for both unit-a and unit-b
        - unit-a entry has status 'succeeded'
        - unit-b entry has a non-success status
        """

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")

        _seed_artifact(tmp_path, "unit-a")

        scope = WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))
        initial_state = PipelineState(
            phase="development",
            work_units=(unit_a, unit_b),
        )

        partial_events = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="unit-b failed"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return partial_events

        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr(ckpt, "save", lambda state: None)

        bundle = _make_policy_bundle(max_workers=2)
        effect = FanOutEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=False,
        )

        runner_module.execute_fan_out_sync(
            effect=effect,
            state=initial_state,
            display=cast("ParallelDisplay", _FakeDisplay()),
            policy_bundle=bundle,
            workspace_scope=scope,
        )

        summary_path = tmp_path / ".agent" / "artifacts" / "parallel_development_summary.json"
        assert summary_path.exists(), (
            ".agent/artifacts/parallel_development_summary.json must be written after fan-out"
        )
        summary = json.loads(summary_path.read_text())

        assert summary["any_failed"] is True, (
            f"parallel_development_summary.json must have any_failed=true, got: {summary!r}"
        )
        assert summary["all_succeeded"] is False, (
            f"parallel_development_summary.json must have all_succeeded=false, got: {summary!r}"
        )

        workers_by_id = {w["unit_id"]: w for w in summary["workers"]}
        assert "unit-a" in workers_by_id, "unit-a must appear in workers list"
        assert "unit-b" in workers_by_id, "unit-b must appear in workers list"

        assert workers_by_id["unit-a"]["status"] == "succeeded", (
            f"unit-a must be succeeded, got: {workers_by_id['unit-a']!r}"
        )
        assert workers_by_id["unit-b"]["status"] != "succeeded", (
            f"unit-b must not be succeeded, got: {workers_by_id['unit-b']!r}"
        )



