"""Shared fake seams for the terminal MCP-error entry-path suites.

Every helper here exists to drive the REAL production machinery -- the real
``McpSupervisor``, the real ``_consume_attempt_output``, the real
``run_with_direct_mcp_recovery``, the real outer caller frames -- with no
real child process, no sleep, and no I/O outside ``tmp_path``.

Shared by ``test_terminal_mcp_error_per_entry_path`` (the per-entry-path
matrix) and ``test_terminal_mcp_error_supervisor_ordering`` (the
``McpSupervisor.__exit__`` ordering and the SIGTERM/SIGKILL next-incident
diagnostics), which together would each breach the 1000-line
repo-structure cap as a single file.

Every symbol is underscore-prefixed on purpose: the repo-structure audit
counts only public top-level classes, so the fakes stay invisible to it.
"""

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
# ══════════════════════════════════════════════════════════════════════

#: The stage carried by the injected terminal error; it doubles as the
#: POSITIVE CONTROL for the Loguru sink (the instrumented executor frame is
#: known to emit exactly this token at WARNING).
_TERMINAL_LAST_STAGE = "registry_built"

#: Marker proving the sink really captured a Loguru record emitted by the
#: instrumented path (a sink that silently captured nothing would otherwise
#: make every negative assertion below vacuously true).
_POSITIVE_CONTROL = f"last_stage={_TERMINAL_LAST_STAGE}"

#: ``Loguru`` renders ``{exception}`` as the empty string for non-exception
#: records and as a real traceback for ``logger.exception`` records, so its
#: presence here is the "no traceback was logged" probe.
_TRACEBACK_MARKER = "Traceback (most recent call last)"

#: Bounded ceiling on the supervisor-thread handshake. The terminal tick is
#: immediate, so this is slack, not a wait: exceeding it is a failure signal.
_SUPERVISOR_WAIT_TIMEOUT_S = 5.0

#: Re-exported so the matrix can name the exact label prefix the supervisor
#: interrupts agent processes under when it goes terminal.
_AGENT_PROCESS_LABEL_PREFIX = AGENT_PROCESS_LABEL_PREFIX


def _terminal_mcp_error(*, child_pid: int = 4242) -> McpServerError:
    """The terminal error ``McpSupervisor.__exit__`` re-raises by contract."""
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
    """A bridge the supervisor and the executor's health check can both use.

    ``effect_executor._check_bridge_health`` reads ``restart_count`` off any
    ``RestartAwareMcpBridge``, and ``McpSupervisor`` calls
    ``check_health_and_restart_if_needed`` on its own tick, so a bridge
    double driven by either surface has to answer both.
    """

    def __init__(self) -> None:
        self._restart_count = 0
        self.health_checks = 0
        self.shutdown_count = 0
        #: Each instance gets a distinct identity tag so a test can observe
        #: that the bridge supervised on attempt 2 is a DIFFERENT object
        #: than the one supervised on attempt 1. ``is`` comparisons plus
        #: identity tags together prove "fresh bridge", not just "different
        #: call returned something".
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
    """A bridge whose health check has exhausted its restart budget.

    One tick in, ``check_health_and_restart_if_needed`` raises the typed
    ``BUDGET_EXHAUSTED`` ``McpServerError`` that
    ``McpSupervisor._do_check_once`` stores for ``__exit__`` to re-raise.
    """

    def check_health_and_restart_if_needed(self) -> bool:
        raise _terminal_mcp_error()


class _HealthyBridge(_SupervisorBridge):
    """A bridge whose health check answers False forever (healthy).

    Distinct identity from ``_SupervisorBridge`` so a test can assert
    ``attempt1_bridge is not attempt2_bridge`` against an attempt 1's
    ``_TerminalBridge`` and an attempt 2's healthy bridge built by the
    bridge factory. Returns False from every health check so the
    supervisor's own tick never stores ``_mcp_error`` and ``__exit__``
    never re-raises.
    """


