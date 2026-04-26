"""Integration tests for serialized post-fanout verification.

Verifies that when run_post_fanout_verification=True:
- Workspace-wide verification runs exactly once, after all workers finish.
- Verification is skipped when any worker fails.
- Concurrent invocation is impossible by construction (serialized after coordinator returns).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.config.enums import PHASE_DEVELOPMENT
from ralph.executor.process import ProcessResult
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.workspace.scope import WorkspaceScope


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
    bundle.pipeline.parallel_execution.max_parallel_workers = max_workers
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
        """_determine_effect_from_policy sets run_post_fanout_verification=True."""
        unit = _make_work_unit("unit-a")
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit,))
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

