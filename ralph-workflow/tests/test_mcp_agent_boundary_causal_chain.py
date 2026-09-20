from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeGuard

import pytest

from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._completion import check_process_result
from ralph.agents.invoke._direct_mcp_recovery import run_with_direct_mcp_recovery
from ralph.pipeline.session_bridge import scoped_reset_tool_registry_callback
from ralph.process._agent_launch_error import AgentLaunchError
from ralph.process._spawn_validation import prepare_spawn_command
from ralph.runtime_events import current_runtime_event

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess


class _SharedBridge:
    def __init__(self, during_reset: Callable[[], None] | None = None) -> None:
        self.resets = 0
        self.during_reset = during_reset

    def reset_tool_registry(self) -> None:
        self.resets += 1
        if self.during_reset is not None:
            self.during_reset()


def test_seam_1_and_2_scoped_resets_coalesce_and_clear() -> None:
    bridge = _SharedBridge()
    reset_b = scoped_reset_tool_registry_callback(bridge, "invocation-b")
    assert reset_b is not None
    def reset_b_during_reset() -> None:
        reset_b()

    bridge.during_reset = reset_b_during_reset
    reset_a = scoped_reset_tool_registry_callback(bridge, "invocation-a")
    reset_c = scoped_reset_tool_registry_callback(bridge, "invocation-c")
    assert reset_a is not None
    assert reset_c is not None

    reset_a()
    reset_c()

    assert bridge.resets == 2
    with pytest.raises(ValueError, match="invocation scope"):
        scoped_reset_tool_registry_callback(bridge, "unscoped")


def test_seam_3_oversized_payload_has_runtime_launch_origin() -> None:
    with pytest.raises(AgentLaunchError) as raised:
        prepare_spawn_command(
            ("agent", "x" * 100), cwd=None, env={"A": "y" * 100}, payload_limit=32
        )

    assert raised.value.failure_origin == "runtime_launch"
    assert prepare_spawn_command(("agent",), cwd=None, env={}, payload_limit=32) == ("agent",)


def test_seam_4_watchdog_retains_reset_causality() -> None:
    calls = 0

    def attempt(_session_id: str | None, capture: Callable[[str], None]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            capture("session")
            raise AgentInvocationError(
                "agent",
                1,
                "Model returned an empty response; No such tool available: mcp__ralph__read_file",
                ['{"type":"tool_result"}'],
            )
        raise IdleWatchdogKilledError(
            "no_output_at_start", 15, runtime_event=current_runtime_event()
        )

    with pytest.raises(IdleWatchdogKilledError) as raised:
        run_with_direct_mcp_recovery(attempt, max_retries=1, reset_tool_registry=lambda: None)

    assert raised.value.failure_origin == "watchdog_observation"
    assert raised.value.runtime_event == "mcp_operation"


class _CompletionHandle:
    pid = 42
    returncode = 1
    stderr = None

    def termination_issuer(self) -> None:
        return None


def _is_managed_process(value: object) -> TypeGuard[ManagedProcess]:
    return isinstance(value, _CompletionHandle)


def test_seam_5_completion_teardown_names_its_issuer() -> None:
    recorded: list[tuple[int, str]] = []

    class _Teardown:
        def teardown_subtree(self, pid: int, *, issuer: str) -> None:
            recorded.append((pid, issuer))

    handle = _CompletionHandle()
    assert _is_managed_process(handle)
    with pytest.raises(AgentInvocationError) as raised:
        check_process_result(handle, "agent", process_teardown=_Teardown())

    assert raised.value.failure_origin == "agent"
    assert recorded == [(42, "invoke:completion:agent")]


def test_typed_origins_stay_distinguishable_without_stderr() -> None:
    runtime = AgentLaunchError("agent", OSError(7, "too big"), 1)
    watchdog = IdleWatchdogKilledError("idle", 15)
    intentional = AgentInvocationError("agent", -15, failure_origin="intentional_termination", issuer="invoke:x")
    mcp = AgentInvocationError("agent", 1, failure_origin="mcp_operation")
    agent = AgentInvocationError("agent", 1)

    assert {exc.failure_origin for exc in (runtime, watchdog, intentional, mcp, agent)} == {
        "runtime_launch",
        "watchdog_observation",
        "intentional_termination",
        "mcp_operation",
        "agent",
    }
