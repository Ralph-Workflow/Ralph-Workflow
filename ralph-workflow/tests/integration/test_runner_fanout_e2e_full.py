"""Integration tests: fan-out → post-fan-out verification + handoff.

Proves the complete end-to-end contract:
- Verification runs exactly once after all workers finish (when flag=True)
- Verification is skipped when flag=False
- Verification is skipped on partial failure (saves wasted verification work)
- Partial failure produces an honest handoff with any_failed=true / all_succeeded=false
- DEVELOPMENT_RESULT.md is written after the fan-out run completes

Uses monkeypatched coordinator and verification hook; no real subprocesses.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.enums import PHASE_DEVELOPMENT
from ralph.executor.process import ProcessResult
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.events import PipelineEvent, WorkerCompletedEvent, WorkerFailedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        allowed_directories=[f"src/{uid}"],
    )


def _make_policy_bundle(max_workers: int = 2) -> MagicMock:
    from ralph.policy.models import PhaseParallelization  # noqa: PLC0415

    bundle = MagicMock()
    para = PhaseParallelization(
        max_parallel_workers=max_workers,
        post_fanout_verification=True,
    )
    dev_phase = MagicMock(requires_commit=False, drain="development")
    dev_phase.parallelization = para
    bundle.pipeline.phases = {PHASE_DEVELOPMENT: dev_phase}
    # Raise AttributeError if old parallel_execution path is accessed
    type(bundle.pipeline).parallel_execution = property(  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        lambda self: (_ for _ in ()).throw(  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            AttributeError("parallel_execution removed — use phases[phase].parallelization")
        )
    )
    return bundle


def _seed_artifact(repo_root: Path, unit_id: str) -> None:
    artifact_dir = repo_root / ".agent" / "workers" / unit_id / "artifacts"
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


def _patch_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch out real subprocess/MCP/asyncio-bridge infrastructure."""
    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr(
        "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
        type("_Fake", (), {"__init__": lambda s, *a, **k: None}),
    )
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory",
        type("_Fake", (), {"__init__": lambda s, *a, **k: None}),
    )


class TestFanoutVerificationAndHandoff:
    """Verification ordering and handoff correctness after fan-out."""

    def test_fanout_then_serialized_post_fanout_verification_runs_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verification runs exactly once; DEVELOPMENT_RESULT.md exists after completion."""
        _seed_artifact(tmp_path, "unit-a")
        _seed_artifact(tmp_path, "unit-b")

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        effect = FanOutDevelopmentEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=True,
        )
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit_a, unit_b))
        workspace_scope = WorkspaceScope(tmp_path)
        policy_bundle = _make_policy_bundle(max_workers=2)

        verify_call_count: list[int] = [0]

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return [
                PipelineEvent.FAN_OUT_STARTED,
                WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
                WorkerCompletedEvent(unit_id="unit-b", exit_code=0),
                PipelineEvent.ALL_WORKERS_COMPLETE,
            ]

        async def _fake_run_process_async(
            command: str, args: object = (), **kwargs: object
        ) -> ProcessResult:
            verify_call_count[0] += 1
            return ProcessResult(command=(command,), returncode=0, stdout="OK", stderr="")

        _patch_infra(monkeypatch)
        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr("ralph.executor.process.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

        runner_module._execute_fan_out_sync(
            effect=effect,
            state=state,
            display=runner_module._LegacyConsoleDisplay(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert verify_call_count[0] == 1, (
            f"Verification must run exactly once, got {verify_call_count[0]} calls"
        )
        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists(), (
            "DEVELOPMENT_RESULT.md must be written after fan-out completes"
        )
        content = handoff_path.read_text()
        assert "all_succeeded: true" in content
        assert "unit-a" in content
        assert "unit-b" in content

    def test_post_fanout_verification_skipped_when_flag_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When run_post_fanout_verification=False, verification hook is never called."""
        _seed_artifact(tmp_path, "unit-a")
        _seed_artifact(tmp_path, "unit-b")

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        effect = FanOutDevelopmentEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=False,
        )
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit_a, unit_b))
        workspace_scope = WorkspaceScope(tmp_path)
        policy_bundle = _make_policy_bundle(max_workers=2)

        verify_calls: list[str] = []

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return [
                PipelineEvent.FAN_OUT_STARTED,
                WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
                WorkerCompletedEvent(unit_id="unit-b", exit_code=0),
                PipelineEvent.ALL_WORKERS_COMPLETE,
            ]

        async def _fake_run_process_async(
            command: str, args: object = (), **kwargs: object
        ) -> ProcessResult:
            verify_calls.append(command)
            return ProcessResult(command=(command,), returncode=0, stdout="OK", stderr="")

        _patch_infra(monkeypatch)
        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr("ralph.executor.process.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

        runner_module._execute_fan_out_sync(
            effect=effect,
            state=state,
            display=runner_module._LegacyConsoleDisplay(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert verify_calls == [], (
            f"Verification must not run when flag=False, got calls: {verify_calls}"
        )

    def test_partial_failure_writes_honest_handoff_and_skips_verification(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Partial failure: handoff shows any_failed=true and verification is skipped."""
        _seed_artifact(tmp_path, "unit-a")

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        effect = FanOutDevelopmentEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=True,
        )
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit_a, unit_b))
        workspace_scope = WorkspaceScope(tmp_path)
        policy_bundle = _make_policy_bundle(max_workers=2)

        verify_calls: list[str] = []

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return [
                PipelineEvent.FAN_OUT_STARTED,
                WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
                WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="no artifact evidence"),
                PipelineEvent.ALL_WORKERS_COMPLETE,
            ]

        async def _fake_run_process_async(
            command: str, args: object = (), **kwargs: object
        ) -> ProcessResult:
            verify_calls.append(command)
            return ProcessResult(command=(command,), returncode=0, stdout="OK", stderr="")

        _patch_infra(monkeypatch)
        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr("ralph.executor.process.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

        runner_module._execute_fan_out_sync(
            effect=effect,
            state=state,
            display=runner_module._LegacyConsoleDisplay(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists(), (
            "DEVELOPMENT_RESULT.md must be written even when a worker fails"
        )
        content = handoff_path.read_text()
        assert "any_failed: true" in content
        assert "all_succeeded: false" in content

        assert verify_calls == [], (
            f"Verification must not run on partial failure, got: {verify_calls}"
        )
