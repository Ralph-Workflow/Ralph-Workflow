"""Invocation options and the invoke path's collaborator-injection surface.

``InvokeOptions`` is the public handle callers use to shape one agent
invocation.  Besides the per-invocation values it carries the scoped
collaborator overrides used to inject fakes into the invoke path WITHOUT
reaching into module internals:

* :meth:`InvokeOptions.command_builder_override` replaces the argv builder.
* :meth:`InvokeOptions.process_monitor_factory_override` replaces the
  process-monitor factory.
* :meth:`InvokeOptions.workspace_monitor_factory_override` replaces the
  workspace-monitor factory.
* :meth:`InvokeOptions.upstream_env_applier_override` replaces the upstream
  MCP env projection used by the per-transport runtime resolvers.
* :meth:`InvokeOptions.platform_override` replaces the host platform the
  runtime resolvers use for OS-specific path selection.

Every override is scoped to a ``with`` block and is stored in a
:class:`~contextvars.ContextVar`, so the override is visible to the code that
runs inside the block (including the generators the invoke path yields from)
and is undone when the block exits -- even if the block raises.  Production
callers never open an override scope, so the default behaviour is unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

_INVOKE_OPTS_UNSET: object = object()

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from ralph.agents.idle_watchdog import (
        SubagentPidRegistry,
        TimeoutPolicy,
        WaitingStatusListener,
    )
    from ralph.agents.invoke._build_command_options import _BuildCommandOptions
    from ralph.agents.invoke._workspace import WorkspaceMonitor
    from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier
    from ralph.config.models import AgentConfig
    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess
    from ralph.process.monitor import ProcessMonitor, SubagentPidSource
    from ralph.process.teardown import ProcessTeardown
    from ralph.workspace.probe import WorkspaceChangeProbe
    from ralph.workspace.protocol import Workspace


class _CommandBuilder(Protocol):
    """Builds the argv for one agent invocation."""

    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]: ...


class _ProcessMonitorFactory(Protocol):
    """Builds the process monitor for one agent invocation."""

    def __call__(
        self,
        handle: ManagedProcess | ManagedPtyProcess,
        config: AgentConfig,
        policy: TimeoutPolicy,
        subagent_pid_source: SubagentPidSource | None = None,
        *,
        registry: ChildLivenessRegistry | None = None,
        scope_prefix: str = "",
    ) -> ProcessMonitor | None: ...


class _WorkspaceMonitorFactory(Protocol):
    """Builds the workspace monitor for one agent invocation."""

    def __call__(
        self,
        workspace_path: Path,
        classifier: WorkspaceChangeClassifier | None = None,
        *args: object,
        **kwargs: object,
    ) -> WorkspaceMonitor | None: ...


class _UpstreamEnvApplier(Protocol):
    """Projects resolved upstream MCP servers into the runtime and server envs."""

    def __call__(
        self,
        upstreams: tuple[UpstreamMcpServer, ...],
        workspace_path: Path | None,
        runtime_env: dict[str, str],
        server_env: dict[str, str],
    ) -> None: ...


_COMMAND_BUILDER_OVERRIDE: ContextVar[_CommandBuilder | None] = ContextVar(
    "ralph_invoke_command_builder_override",
    default=None,
)
_PROCESS_MONITOR_FACTORY_OVERRIDE: ContextVar[_ProcessMonitorFactory | None] = ContextVar(
    "ralph_invoke_process_monitor_factory_override",
    default=None,
)
_WORKSPACE_MONITOR_FACTORY_OVERRIDE: ContextVar[_WorkspaceMonitorFactory | None] = ContextVar(
    "ralph_invoke_workspace_monitor_factory_override",
    default=None,
)
_UPSTREAM_ENV_APPLIER_OVERRIDE: ContextVar[_UpstreamEnvApplier | None] = ContextVar(
    "ralph_invoke_upstream_env_applier_override",
    default=None,
)
_PLATFORM_OVERRIDE: ContextVar[str | None] = ContextVar(
    "ralph_invoke_platform_override",
    default=None,
)


@dataclass(frozen=True)
class InvokeOptions:
    """Options for agent invocation."""

    # Optional workspace-monitor factory. Production callers leave this
    # ``None`` so :mod:`ralph.agents.invoke` constructs the real
    # ``WorkspaceMonitor`` (which starts a real watchdog observer).
    # Tests that only exercise routing / dispatch behavior inject a
    # factory that returns ``None`` (skip the observer entirely) or a
    # fake monitor so they do not block on the real watchdog observer's
    # ``start`` / ``stop`` cost under the 1-second per-test timeout
    # enforced by ``tests/conftest.py``. The factory signature mirrors
    # :func:`ralph.agents.invoke._start_workspace_monitor` so callers
    # can pass a thin ``lambda *args, **kwargs: None`` shortcut.
    workspace_monitor_factory: (
        Callable[[Path, WorkspaceChangeClassifier | None], WorkspaceMonitor | None] | None
    ) = None
    workspace_probe: WorkspaceChangeProbe | None = None
    workspace: Workspace | None = None
    phase: str | None = None

    model_flag: str | None = None
    session_id: str | None = None
    verbose: bool = False
    show_progress: bool = True
    workspace_path: Path | None = None
    extra_env: dict[str, str] | None = None
    unsafe_mode: bool = False
    idle_timeout_seconds: float | None = None
    activity_only_supervision: bool = False
    activity_only_operator_cap_seconds: float | None = None
    activity_only_status_interval_seconds: float | None = None
    relay_activity_sink_register: Callable[[Callable[[str], None]], Callable[[], None]] | None = (
        None
    )
    relay_health_error: Callable[[], str | None] | None = None
    drain_window_seconds: float | None = None
    max_waiting_on_child_seconds: float | None = None
    idle_poll_interval_seconds: float | None = None
    parent_exit_grace_seconds: float | None = None
    descendant_wait_timeout_seconds: float | None = None
    descendant_wait_poll_seconds: float | None = None
    process_exit_wait_seconds: float | None = None
    max_session_seconds: float | None = None
    waiting_status_interval_seconds: float | None = None
    suspect_waiting_on_child_seconds: float | None = None
    child_progress_ttl_seconds: float | None = None
    child_heartbeat_ttl_seconds: float | None = None
    child_stale_label_ttl_seconds: float | None = None
    child_exit_reconcile_seconds: float | None = None
    max_waiting_on_child_no_progress_seconds: float | None = None
    no_progress_quiet_seconds: float | None = None
    no_output_at_start_seconds: float | None = None
    no_progress_quiet_minimum_invocation_seconds: float | None = None
    no_progress_quiet_heartbeat_ceiling_seconds: float | None | object = _INVOKE_OPTS_UNSET
    post_tool_result_progression_seconds: float | None = None
    repeated_error_consecutive_threshold: int | None = None
    repeated_error_window_count: int | None = None
    repeated_error_window_seconds: float | None = None
    activity_evidence_ttl_seconds: float | None = None
    workspace_change_weights: dict[str, float] | None = None
    process_monitor_enabled: bool | None = None
    process_teardown: ProcessTeardown | None = None
    subagent_output_capture_enabled: bool | None = None
    subagent_output_poll_interval_seconds: float | None = None
    os_descendant_only_ceiling_seconds: float | None | object = _INVOKE_OPTS_UNSET
    os_descendant_only_suspect_seconds: float | None | object = _INVOKE_OPTS_UNSET
    cpu_idle_seconds: float | None | object = _INVOKE_OPTS_UNSET
    log_growth_seconds: float | None | object = _INVOKE_OPTS_UNSET
    master_prompt_file: str | None = None
    waiting_listener: WaitingStatusListener | None = None
    pre_output_listener: Callable[[], None] | None = None
    permission_prompt_listener: Callable[[str], None] | None = None
    required_artifact: RequiredArtifact | None = None
    requires_completion_evidence: bool = True
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None
    initial_session_id: str | None = None
    settings_json: str | None = None
    stop_sentinel_path: Path | None = None
    # Live runtime signals from the pipeline that the watchdog consults
    # on every evaluate() call so the StuckClassifier gate can return
    # DUPLICATE_KILL (when the pipeline is already in a wait state)
    # or WAITING_ON_CONNECTIVITY (when the network is offline) and
    # defer the fire. Both providers are optional; the watchdog
    # falls back to "no live signal" when they are None.
    connectivity_state_provider: Callable[[], str | None] | None = None
    is_waiting_state_provider: Callable[[], bool] | None = None
    # R1 / R5 (Trustworthy Idle Watchdog spec): when the orchestrator
    # pre-builds a per-invocation ``SubagentPidRegistry`` and its
    # per-transport ``SubagentPidSource``, it threads them through
    # here so the SAME registry reaches both the strategy layer
    # (``strategy_for_command(..., subagent_pid_source=...)``) and
    # the parser layer (``stream_parsed_agent_activity(...
    # subagent_pid_registry=...)``). Without these fields, the
    # strategy layer builds a FRESH registry internally and the
    # parser-registered PIDs never reach the strategy's filtered
    # count -- the watchdog-visible filtered subagent count is
    # desynchronized from the parser's authoritative registration
    # set. The shared-registry contract is documented at
    # ``ralph/agents/registry.py:build_subagent_pid_registry``.
    #
    # Both fields default to ``None`` so the legacy direct-call
    # signature ``invoke_agent(config, prompt_file, options=...)``
    # still works (the strategy layer builds a fresh registry
    # internally for backward compat with test fakes that pre-date
    # the R5 cross-transport wiring).
    subagent_pid_registry: SubagentPidRegistry | None = None
    subagent_pid_source: SubagentPidSource | None = None

    def __post_init__(self) -> None:
        if self.workspace_monitor_factory is None:
            override = _WORKSPACE_MONITOR_FACTORY_OVERRIDE.get()
            if override is not None:
                object.__setattr__(self, "workspace_monitor_factory", override)

    # ------------------------------------------------------------------
    # Scoped collaborator overrides (the invoke path's injection seam)
    # ------------------------------------------------------------------
    #
    # Each override is a context manager that installs a fake for the
    # duration of the block.  They live on the public options class so a
    # caller can inject a collaborator without importing (or rebinding) a
    # production private.

    @classmethod
    @contextmanager
    def command_builder_override(cls, builder: _CommandBuilder) -> Iterator[None]:
        """Replace the argv builder for the duration of the block.

        Args:
            builder: Callable invoked with the agent config, the prompt
                file and the resolved ``BuildCommandOptions``; it returns
                the argv for the invocation.

        Yields:
            Nothing; the scope is the unit of installation.
        """
        token = _COMMAND_BUILDER_OVERRIDE.set(builder)
        try:
            yield
        finally:
            _COMMAND_BUILDER_OVERRIDE.reset(token)

    @classmethod
    @contextmanager
    def process_monitor_factory_override(cls, factory: _ProcessMonitorFactory) -> Iterator[None]:
        """Replace the process-monitor factory for the duration of the block.

        Args:
            factory: Callable invoked with the process handle, the agent
                config, the timeout policy and the optional subagent PID
                source; it returns the monitor (or ``None`` to skip it).

        Yields:
            Nothing; the scope is the unit of installation.
        """
        token = _PROCESS_MONITOR_FACTORY_OVERRIDE.set(factory)
        try:
            yield
        finally:
            _PROCESS_MONITOR_FACTORY_OVERRIDE.reset(token)

    @classmethod
    @contextmanager
    def workspace_monitor_factory_override(
        cls,
        factory: _WorkspaceMonitorFactory | None,
    ) -> Iterator[None]:
        """Replace the workspace-monitor factory for the duration of the block.

        Args:
            factory: Callable invoked with the workspace path and optional
                workspace change classifier; returns a WorkspaceMonitor or None.

        Yields:
            Nothing; the scope is the unit of installation.
        """
        token = _WORKSPACE_MONITOR_FACTORY_OVERRIDE.set(factory)
        try:
            yield
        finally:
            _WORKSPACE_MONITOR_FACTORY_OVERRIDE.reset(token)

    @classmethod
    @contextmanager
    def upstream_env_applier_override(cls, applier: _UpstreamEnvApplier) -> Iterator[None]:
        """Replace the upstream MCP env projection for the duration of the block.

        Args:
            applier: Callable invoked with the resolved upstreams, the
                workspace path and the runtime / server environment dicts
                it mutates.

        Yields:
            Nothing; the scope is the unit of installation.
        """
        token = _UPSTREAM_ENV_APPLIER_OVERRIDE.set(applier)
        try:
            yield
        finally:
            _UPSTREAM_ENV_APPLIER_OVERRIDE.reset(token)

    @classmethod
    @contextmanager
    def platform_override(cls, platform: str) -> Iterator[None]:
        """Replace the host platform for the duration of the block.

        The runtime resolvers use the platform to pick OS-specific paths
        (the Cursor IDE state database lives in a different place on
        macOS).  Overriding it keeps those paths testable on any host.

        Args:
            platform: Platform identifier, e.g. ``"darwin"`` or ``"linux"``.

        Yields:
            Nothing; the scope is the unit of installation.
        """
        token = _PLATFORM_OVERRIDE.set(platform)
        try:
            yield
        finally:
            _PLATFORM_OVERRIDE.reset(token)
