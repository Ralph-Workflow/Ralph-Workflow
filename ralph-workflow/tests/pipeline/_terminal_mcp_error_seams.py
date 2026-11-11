from __future__ import annotations

import contextlib
import threading
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from loguru import logger as loguru_logger

from ralph.agents.invoke import AgentInvocationError
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.protocol.startup import HeartbeatPolicy
from ralph.mcp.server._mcp_attempt_result import RestartAttemptResult
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.runner import load_policy_bundle_for_run
from ralph.policy.loader import default_dir, load_policy
from ralph.process.mcp_supervisor import AGENT_PROCESS_LABEL_PREFIX, McpSupervisor
from ralph.workspace import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager

    import pytest

    from ralph.config.enums import AgentTransport
    from ralph.pipeline.factory import MaterializeMasterPromptFn, PipelineDeps
    from ralph.policy.models import PolicyBundle

_TERMINAL_LAST_STAGE = "registry_built"

_POSITIVE_CONTROL = f"last_stage={_TERMINAL_LAST_STAGE}"

_TRACEBACK_MARKER = "Traceback (most recent call last)"

_SUPERVISOR_WAIT_TIMEOUT_S = 5.0

_AGENT_PROCESS_LABEL_PREFIX = AGENT_PROCESS_LABEL_PREFIX


def _terminal_mcp_error(*, child_pid: int = 4242) -> McpServerError:
    return McpServerError(
        "MCP server restart budget exhausted",
        restart_count=3,
        last_stage=_TERMINAL_LAST_STAGE,
        elapsed_s=12.5,
        restart_reason="preflight_timeout",
        handover_step="tools/call",
        probe_duration_s=10.0,
        probe_timeout_s=30.0,
        child_pid=child_pid,
        psutil_status="terminated",
        failure_kind=RestartAttemptResult.BUDGET_EXHAUSTED,
    )


class _SupervisorBridge(RestartAwareMcpBridge):
    def __init__(self) -> None:
        self._restart_count = 0
        self.health_checks = 0
        self.shutdown_count = 0
        self.identity_tag: str = f"_SupervisorBridge<{id(self)}>"

    @property
    def run_id(self) -> str:
        return "test-run-id"

    @property
    def restart_count(self) -> int:
        return self._restart_count

    def check_health_and_restart_if_needed(self) -> bool:
        self.health_checks += 1
        return False

    def reset_session_budget(self) -> None:
        pass

    def start(self) -> None:
        pass

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:12345/mcp"

    def endpoint_uri(self) -> str:
        return self.agent_endpoint_uri()

    def shutdown(self) -> None:
        self.shutdown_count += 1


class _TerminalBridge(_SupervisorBridge):
    def check_health_and_restart_if_needed(self) -> bool:
        raise _terminal_mcp_error()


class _HealthyBridge(_SupervisorBridge):
    pass


class _TerminalSupervisorFactory:
    def __init__(self) -> None:
        self.supervisors: deque[McpSupervisor] = deque(maxlen=16)
        self.label_scopes: deque[str] = deque(maxlen=16)
        self.interrupted_labels: deque[str] = deque(maxlen=16)
        self.supervised_bridges: deque[RestartAwareMcpBridge] = deque(maxlen=16)
        self.terminal = threading.Event()

    def __call__(
        self,
        bridge: RestartAwareMcpBridge,
        *,
        check_interval: timedelta,
        on_restart: Callable[[int], None] | None,
        agent_label_scope: str,
    ) -> AbstractContextManager[object, bool | None]:
        del check_interval
        self.label_scopes.append(agent_label_scope)
        self.supervised_bridges.append(bridge)
        supervisor = McpSupervisor(
            bridge,
            check_interval=timedelta(0),
            on_restart=on_restart,
            agent_label_scope=agent_label_scope,
            terminate_agents=self._note_terminal_interrupt,
            _record_psi_pressure=lambda: None,
            _now=lambda: 0.0,
        )
        self.supervisors.append(supervisor)
        return supervisor

    def _note_terminal_interrupt(self, label: str) -> None:
        self.interrupted_labels.append(label)
        self.terminal.set()

    def _await_terminal(self) -> None:
        assert self.terminal.wait(timeout=_SUPERVISOR_WAIT_TIMEOUT_S), (
            "the supervisor never reached its terminal tick; the agent body "
            "would finish first and __exit__ would leave the stored error unread"
        )

    def attempt_returns_immediately(self, *args: object, **kwargs: object) -> list[str]:
        del args, kwargs
        return []

    def agent_exits_cleanly(self, *args: object, **kwargs: object) -> list[str]:
        del args, kwargs
        self._await_terminal()
        return []

    def agent_dies_in_flight(self, *args: object, **kwargs: object) -> list[str]:
        del args, kwargs
        self._await_terminal()
        raise AgentInvocationError("agent exited non-zero while MCP was terminal", 1)


class _Recorder:
    def __init__(self) -> None:
        self.records: list[str] = []

    def __call__(self, message: object) -> None:
        self.records.append(str(message))


