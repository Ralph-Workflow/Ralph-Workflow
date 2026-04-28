from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from ralph.config.enums import PHASE_DEVELOPMENT
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.runner import _execute_fan_out_sync
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

if TYPE_CHECKING:
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


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        pass

    def set_status(self, unit_id: str, status: object) -> None:
        pass

    def __enter__(self) -> _FakeDisplay:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def _make_mock_policy_bundle() -> MagicMock:
    bundle = MagicMock()
    bundle.pipeline.phases = {
        PHASE_DEVELOPMENT: MagicMock(requires_commit=False, drain="development"),
    }
    bundle.pipeline.parallel_execution.max_parallel_workers = 8
    bundle.agents.agent_drains = {}
    bundle.agents.agent_chains = {}
    return bundle


def _setup_patches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    fake_executor: FakeAgentExecutor,
    *,
    artifact_unit_ids: set[str] | None = None,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
        lambda *args, **kwargs: fake_executor,
    )
    monkeypatch.setattr(
        "ralph.display.parallel_display.ParallelDisplay",
        _FakeDisplay,
    )
    monkeypatch.setattr(
        "ralph.pipeline.checkpoint.save",
        lambda _state: None,
    )
    monkeypatch.setattr(
        "ralph.git.executor.GitExecutor",
        MagicMock,
    )
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory",
        lambda *args, **kwargs: MagicMock(),
    )

    # Pre-seed worker-local artifacts for units that should succeed.
    # The coordinator checks for artifacts in .agent/workers/<unit_id>/artifacts/
    # after each worker run. Pre-seeding ensures the check passes.
    worker_namespace_root = Path(tmp_path) / ".agent" / "workers"
    if artifact_unit_ids is not None:
        for uid in artifact_unit_ids:
            _seed_worker_artifact(worker_namespace_root, uid)


class TestParallelResume:
    def test_resume_skips_succeeded_workers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
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
            phase=PHASE_DEVELOPMENT,
            work_units=units,
            worker_states=worker_states,
        )
        effect = FanOutDevelopmentEffect(work_units=units, max_workers=5)
        fake_executor = _fake_executor_for(["unit-2", "unit-3", "unit-4"])
        scope = MagicMock()
        scope.root = tmp_path

        _setup_patches(
            monkeypatch,
            tmp_path,
            fake_executor,
            artifact_unit_ids={"unit-2", "unit-3", "unit-4"},
        )

        _execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
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
        tmp_path: Any,
    ) -> None:
        units = tuple(_make_work_unit(f"unit-{i}") for i in range(3))
        worker_states = {
            "unit-0": _make_worker_state("unit-0", WorkerStatus.RUNNING),
            "unit-1": _make_worker_state("unit-1", WorkerStatus.PENDING),
            "unit-2": _make_worker_state("unit-2", WorkerStatus.PENDING),
        }
        state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            work_units=units,
            worker_states=worker_states,
        )
        effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)
        fake_executor = _fake_executor_for(["unit-0", "unit-1", "unit-2"])
        scope = MagicMock()
        scope.root = tmp_path

        _setup_patches(
            monkeypatch,
            tmp_path,
            fake_executor,
            artifact_unit_ids={"unit-0", "unit-1", "unit-2"},
        )

        _execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            policy_bundle=_make_mock_policy_bundle(),
            workspace_scope=scope,
        )

        launched_ids = {u.unit_id for u in fake_executor.calls}
        assert "unit-0" in launched_ids, "RUNNING unit must be re-launched after reset to PENDING"
        assert len(launched_ids) == RESUMED_WORKER_COUNT

    def test_resume_completes_all_units(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
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
            phase=PHASE_DEVELOPMENT,
            work_units=units,
            worker_states=worker_states,
        )
        effect = FanOutDevelopmentEffect(work_units=units, max_workers=5)
        fake_executor = _fake_executor_for(["unit-2", "unit-3", "unit-4"])
        scope = MagicMock()
        scope.root = tmp_path

        _setup_patches(
            monkeypatch,
            tmp_path,
            fake_executor,
            artifact_unit_ids={"unit-2", "unit-3", "unit-4"},
        )

        final_state = _execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
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
