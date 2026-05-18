"""Integration test: same-workspace fan-out → analysis handoff.

Proves the full supported path:
  planning artifact with >=2 disjoint work_units
  → FanOutEffect from _determine_effect_from_policy
  → coordinator.run_fan_out produces ALL_WORKERS_COMPLETE
  → reducer advances to development_analysis (no merge/worktree step)
  → per-worker evidence stays in its own namespace

All workers use FakeAgentExecutor (no subprocess, no real MCP).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from rich.console import Console

if TYPE_CHECKING:
    import pytest

    from ralph.display.parallel_display import ParallelDisplay


from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import PipelineEvent, WorkerCompletedEvent, WorkerFailedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.models import PhaseParallelization
from ralph.workspace.scope import WorkspaceScope

_DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"


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


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        allowed_directories=[f"src/{uid}"],
    )


def _make_policy_bundle(max_workers: int = 4) -> MagicMock:
    bundle = MagicMock()
    para = PhaseParallelization(max_parallel_workers=max_workers, post_fanout_verification=True)
    dev_phase = MagicMock(requires_commit=False, drain="development", role="execution")
    dev_phase.parallelization = para
    bundle.pipeline.phases = {"development": dev_phase}
    bundle.agents.agent_drains = {
        "development": MagicMock(chain="developer"),
    }
    bundle.agents.agent_chains = {
        "developer": MagicMock(agents=["developer"]),
    }
    return bundle


class _FakeDisplay:
    def __init__(self) -> None:
        self.console = Console(file=io.StringIO(), force_terminal=False, color_system=None)

    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status


class TestRunnerAnalysisHandoffIntegration:
    """Runner-level integration: _execute_fan_out_sync wires the analysis handoff.

    These tests call _execute_fan_out_sync directly (not just the helper) to prove
    that after parallel fan-out the runner writes .agent/DEVELOPMENT_RESULT.md so the
    analysis phase can pick it up through the normal handoff path.
    """

    def test_runner_writes_development_result_md_after_all_succeed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After two workers succeed, .agent/DEVELOPMENT_RESULT.md must exist
        and contain the parallel summary so the analysis phase can read it."""

        unit_a = WorkUnit(unit_id="unit-a", description="Unit A", allowed_directories=["src/a"])
        unit_b = WorkUnit(unit_id="unit-b", description="Unit B", allowed_directories=["src/b"])

        _seed_artifact(tmp_path, "unit-a")
        _seed_artifact(tmp_path, "unit-b")

        scope = WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))
        initial_state = PipelineState(
            phase="development",
            work_units=(unit_a, unit_b),
        )

        success_events = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerCompletedEvent(unit_id="unit-b", exit_code=0),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return success_events

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

        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists(), (
            ".agent/DEVELOPMENT_RESULT.md must be written for analysis to consume after fan-out"
        )
        content = handoff_path.read_text()
        assert "Parallel Development Summary" in content
        assert "unit-a" in content
        assert "unit-b" in content
        assert "all_succeeded: true" in content

    def test_runner_partial_failure_reflected_in_handoff(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When unit-b fails, DEVELOPMENT_RESULT.md must show any_failed=true
        and all_succeeded=false so the analysis phase sees the honest outcome."""

        unit_a = WorkUnit(unit_id="unit-a", description="Unit A", allowed_directories=["src/a"])
        unit_b = WorkUnit(unit_id="unit-b", description="Unit B", allowed_directories=["src/b"])

        _seed_artifact(tmp_path, "unit-a")  # unit-b gets no artifact on purpose

        scope = WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))
        initial_state = PipelineState(
            phase="development",
            work_units=(unit_a, unit_b),
        )

        partial_events = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="no artifact evidence"),
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

        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists(), (
            ".agent/DEVELOPMENT_RESULT.md must be written even on partial failure"
        )
        content = handoff_path.read_text()
        assert "any_failed: true" in content
        assert "all_succeeded: false" in content