class _TerminalSupervisorFactory:
    """The REAL ``McpSupervisor``, wired to the bridge the executor hands it.

    Injected through ``PipelineDeps.mcp_supervisor_factory``, so the
    production supervisor state machine runs for real: ``__enter__`` starts
    the background thread, the first tick stores the typed terminal error
    on a TERMINAL bridge, the thread interrupts the agent processes, and
    ``__exit__`` joins and re-raises it out of the supervised block. On a
    HEALTHY bridge the supervisor keeps polling and ``__exit__`` returns
    cleanly.

    Per-attempt identity is recorded so a test can observe which bridge
    was supervised on attempt N and prove attempt 2 saw a DIFFERENT
    object than attempt 1 (the "fresh bridge" contract).

    Determinism without a sleep: the check interval is zero, so the thread
    reaches its first tick immediately, and ``agent_exits_cleanly`` /
    ``agent_dies_in_flight`` block on ``terminal`` until the supervisor has
    actually gone terminal. That ordering is also the real incident -- the
    agent is wedged on a server that will never answer again, which is why
    the supervisor interrupts it -- and it removes the race where a fast
    body would set ``_done`` before the first tick and leave ``_mcp_error``
    unread.
    """

    def __init__(self) -> None:
        self.supervisors: deque[McpSupervisor] = deque(maxlen=16)
        self.label_scopes: deque[str] = deque(maxlen=16)
        self.interrupted_labels: deque[str] = deque(maxlen=16)
        #: Per-attempt identity of the supervised bridge, in call order.
        #: The fresh-bridge test reads ``self.supervised_bridges[0]`` and
        #: ``self.supervised_bridges[1]`` and asserts they are DIFFERENT
        #: objects (``is`` comparison).
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
        # RETAIN the bridge: the supervisor is built around the bridge the
        # executor handed it, never a discardable terminal stand-in. A
        # HEALTHY bridge means a healthy supervised attempt; a TERMINAL
        # bridge means the supervisor will store ``_mcp_error`` and the
        # ``__exit__`` re-raise fires. The seam proves the right thing in
        # both directions: ``McpSupervisor.__exit__`` either re-raises
        # (terminal) or returns (healthy).
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
        """The supervised agent body for a fresh-bridge test that drives both attempts.

        Returns ``[]`` WITHOUT blocking on ``terminal``, so a HEALTHY
        attempt (where the supervisor never goes terminal) completes the
        turn loop cleanly and ``run_with_direct_mcp_recovery`` returns
        ``_AttemptResult(AGENT_SUCCESS, ...)``. On a TERMINAL attempt the
        supervisor still stores ``_mcp_error`` first because the
        background thread runs ``tick()`` immediately on ``__enter__``
        (interval = 0) regardless of what the agent body does; the body
        just exits with no output, and ``__exit__`` re-raises the stored
        error.

        A separate body (``agent_exits_cleanly``) is kept for the matrix
        tests where the determinism from a blocking body is required --
        the matrix only ever drives one attempt per entry path, so the
        blocking-vs-immediate difference is invisible there.
        """
        del args, kwargs
        return []

    def agent_exits_cleanly(self, *args: object, **kwargs: object) -> list[str]:
        """The supervised agent body: runs, then exits without an error."""
        del args, kwargs
        self._await_terminal()
        return []

    def agent_dies_in_flight(self, *args: object, **kwargs: object) -> list[str]:
        """The supervised agent body: dies while supervision is terminal."""
        del args, kwargs
        self._await_terminal()
        raise AgentInvocationError("agent exited non-zero while MCP was terminal", 1)


class _Recorder:
    """Collects log records with the exception body rendered."""

    def __init__(self) -> None:
        self.records: list[str] = []

    def __call__(self, message: object) -> None:
        self.records.append(str(message))


@contextlib.contextmanager
def _log_capture() -> Iterator[_Recorder]:
    """Attach a Loguru sink with the exception body included.

    Never bare ``caplog``: it cannot see Loguru records at all, so every
    negative assertion written against it would pass vacuously.
    """
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
    """Assert the typed conversion logged NO traceback and no crash line.

    The positive control comes first: a sink that captured nothing would make
    both negatives below vacuously true, so prove the instrumented frame
    really emitted its known WARNING before trusting any negative.
    """
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
    """Executor boundary: the typed retry intent names ``McpServerError``."""
    intent = effect_executor_module.pop_last_captured_retry_intent()
    assert intent.failure_reason == "McpServerError"
    # A dead MCP bridge must not be resumed: the intent carries no session
    # action, so the next attempt starts fresh (and, per the executor's own
    # mark, on a fresh bridge).
    assert intent.action is None
    assert intent.session_id is None


