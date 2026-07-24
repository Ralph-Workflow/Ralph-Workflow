"""Integration tests for serialized post-fanout verification.

Verifies that when run_post_fanout_verification=True:
- Workspace-wide verification runs exactly once, after all workers finish.
- Verification is skipped when any worker fails.
- Concurrent invocation is impossible by construction (serialized after coordinator returns).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.display.context import make_display_context
from ralph.executor.process import ProcessResult
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import WorkerCompletedEvent, WorkerFailedEvent
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.models import PhaseParallelization
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_MAX_AGENT_RETRIES = 3


def _legacy_display() -> runner_module.ParallelDisplay:
    return runner_module.ParallelDisplay(make_display_context())


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=[f"src/{unit_id}"],
    )


def _make_policy_bundle(max_workers: int = 2) -> MagicMock:
    bundle = MagicMock()
    para = PhaseParallelization(max_parallel_workers=max_workers, post_fanout_verification=True)
    dev_phase = MagicMock(requires_commit=False, drain="development", role="execution")
    dev_phase.parallelization = para
    bundle.pipeline.phases = {"development": dev_phase}
    bundle.pipeline.recovery.failed_route = "failed_terminal"
    bundle.agents.agent_drains = {
        "development": MagicMock(
            chain="developer", drain_class="development", capability_class=None
        ),
    }
    return bundle


class TestSerializedPostFanoutVerification:
    def test_verification_runs_once_after_workers_succeed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Post-fanout verification runs exactly once after all workers finish."""
        unit = _make_work_unit("unit-a")
        effect = FanOutEffect(
            work_units=(unit,),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        state = PipelineState(phase="development", work_units=(unit,))
        policy_bundle = _make_policy_bundle(max_workers=1)
        workspace_scope = WorkspaceScope(tmp_path)

        call_order: list[str] = []

        class _FakeExecutor:
            def __init__(self, command: object, signal_bridge: object | None = None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace: object) -> None:
                del workspace

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            del kwargs
            call_order.append("fan_out")
            return []

        async def _fake_run_process_async(
            command: str,
            args: tuple[object, ...] = (),
            **kwargs: object,
        ) -> ProcessResult:
            del args, kwargs
            call_order.append(f"verify:{command}")
            return ProcessResult(
                command=(command,),
                returncode=0,
                stdout="All checks passed!",
                stderr="",
            )

        monkeypatch.setattr(
            "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
        )
        monkeypatch.setattr(
            "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
            _FakeExecutor,
        )
        monkeypatch.setattr(
            "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
        )
        monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr("ralph.pipeline.runner.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

        runner_module.execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_legacy_display(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert "fan_out" in call_order
        verify_calls = [c for c in call_order if c.startswith("verify:")]
        assert len(verify_calls) == 1, f"Expected exactly 1 verification call, got: {call_order}"

    def test_verification_runs_after_fan_out_not_before(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Verification must run after coordinator.run_fan_out returns, never before."""
        unit = _make_work_unit("unit-a")
        effect = FanOutEffect(
            work_units=(unit,),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        state = PipelineState(phase="development", work_units=(unit,))
        policy_bundle = _make_policy_bundle(max_workers=1)
        workspace_scope = WorkspaceScope(tmp_path)

        call_order: list[str] = []

        class _FakeExecutor:
            def __init__(self, command: object, signal_bridge: object | None = None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace: object) -> None:
                del workspace

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            del kwargs
            call_order.append("fan_out")
            return []

        async def _fake_run_process_async(
            command: str,
            args: tuple[object, ...] = (),
            **kwargs: object,
        ) -> ProcessResult:
            del args, kwargs
            call_order.append("verify")
            return ProcessResult(command=(command,), returncode=0, stdout="", stderr="")

        monkeypatch.setattr(
            "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
        )
        monkeypatch.setattr(
            "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
            _FakeExecutor,
        )
        monkeypatch.setattr(
            "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
        )
        monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr("ralph.pipeline.runner.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

        runner_module.execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_legacy_display(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert call_order == ["fan_out", "verify"], (
            f"fan_out must complete before verify with no extra work, got: {call_order}"
        )

    def test_verification_skipped_when_run_post_fanout_verification_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When run_post_fanout_verification=False, no verification command is called."""
        unit = _make_work_unit("unit-a")
        effect = FanOutEffect(
            work_units=(unit,),
            max_workers=1,
            run_post_fanout_verification=False,
        )
        state = PipelineState(phase="development", work_units=(unit,))
        policy_bundle = _make_policy_bundle(max_workers=1)
        workspace_scope = WorkspaceScope(tmp_path)
        verify_calls: list[str] = []

        class _FakeExecutor:
            def __init__(self, command: object, signal_bridge: object | None = None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace: object) -> None:
                del workspace

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            del kwargs
            return []

        async def _fake_run_process_async(
            command: str,
            args: tuple[object, ...] = (),
            **kwargs: object,
        ) -> ProcessResult:
            del args, kwargs
            verify_calls.append(command)
            return ProcessResult(command=(command,), returncode=0, stdout="", stderr="")

        monkeypatch.setattr(
            "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
        )
        monkeypatch.setattr(
            "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
            _FakeExecutor,
        )
        monkeypatch.setattr(
            "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
        )
        monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr("ralph.pipeline.runner.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

        runner_module.execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_legacy_display(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert verify_calls == [], (
            f"Verification should not run when flag is False, got: {verify_calls}"
        )

    def test_determine_effect_enables_post_fanout_verification(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_determine_effect_from_policy sets run_post_fanout_verification=True for >=2 units."""
        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        state = PipelineState(phase="development", work_units=(unit_a, unit_b))
        policy_bundle = _make_policy_bundle(max_workers=2)

        effect = runner_module.determine_effect_from_policy(state, policy_bundle)

        assert isinstance(effect, FanOutEffect)
        assert effect.run_post_fanout_verification is True, (
            "_determine_effect_from_policy must enable run_post_fanout_verification "
            "for same-workspace parallel execution"
        )

    def test_verification_skipped_when_any_worker_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Post-fanout verification must not run when a worker has failed."""

        unit = _make_work_unit("unit-a")
        effect = FanOutEffect(
            work_units=(unit,),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        state = PipelineState(phase="development", work_units=(unit,))
        policy_bundle = _make_policy_bundle(max_workers=1)
        workspace_scope = WorkspaceScope(tmp_path)
        verify_calls: list[str] = []

        class _FakeExecutor:
            def __init__(self, command: object, signal_bridge: object | None = None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace: object) -> None:
                del workspace

        async def _fake_run_fan_out_with_failure(**kwargs: object) -> list[WorkerFailedEvent]:
            del kwargs
            return [WorkerFailedEvent(unit_id="unit-a", exit_code=1, error="worker crashed")]

        async def _fake_run_process_async(
            command: str,
            args: tuple[object, ...] = (),
            **kwargs: object,
        ) -> ProcessResult:
            del args, kwargs
            verify_calls.append(command)
            return ProcessResult(command=(command,), returncode=0, stdout="", stderr="")

        monkeypatch.setattr(
            "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
        )
        monkeypatch.setattr(
            "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
            _FakeExecutor,
        )
        monkeypatch.setattr(
            "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
        )
        monkeypatch.setattr(
            "ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out_with_failure
        )
        monkeypatch.setattr("ralph.pipeline.runner.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

        runner_module.execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_legacy_display(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert verify_calls == [], (
            f"Verification must not run when a worker failed, got: {verify_calls}"
        )

    def test_post_fanout_verification_failure_routes_to_recovery(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When verification fails, the returned state must be "failed"
        with last_error containing the verification failure message."""
        unit = _make_work_unit("unit-a")
        effect = FanOutEffect(
            work_units=(unit,),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        # Exhaust the dev_chain retries so the reducer transitions to "failed"
        # rather than scheduling another retry attempt.
        exhausted_chain = AgentChainState(agents=[], current_index=0, retries=_MAX_AGENT_RETRIES)
        state = PipelineState(
            phase="development",
            work_units=(unit,),
            phase_chains={"development": exhausted_chain},
        )
        policy_bundle = _make_policy_bundle(max_workers=1)
        workspace_scope = WorkspaceScope(tmp_path)

        verify_failure_output = "type check failed: 3 errors found"

        class _FakeExecutor:
            def __init__(self, command: object, signal_bridge: object | None = None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace: object) -> None:
                del workspace

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            del kwargs
            return []

        async def _fake_run_process_async(
            command: str,
            args: tuple[object, ...] = (),
            **kwargs: object,
        ) -> ProcessResult:
            del args, kwargs
            return ProcessResult(
                command=(command,),
                returncode=1,
                stdout=verify_failure_output,
                stderr="",
            )

        monkeypatch.setattr(
            "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
        )
        monkeypatch.setattr(
            "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
            _FakeExecutor,
        )
        monkeypatch.setattr(
            "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
        )
        monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr("ralph.pipeline.runner.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

        final_state = runner_module.execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_legacy_display(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert final_state.phase == "failed_terminal", (
            f"Expected 'failed_terminal' after verification failure, got: {final_state.phase}"
        )
        assert final_state.last_error is not None, (
            "last_error must be set after verification failure"
        )
        assert verify_failure_output in (final_state.last_error or ""), (
            f"last_error must contain the verification output, got: {final_state.last_error!r}"
        )

    def test_verification_runs_after_all_workers_finish_never_concurrently(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Verification starts only after the last worker finishes; never overlaps workers.

        Captures the sequence of events: fan_out_started, fan_out_ended, verify_started,
        verify_ended. Asserts that verify_started >= fan_out_ended (no overlap).
        By construction, _execute_fan_out_sync is synchronous — it awaits the coordinator
        before running verification — so overlap is architecturally impossible. This test
        proves that guarantee holds through the call chain.
        """

        units = tuple(
            WorkUnit(
                unit_id=f"unit-{number}",
                description=f"Work unit {number}",
                allowed_directories=[f"src/unit-{number}"],
                step_ids=[f"S-{number}"],
            )
            for number in range(1, 6)
        )
        effect = FanOutEffect(
            work_units=units,
            max_workers=5,
            run_post_fanout_verification=True,
        )
        state = PipelineState(phase="development", work_units=units)
        policy_bundle = _make_policy_bundle(max_workers=5)
        workspace_scope = WorkspaceScope(tmp_path)

        timestamps: dict[str, float] = {}
        call_order: list[str] = []

        class _FakeExecutor:
            def __init__(self, command: object, signal_bridge: object | None = None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace: object) -> None:
                del workspace

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            del kwargs
            timestamps["fan_out_started"] = time.monotonic()
            call_order.append("fan_out")
            for unit in units:
                call_order.append(f"worker:{unit.unit_id}")
            timestamps["fan_out_ended"] = time.monotonic()
            return [
                WorkerCompletedEvent(unit_id=unit.unit_id, exit_code=0) for unit in units
            ]

        async def _fake_run_process_async(
            command: str,
            args: tuple[object, ...] = (),
            **kwargs: object,
        ) -> ProcessResult:
            del args, kwargs
            timestamps["verify_started"] = time.monotonic()
            call_order.append("verify")
            timestamps["verify_ended"] = time.monotonic()
            return ProcessResult(command=(command,), returncode=0, stdout="OK", stderr="")

        monkeypatch.setattr(
            "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
        )
        monkeypatch.setattr(
            "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
            _FakeExecutor,
        )
        monkeypatch.setattr(
            "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
        )
        monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr("ralph.pipeline.runner.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

        runner_module.execute_fan_out_sync(
            effect=effect,
            state=state,
            display=_legacy_display(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert "fan_out" in call_order, "fan_out must have been called"
        assert "verify" in call_order, "verify must have been called"
        assert call_order.index("fan_out") < call_order.index("verify"), (
            f"fan_out must precede verify, got: {call_order}"
        )
        assert call_order[-1] == "verify"
        assert [entry for entry in call_order if entry.startswith("worker:")] == [
            f"worker:{unit.unit_id}" for unit in units
        ]
        # Verify started at or after fan_out ended (no overlap)
        assert timestamps["verify_started"] >= timestamps["fan_out_ended"], (
            f"verify must not start before fan_out ends: "
            f"fan_out_ended={timestamps['fan_out_ended']}, "
            f"verify_started={timestamps['verify_started']}"
        )
        # Verify ran exactly once
        verify_calls = [c for c in call_order if c == "verify"]
        assert len(verify_calls) == 1, (
            f"verify must run exactly once, got {len(verify_calls)} calls: {call_order}"
        )
