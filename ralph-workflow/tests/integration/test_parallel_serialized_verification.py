"""Integration tests for serialized post-fanout verification.

Verifies that when run_post_fanout_verification=True:
- Workspace-wide verification runs exactly once, after all workers finish.
- Verification is skipped when any worker fails.
- Concurrent invocation is impossible by construction (serialized after coordinator returns).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_FAILED
from ralph.executor.process import ProcessResult
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.workspace.scope import WorkspaceScope

_MAX_AGENT_RETRIES = 3


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=[f"src/{unit_id}"],
    )


def _make_policy_bundle(max_workers: int = 2) -> MagicMock:
    bundle = MagicMock()
    bundle.pipeline.phases = {
        PHASE_DEVELOPMENT: MagicMock(requires_commit=False, drain="development"),
    }
    bundle.pipeline.parallel_execution.phase = PHASE_DEVELOPMENT
    bundle.pipeline.parallel_execution.max_parallel_workers = max_workers
    bundle.pipeline.parallel_execution.post_fanout_verification = True
    bundle.pipeline.recovery.terminal_recovery_route = PHASE_FAILED
    return bundle


class TestSerializedPostFanoutVerification:
    def test_verification_runs_once_after_workers_succeed(self, monkeypatch, tmp_path) -> None:
        """Post-fanout verification runs exactly once after all workers finish."""
        unit = _make_work_unit("unit-a")
        effect = FanOutDevelopmentEffect(
            work_units=(unit,),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit,))
        policy_bundle = _make_policy_bundle(max_workers=1)
        workspace_scope = WorkspaceScope(tmp_path)

        call_order: list[str] = []

        class _FakeExecutor:
            def __init__(self, command, signal_bridge=None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace) -> None:
                del workspace

        async def _fake_run_fan_out(**kwargs):
            call_order.append("fan_out")
            return []

        async def _fake_run_process_async(command, args=(), **kwargs):
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
        monkeypatch.setattr("ralph.executor.process.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

        runner_module._execute_fan_out_sync(
            effect=effect,
            state=state,
            display=runner_module._LegacyConsoleDisplay(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert "fan_out" in call_order
        verify_calls = [c for c in call_order if c.startswith("verify:")]
        assert len(verify_calls) == 1, f"Expected exactly 1 verification call, got: {call_order}"

    def test_verification_runs_after_fan_out_not_before(self, monkeypatch, tmp_path) -> None:
        """Verification must run after coordinator.run_fan_out returns, never before."""
        unit = _make_work_unit("unit-a")
        effect = FanOutDevelopmentEffect(
            work_units=(unit,),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit,))
        policy_bundle = _make_policy_bundle(max_workers=1)
        workspace_scope = WorkspaceScope(tmp_path)

        call_order: list[str] = []

        class _FakeExecutor:
            def __init__(self, command, signal_bridge=None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace) -> None:
                del workspace

        async def _fake_run_fan_out(**kwargs):
            call_order.append("fan_out")
            return []

        async def _fake_run_process_async(command, args=(), **kwargs):
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
        monkeypatch.setattr("ralph.executor.process.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

        runner_module._execute_fan_out_sync(
            effect=effect,
            state=state,
            display=runner_module._LegacyConsoleDisplay(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert call_order.index("fan_out") < call_order.index("verify"), (
            f"fan_out must complete before verify, but got order: {call_order}"
        )

    def test_verification_skipped_when_run_post_fanout_verification_false(
        self, monkeypatch, tmp_path
    ) -> None:
        """When run_post_fanout_verification=False, no verification command is called."""
        unit = _make_work_unit("unit-a")
        effect = FanOutDevelopmentEffect(
            work_units=(unit,),
            max_workers=1,
            run_post_fanout_verification=False,
        )
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit,))
        policy_bundle = _make_policy_bundle(max_workers=1)
        workspace_scope = WorkspaceScope(tmp_path)
        verify_calls: list[str] = []

        class _FakeExecutor:
            def __init__(self, command, signal_bridge=None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace) -> None:
                del workspace

        async def _fake_run_fan_out(**kwargs):
            return []

        async def _fake_run_process_async(command, args=(), **kwargs):
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
            f"Verification should not run when flag is False, got: {verify_calls}"
        )

    def test_determine_effect_enables_post_fanout_verification(self, monkeypatch) -> None:
        """_determine_effect_from_policy sets run_post_fanout_verification=True for >=2 units."""
        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit_a, unit_b))
        policy_bundle = _make_policy_bundle(max_workers=2)

        effect = runner_module._determine_effect_from_policy(state, policy_bundle)

        assert isinstance(effect, FanOutDevelopmentEffect)
        assert effect.run_post_fanout_verification is True, (
            "_determine_effect_from_policy must enable run_post_fanout_verification "
            "for same-workspace parallel execution"
        )

    def test_verification_skipped_when_any_worker_fails(self, monkeypatch, tmp_path) -> None:
        """Post-fanout verification must not run when a worker has failed."""
        from ralph.pipeline.events import WorkerFailedEvent  # noqa: PLC0415

        unit = _make_work_unit("unit-a")
        effect = FanOutDevelopmentEffect(
            work_units=(unit,),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit,))
        policy_bundle = _make_policy_bundle(max_workers=1)
        workspace_scope = WorkspaceScope(tmp_path)
        verify_calls: list[str] = []

        class _FakeExecutor:
            def __init__(self, command, signal_bridge=None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace) -> None:
                del workspace

        async def _fake_run_fan_out_with_failure(**kwargs):
            # Return a WorkerFailedEvent to simulate a failed worker
            return [WorkerFailedEvent(unit_id="unit-a", exit_code=1, error="worker crashed")]

        async def _fake_run_process_async(command, args=(), **kwargs):
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
            f"Verification must not run when a worker failed, got: {verify_calls}"
        )

    def test_post_fanout_verification_failure_routes_to_recovery(
        self, monkeypatch, tmp_path
    ) -> None:
        """When verification fails, the returned state must be PHASE_FAILED
        with last_error containing the verification failure message."""
        unit = _make_work_unit("unit-a")
        effect = FanOutDevelopmentEffect(
            work_units=(unit,),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        # Exhaust the dev_chain retries so the reducer transitions to PHASE_FAILED
        # rather than scheduling another retry attempt.
        exhausted_chain = AgentChainState(agents=[], current_index=0, retries=_MAX_AGENT_RETRIES)
        state = PipelineState(
            phase=PHASE_DEVELOPMENT, work_units=(unit,), dev_chain=exhausted_chain
        )
        policy_bundle = _make_policy_bundle(max_workers=1)
        workspace_scope = WorkspaceScope(tmp_path)

        verify_failure_output = "type check failed: 3 errors found"

        class _FakeExecutor:
            def __init__(self, command, signal_bridge=None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace) -> None:
                del workspace

        async def _fake_run_fan_out(**kwargs):
            return []

        async def _fake_run_process_async(command, args=(), **kwargs):
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
        monkeypatch.setattr("ralph.executor.process.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

        final_state = runner_module._execute_fan_out_sync(
            effect=effect,
            state=state,
            display=runner_module._LegacyConsoleDisplay(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert final_state.phase == PHASE_FAILED, (
            f"Expected PHASE_FAILED after verification failure, got: {final_state.phase}"
        )
        assert final_state.last_error is not None, (
            "last_error must be set after verification failure"
        )
        assert verify_failure_output in (final_state.last_error or ""), (
            f"last_error must contain the verification output, got: {final_state.last_error!r}"
        )

    def test_verification_runs_after_all_workers_finish_never_concurrently(
        self, monkeypatch, tmp_path
    ) -> None:
        """Verification starts only after the last worker finishes; never overlaps workers.

        Captures the sequence of events: fan_out_started, fan_out_ended, verify_started,
        verify_ended. Asserts that verify_started >= fan_out_ended (no overlap).
        By construction, _execute_fan_out_sync is synchronous — it awaits the coordinator
        before running verification — so overlap is architecturally impossible. This test
        proves that guarantee holds through the call chain.
        """
        import time  # noqa: PLC0415

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        effect = FanOutDevelopmentEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=True,
        )
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit_a, unit_b))
        policy_bundle = _make_policy_bundle(max_workers=2)
        workspace_scope = WorkspaceScope(tmp_path)

        timestamps: dict[str, float] = {}
        call_order: list[str] = []

        class _FakeExecutor:
            def __init__(self, command, signal_bridge=None) -> None:
                del command, signal_bridge

        class _FakeMcpFactory:
            def __init__(self, workspace) -> None:
                del workspace

        async def _fake_run_fan_out(**kwargs):
            timestamps["fan_out_started"] = time.monotonic()
            call_order.append("fan_out")
            # Simulate two workers completing
            timestamps["fan_out_ended"] = time.monotonic()
            return []

        async def _fake_run_process_async(command, args=(), **kwargs):
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
        monkeypatch.setattr("ralph.executor.process.run_process_async", _fake_run_process_async)
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

        runner_module._execute_fan_out_sync(
            effect=effect,
            state=state,
            display=runner_module._LegacyConsoleDisplay(),
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
        )

        assert "fan_out" in call_order, "fan_out must have been called"
        assert "verify" in call_order, "verify must have been called"
        assert call_order.index("fan_out") < call_order.index("verify"), (
            f"fan_out must precede verify, got: {call_order}"
        )
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
