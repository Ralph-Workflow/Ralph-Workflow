from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import fan_out as _fan_out_module
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.fan_out import execute_fan_out_sync
from ralph.pipeline.parallel.coordinator import WorkerContext
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


RESUMED_WORKER_COUNT = 3


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        allowed_directories=[f"src/{uid}"],
    )


def _make_worker_state(uid: str, status: WorkerStatus) -> WorkerState:
    return WorkerState(unit_id=uid, status=status)


def _fake_executor_for(unit_ids: list[str]) -> FakeAgentExecutor:
    runs = {uid: FakeRun(outputs=["done"], exit_code=0, duration_ms=10) for uid in unit_ids}
    return FakeAgentExecutor(runs)


def _seed_worker_artifact(worker_namespace_root: Path, unit_id: str) -> None:
    artifact_dir = worker_namespace_root / unit_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "plan.json").write_text(
        json.dumps(
            {
                "name": "plan",
                "type": "plan",
                "content": {"summary": "done"},
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "metadata": {},
            }
        )
    )


def _setup_patches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_executor: FakeAgentExecutor,
    artifact_unit_ids: set[str],
) -> None:
    workers_root = tmp_path / ".agent" / "workers"
    for uid in artifact_unit_ids:
        _seed_worker_artifact(workers_root, uid)
    monkeypatch.setattr(
        _fan_out_module,
        "_build_session_mcp_plan_for_phase",
        lambda **kwargs: (MagicMock(), "development"),
    )

    def _fake_worker_context(**kwargs: object) -> tuple[object, object]:
        return fake_executor, WorkerContext(same_workspace=None)

    monkeypatch.setattr(_fan_out_module, "_fan_out_worker_context", _fake_worker_context)
    monkeypatch.setattr(ckpt, "save", lambda state: None)


def _make_mock_policy_bundle() -> MagicMock:
    bundle = MagicMock()
    bundle.pipeline.recovery.failed_route = "failed_terminal"
    return bundle


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        pass

    def set_status(self, unit_id: str, status: object) -> None:
        pass

    def __enter__(self) -> _FakeDisplay:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class TestParallelResume:

    def test_resume_skips_succeeded_workers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        units = tuple(_make_work_unit(f"unit-{i}") for i in range(5))
        worker_states = {
            "unit-0": _make_worker_state("unit-0", WorkerStatus.SUCCEEDED),
            "unit-1": _make_worker_state("unit-1", WorkerStatus.SUCCEEDED),
            "unit-2": _make_worker_state("unit-2", WorkerStatus.PENDING),
            "unit-3": _make_worker_state("unit-3", WorkerStatus.PENDING),
            "unit-4": _make_worker_state("unit-4", WorkerStatus.PENDING),
        }
        state = PipelineState(
            phase="development",
            work_units=units,
            worker_states=worker_states,
        )
        effect = FanOutEffect(work_units=units, max_workers=5)
        fake_executor = _fake_executor_for(["unit-2", "unit-3", "unit-4"])
        scope = MagicMock()
        scope.root = tmp_path

        _setup_patches(
            monkeypatch,
            tmp_path,
            fake_executor,
            artifact_unit_ids={"unit-2", "unit-3", "unit-4"},
        )

        execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_FakeDisplay(),
            policy_bundle=_make_mock_policy_bundle(),
            workspace_scope=scope,
        )

        launched_ids = {u.unit_id for u in fake_executor.calls}
        assert launched_ids == {"unit-2", "unit-3", "unit-4"}
        assert "unit-0" not in launched_ids
        assert "unit-1" not in launched_ids

        runs_for = Counter(u.unit_id for u in fake_executor.calls)
        assert runs_for["unit-2"] == 1, "Each resumed unit must be invoked exactly once"
        assert runs_for["unit-3"] == 1, "Each resumed unit must be invoked exactly once"
        assert runs_for["unit-4"] == 1, "Each resumed unit must be invoked exactly once"

    def test_resume_resets_running_to_pending(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        units = tuple(_make_work_unit(f"unit-{i}") for i in range(3))
        worker_states = {
            "unit-0": _make_worker_state("unit-0", WorkerStatus.RUNNING),
            "unit-1": _make_worker_state("unit-1", WorkerStatus.PENDING),
            "unit-2": _make_worker_state("unit-2", WorkerStatus.PENDING),
        }
        state = PipelineState(
            phase="development",
            work_units=units,
            worker_states=worker_states,
        )
        effect = FanOutEffect(work_units=units, max_workers=3)
        fake_executor = _fake_executor_for(["unit-0", "unit-1", "unit-2"])
        scope = MagicMock()
        scope.root = tmp_path

        _setup_patches(
            monkeypatch,
            tmp_path,
            fake_executor,
            artifact_unit_ids={"unit-0", "unit-1", "unit-2"},
        )

        execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_FakeDisplay(),
            policy_bundle=_make_mock_policy_bundle(),
            workspace_scope=scope,
        )

        launched_ids = {u.unit_id for u in fake_executor.calls}
        assert "unit-0" in launched_ids, "RUNNING unit must be re-launched after reset to PENDING"
        assert len(launched_ids) == RESUMED_WORKER_COUNT

    def test_resume_completes_all_units(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        units = tuple(_make_work_unit(f"unit-{i}") for i in range(5))
        worker_states = {
            "unit-0": _make_worker_state("unit-0", WorkerStatus.SUCCEEDED),
            "unit-1": _make_worker_state("unit-1", WorkerStatus.SUCCEEDED),
            "unit-2": _make_worker_state("unit-2", WorkerStatus.RUNNING),
            "unit-3": _make_worker_state("unit-3", WorkerStatus.PENDING),
            "unit-4": _make_worker_state("unit-4", WorkerStatus.PENDING),
        }
        state = PipelineState(
            phase="development",
            work_units=units,
            worker_states=worker_states,
        )
        effect = FanOutEffect(work_units=units, max_workers=5)
        fake_executor = _fake_executor_for(["unit-2", "unit-3", "unit-4"])
        scope = MagicMock()
        scope.root = tmp_path

        _setup_patches(
            monkeypatch,
            tmp_path,
            fake_executor,
            artifact_unit_ids={"unit-2", "unit-3", "unit-4"},
        )

        final_state = execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_FakeDisplay(),
            policy_bundle=_make_mock_policy_bundle(),
            workspace_scope=scope,
        )

        assert len(fake_executor.calls) == RESUMED_WORKER_COUNT
        launched_ids = {u.unit_id for u in fake_executor.calls}
        assert launched_ids == {"unit-2", "unit-3", "unit-4"}

        for i in range(5):
            uid = f"unit-{i}"
            ws = final_state.worker_states.get(uid)
            assert ws is not None, f"{uid} missing from final worker_states"
            assert ws.status == WorkerStatus.SUCCEEDED, f"{uid} expected SUCCEEDED, got {ws.status}"