@contextlib.contextmanager
def _log_capture() -> Iterator[_Recorder]:
    recorder = _Recorder()
    sink_id = loguru_logger.add(
        recorder,
        level="DEBUG",
        format="{message}\n{exception}",
        backtrace=True,
        diagnose=False,
    )
    try:
        yield recorder
    finally:
        loguru_logger.remove(sink_id)


def _assert_no_traceback_or_crash(recorder: _Recorder) -> None:
    assert any(_POSITIVE_CONTROL in record for record in recorder.records), (
        "positive control: the instrumented executor frame must emit "
        f"{_POSITIVE_CONTROL!r}; captured records were: {recorder.records}"
    )
    assert not any("Phase handler crashed" in record for record in recorder.records), (
        f"terminal McpServerError must not be logged as a phase-handler crash: {recorder.records}"
    )
    assert not any(_TRACEBACK_MARKER in record for record in recorder.records), (
        f"terminal McpServerError must not be logged via logger.exception: {recorder.records}"
    )


def _assert_typed_retry_intent() -> None:
    intent = effect_executor_module.pop_last_captured_retry_intent()
    assert intent.failure_reason == "McpServerError"
    assert intent.action is None
    assert intent.session_id is None


class _RegistryForAnyAgentFactory:
    def __call__(self, config: UnifiedConfig) -> object:
        del config
        agent_config = AgentConfig(cmd="opencode", output_flag="--json-stream")

        class _Registry:
            def get(self, name: str) -> AgentConfig | None:
                del name
                return agent_config

        return _Registry()


def _fake_materialize_master_prompt(tmp_path: Path) -> MaterializeMasterPromptFn:
    def materialize(
        workspace_root: Path,
        name: str,
        default_product_criteria: str | None = None,
        worker_namespace: Path | None = None,
        planning_style: bool = False,
        transport: AgentTransport | None = None,
    ) -> str:
        del (
            workspace_root,
            name,
            default_product_criteria,
            worker_namespace,
            planning_style,
            transport,
        )
        return str(tmp_path / "MASTER_PROMPT.md")

    return materialize


def _fast_heartbeat() -> HeartbeatPolicy:
    return HeartbeatPolicy(interval=timedelta(seconds=2))


def _deps_with_terminal_supervisor(
    tmp_path: Path,
    *,
    bridge: RestartAwareMcpBridge | None = None,
) -> tuple[PipelineDeps, _TerminalSupervisorFactory]:
    factory = _TerminalSupervisorFactory()
    deps = make_test_pipeline_deps(
        display_context=make_display_context(),
        bridge=bridge if bridge is not None else _TerminalBridge(),
        master_prompt_materializer=_fake_materialize_master_prompt(tmp_path),
        registry_factory=_RegistryForAnyAgentFactory(),
        check_mcp_bridge_health_fn=_no_bridge_health_check,
        mcp_supervisor_factory=factory,
        heartbeat_policy_from_env_fn=_fast_heartbeat,
    )
    return deps, factory


_DEFAULT_BRIDGE_CALL_HISTORY_LIMIT = 64


class _SequenceBridgeFactory:
    def __init__(
        self,
        bridges: deque[RestartAwareMcpBridge],
        *,
        call_history_limit: int = _DEFAULT_BRIDGE_CALL_HISTORY_LIMIT,
    ) -> None:
        self._bridges = bridges
        self.calls: deque[dict[str, object]] = deque(maxlen=call_history_limit)

    def __call__(
        self,
        *,
        workspace_root: Path,
        drain: str,
        agents_policy: object,
        transport: object = None,
        capabilities: frozenset[str] | None = None,
        session_id_prefix: str | None = None,
        run_id: str | None = None,
        model_identity: object = None,
        parallel_worker: bool = False,
        worker_namespace: Path | None = None,
        worker_artifact_dir: Path | None = None,
        allowed_roots: tuple[Path, ...] | None = None,
    ) -> RestartAwareMcpBridge:
        self.calls.append(
            {
                "workspace_root": workspace_root,
                "drain": drain,
                "agents_policy": agents_policy,
                "transport": transport,
                "session_id_prefix": session_id_prefix,
                "run_id": run_id,
                "parallel_worker": parallel_worker,
            }
        )
        assert self._bridges, (
            "_SequenceBridgeFactory ran out of bridges mid-test; the "
            "test expected fewer bridge_factory calls than were made"
        )
        return self._bridges.popleft()


def _policy_bundle(tmp_path: Path) -> PolicyBundle:
    config = UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))
    return load_policy_bundle_for_run(WorkspaceScope(tmp_path), config)


def _patch_invoke_agent(
    monkeypatch: pytest.MonkeyPatch,
    invoke: _InvokeAgentLike,
) -> None:
    import ralph.cli.commands.commit as commit_cli
    from ralph.pipeline.plumbing import commit_plumbing, smoke_plumbing

    for module in (effect_executor_module, smoke_plumbing, commit_plumbing, commit_cli):
        monkeypatch.setattr(module, "invoke_agent", invoke, raising=False)


def _default_policy_bundle() -> PolicyBundle:
    return load_policy(default_dir())


class _InvokeAgentLike(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> list[str]: ...


def _no_bridge_health_check(bridge: object) -> None:
    del bridge


def _no_agent_output(*args: object, **kwargs: object) -> list[str]:
    del args, kwargs
    return []