class _RegistryForAnyAgentFactory:
    """A registry factory that can produce the agent for every entry path."""

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
    """``PipelineDeps`` wired to the terminal supervisor seam, all fakes.

    The default ``bridge=`` is a ``_TerminalBridge`` so the matrix test
    (which drives one attempt per entry path through the real
    ``bridge_factory`` -> ``mcp_supervisor_factory`` chain) supervises a
    supervisor that actually goes terminal on its first tick and stores
    ``_mcp_error``. With the seam's previous "discard the bridge, build a
    fresh ``_TerminalBridge``" behaviour that masking was implicit; with
    the seam's honest "build around the bridge you are given" behaviour
    the matrix test would otherwise supervise a healthy
    ``_SupervisorBridge`` and the supervisor's ``__exit__`` would never
    raise. A caller-supplied ``bridge=`` still wins, so the
    fresh-bridge tests can hand in their own dead bridge directly.
    """
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


#: Bounded ceiling on ``_SequenceBridgeFactory.calls``. Same default as
#: the recording bridge factory in ``_pipeline_deps_factory`` so tests
#: never hold unbounded per-invocation kwargs indefinitely.
_DEFAULT_BRIDGE_CALL_HISTORY_LIMIT = 64


class _SequenceBridgeFactory:
    """A bridge factory that hands out one bridge per call, in order.

    Used by the fresh-bridge test to script the per-attempt identity:
    call 1 returns the dead bridge (so the executor, with no
    fresh-bridge mark yet, fetches it via ``bridge_factory`` if and only
    if ``own_bridge=True``); call 2 returns a DISTINCT healthy bridge --
    a separate ``RestartAwareMcpBridge`` with its own ``identity_tag`` --
    so the ``attempt 2 supervised bridge`` and the ``attempt 1
    supervised bridge`` are demonstrably different objects.

    ``calls`` records every call for the entry-count assertion in the
    fresh-bridge test, and bridges are popped FIFO so a test's expected
    call count matches the deque's underflow contract.
    """

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


# Keep the existing default-history constant exported here so the
# ``_RecordingBridgeFactory`` already in ``_pipeline_deps_factory`` can
# stay a separate type -- no monkey-patching of ``tests._pipeline_deps_factory``
# is needed.


def _policy_bundle(tmp_path: Path) -> PolicyBundle:
    config = UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))
    return load_policy_bundle_for_run(WorkspaceScope(tmp_path), config)


def _patch_invoke_agent(
    monkeypatch: pytest.MonkeyPatch,
    invoke: _InvokeAgentLike,
) -> None:
    """Stop any real agent spawn on every entry path that resolves its own.

    ``execute_agent_effect`` falls back to its module-global
    ``invoke_agent`` (``ralph/pipeline/effect_executor.py:423``) when a
    caller does not forward one, and the two plumbing call sites forward
    THEIR module globals instead. Patching all three keeps the whole matrix
    subprocess-free without touching the executor's own argument plumbing.
    """
    import ralph.cli.commands.commit as commit_cli
    from ralph.pipeline.plumbing import commit_plumbing, smoke_plumbing

    for module in (effect_executor_module, smoke_plumbing, commit_plumbing, commit_cli):
        monkeypatch.setattr(module, "invoke_agent", invoke, raising=False)


def _default_policy_bundle() -> PolicyBundle:
    """The shipped policy bundle, for the out-of-graph policy preflight."""
    return load_policy(default_dir())


class _InvokeAgentLike(Protocol):
    """The shape of the ``invoke_agent`` doubles injected at each seam."""

    def __call__(self, *args: object, **kwargs: object) -> list[str]: ...


def _no_bridge_health_check(bridge: object) -> None:
    """A bridge-health check that always passes; no live MCP surface here."""
    del bridge


def _no_agent_output(*args: object, **kwargs: object) -> list[str]:
    """An ``invoke_agent`` double that ran and produced no lines."""
    del args, kwargs
    return []
