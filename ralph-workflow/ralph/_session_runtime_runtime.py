"""Runtime context for Ralph-managed standalone agent sessions."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from loguru import logger

from ralph.agents.invoke import (
    InvokeOptions,
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
)
from ralph.agents.invoke._direct_mcp_recovery import (
    default_direct_mcp_retry_limit,
    iter_with_direct_mcp_recovery,
    summarize_retry_failure_evidence,
)
from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.lifecycle import McpServerExtras, SessionBridgeLike
from ralph.mcp.session_plan import SessionMcpPlan, SessionModelOpts

from ._session_runtime_deps import ManagedAgentSessionDeps

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from ralph.agents.idle_watchdog import WaitingStatusListener
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.policy.models import AgentsPolicy

    from ._session_runtime_request import ManagedAgentSessionRequest


class ManagedAgentSessionRuntime:
    """Host-owned context for running prompt-like mini workflows through Ralph.

    A :class:`ManagedAgentSessionRuntime` is the reusable runtime seam
    Ralph exposes for tools that need to drive a single, isolated agent
    session without entering the full policy-driven pipeline. Callers own
    the higher-level host loop (e.g. plan/verify helpers, ad-hoc prompt
    runners, or external tooling); the runtime owns the MCP bridge, the
    per-session environment, agent invocation wiring, retry handling, and
    optional system-prompt materialization.

    Instances are constructed via :meth:`open` (a classmethod) so the
    bridge and session id lifecycle are managed in one place. The runtime
    is a context manager: ``with runtime: ...`` shuts down the bridge on
    exit (also available directly via :meth:`close`).

    Attributes (set in :meth:`open`, treated as read-only afterwards):
        config: The fully-merged :class:`UnifiedConfig` driving the
            general settings of every :meth:`invoke_prompt_file` turn.
        workspace_root: The repository-relative workspace the agent
            subprocess treats as its working directory.
        agent_config: The selected :class:`AgentConfig` identifying the
            agent CLI to invoke.
        request: The :class:`ManagedAgentSessionRequest` that named the
            session; retained verbatim for diagnostics and checkpoint
            rehydration.
        bridge: The :class:`SessionBridgeLike` started by :meth:`open`.
            Owns the MCP server endpoint the agent reaches as
            ``MCP_ENDPOINT``.
        agent_session: The :class:`AgentSession` carrying the unique
            session id, run id, declared capabilities, and model identity.
        system_prompt_file: Resolved path to a system-prompt file when
            the request named one; ``None`` when the agent runs without an
            explicit system prompt.
        deps: The :class:`ManagedAgentSessionDeps` dependency bundle in
            use (production defaults, or the test stub passed to
            :meth:`open`).

    Invariants:
        - The class is constructed only via :meth:`open`; the regular
          ``__init__`` is reserved for the runtime's internal use to keep
          construction injectable.
        - The MCP bridge owned by the runtime is alive between
          construction and :meth:`close`; callers must invoke
          :meth:`close` (or use the context-manager protocol) regardless
          of how their host loop exits.
    """

    def __init__(
        self,
        *,
        config: UnifiedConfig,
        workspace_root: Path,
        agent_config: AgentConfig,
        request: ManagedAgentSessionRequest,
        bridge: SessionBridgeLike,
        agent_session: AgentSession,
        system_prompt_file: str | None,
        deps: ManagedAgentSessionDeps,
    ) -> None:
        self._config = config
        self._workspace_root = workspace_root
        self._agent_config = agent_config
        self._request = request
        self._bridge = bridge
        self._agent_session = agent_session
        self._system_prompt_file = system_prompt_file
        self._deps = deps

    @classmethod
    def open(
        cls,
        *,
        config: UnifiedConfig,
        workspace_root: Path,
        agent_config: AgentConfig,
        request: ManagedAgentSessionRequest,
        deps: ManagedAgentSessionDeps | None = None,
        agents_policy: AgentsPolicy | None = None,
    ) -> ManagedAgentSessionRuntime:
        """Construct a managed Ralph session ready for :meth:`invoke_prompt_file`.

        ``open`` allocates the session id, starts the MCP bridge that the
        agent will talk to, and (optionally) materializes a system-prompt
        file. It is the only sanctioned way to build a
        :class:`ManagedAgentSessionRuntime`; the regular ``__init__`` is
        reserved for the runtime's internal use so it can be reasoned about
        as a pure dependency-injected bundle.

        Keyword Args:
            config: The fully-merged :class:`ralph.config.models.UnifiedConfig`
                that drives general settings (verbosity, retry limits, JSON
                parser). Reused for every :meth:`invoke_prompt_file` call
                made through the runtime.
            workspace_root: Filesystem location the agent session will treat
                as its working directory. Forwarded to the workspace factory
                in ``deps`` to produce a :class:`Workspace` that the MCP
                bridge will hand to tools.
            agent_config: Selected :class:`ralph.config.models.AgentConfig`
                that names which agent CLI to invoke (e.g. Claude, Codex,
                OpenCode), which transport to use, and which optional model
                flag to pass through.
            request: Caller-supplied :class:`ManagedAgentSessionRequest` that
                names the session id prefix, ``drain``, capabilities, system
                prompt, and any pre-resolved session plan.
            deps: Optional :class:`ManagedAgentSessionDeps` bundle overriding
                one or more collaborator boundaries (workspace factory, MCP
                server starter, agent invoker, system-prompt materializer,
                bridge shutdown). Pass a stubbed bundle in tests to avoid
                real subprocesses and filesystem access. When ``None`` the
                production defaults from
                :class:`ManagedAgentSessionDeps` are used.
            agents_policy: Optional :class:`ralph.policy.models.AgentsPolicy`
                used to resolve MCP capabilities and access modes when
                ``request.capabilities`` and ``request.session_mcp_plan`` are
                both ``None``. Falls back to the policy embedded in
                ``config`` when omitted.

        Returns:
            A fully wired :class:`ManagedAgentSessionRuntime` whose MCP
            bridge is already listening on a private endpoint. The caller
            owns the returned runtime and must invoke :meth:`close` (or use
            it as a context manager) so the bridge shuts down on exit.

        Raises:
            Exception: Any failure during bridge start or system-prompt
                materialization is re-raised **after** any partially-started
                bridge has been shut down via ``deps.shutdown_bridge``, so
                the caller never inherits a half-initialized MCP listener.

        Side Effects:
            - Starts an MCP server subprocess (or in-memory bridge, if the
              ``deps.start_mcp_server`` override returns one) bound to a
              private endpoint.
            - Captures a fresh ``run_id`` (UUID4) that is exposed through
              ``MCP_RUN_ID_ENV`` and ``AGENT_LABEL_SCOPE_ENV`` to the agent
              subprocess.
            - May write a system-prompt file under ``workspace_root`` via
              ``deps.materialize_system_prompt``.
        """
        runtime_deps = deps or ManagedAgentSessionDeps()
        session_plan = _resolve_session_plan(
            request=request,
            deps=runtime_deps,
            agent_config=agent_config,
            workspace_root=workspace_root,
            agents_policy=agents_policy,
        )
        agent_session = AgentSession(
            session_id=f"{request.session_id_prefix}-{uuid4().hex[:8]}",
            run_id=str(uuid4()),
            drain=request.drain,
            capabilities=set(session_plan.capabilities),
            model_identity=session_plan.model_identity,
            stored_capability_profile=session_plan.capability_profile,
        )
        workspace = runtime_deps.workspace_factory(workspace_root)
        bridge = runtime_deps.start_mcp_server(
            agent_session,
            workspace,
            McpServerExtras(phase=request.drain, extra_env=session_plan.server_env),
        )
        try:
            system_prompt_file = None
            if request.system_prompt_name is not None:
                system_prompt_file = runtime_deps.materialize_system_prompt(
                    workspace_root,
                    request.system_prompt_name,
                    request.default_current_prompt,
                )
            return cls(
                config=config,
                workspace_root=workspace_root,
                agent_config=agent_config,
                request=request,
                bridge=bridge,
                agent_session=agent_session,
                system_prompt_file=system_prompt_file,
                deps=runtime_deps,
            )
        except Exception:
            runtime_deps.shutdown_bridge(bridge)
            raise

    def __enter__(self) -> ManagedAgentSessionRuntime:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.close()

    def close(self) -> None:
        """Shut down the MCP bridge owned by this session."""
        self._deps.shutdown_bridge(self._bridge)

    def invoke_prompt_file(
        self,
        prompt_file: str | Path,
        *,
        session_id: str | None = None,
        session_id_sink: Callable[[str], None] | None = None,
        required_artifact: RequiredArtifact | None = None,
        waiting_listener: WaitingStatusListener | None = None,
        permission_prompt_listener: Callable[[str], None] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> Iterable[str]:
        """Drive one host-owned agent turn and stream the agent's output.

        ``invoke_prompt_file`` resolves the per-turn ``InvokeOptions`` for
        the configured agent, injects the MCP endpoint / run-id / agent
        scope environment variables into the agent subprocess, and yields
        the agent's output line-by-line (logging through
        :func:`ralph.agents.invoke.invoke_agent`). The MCP bridge started
        by :meth:`open` is reachable as the ``MCP_ENDPOINT`` env value, so
        tools that call back into Ralph resolve back to the same bridge
        for the lifetime of the turn.

        Args:
            prompt_file: Path to the prompt file the agent will be asked to
                read. Resolved relative to the runtime's ``workspace_root``
                by the underlying invocation mechanism; absolute paths are
                honored as-is.

        Keyword Args:
            session_id: Optional explicit session id forwarded to the agent.
                When ``None`` the agent runtime generates one; the actual id
                is reported through ``session_id_sink``.
            session_id_sink: Optional callback invoked as soon as the agent
                makes its session id observable (i.e. after the first
                handshake). Receives the resolved session id so the host can
                store it for resumption, logging, or checkpoint writes.
            required_artifact: Optional
                :class:`ralph.phases.required_artifacts.RequiredArtifact`
                declaration used by the agent runtime to gate completion;
                the turn fails fast if no matching artifact is produced.
                Most host loops leave this ``None``.
            waiting_listener: Optional callback invoked when the agent
                reports it is waiting on a tool call. Used by progress UIs
                to surface the wait state without depending on stdout
                parsing.
            permission_prompt_listener: Optional callback invoked when the
                agent prompts for permission (e.g. before an action that
                requires operator approval). Implementations should return
                the agent's answer or raise to abort the turn.
            extra_env: Optional additional environment variables merged into
                the agent subprocess environment, **excluding** the three
                reserved names ``MCP_ENDPOINT``, ``MCP_RUN_ID``, and
                ``AGENT_LABEL_SCOPE`` (these are owned by the runtime and
                always set by ``open``).

        Returns:
            Iterable[str]: A lazy iterator over the agent's streamed output
            lines. The iterator is wrapped by
            :func:`ralph.agents.invoke._direct_mcp_recovery.iter_with_direct_mcp_recovery`,
            which transparently retries on direct-MCP failures up to
            ``config.general.max_same_agent_retries`` attempts. Retry events
            are emitted through :func:`loguru.logger.warning`.

        Raises:
            Exception: Propagated from the underlying agent invocation or
                from the recovery iterator once retries are exhausted. The
                MCP bridge started by :meth:`open` is **not** shut down on
                failure; callers are expected to invoke :meth:`close` (or use
                the runtime as a context manager) regardless of outcome.

        Side Effects:
            - Launches the configured agent CLI as a subprocess.
            - Injects ``MCP_ENDPOINT``, ``MCP_RUN_ID``, and
              ``AGENT_LABEL_SCOPE`` into the subprocess environment so the
              agent can reach the MCP bridge owned by this runtime.
            - May invoke ``session_id_sink`` and the listener callbacks as
              the turn progresses.
            - On retryable failure, may re-launch the agent subprocess and
              may reset the bridge's tool registry (when one is exposed via
              ``bridge.reset_tool_registry``).

        Example:
            >>> with ManagedAgentSessionRuntime.open(
            ...     config=config,
            ...     workspace_root=repo_root,
            ...     agent_config=agent_config,
            ...     request=ManagedAgentSessionRequest(
            ...         session_id_prefix="plan", drain="planning"
            ...     ),
            ... ) as runtime:
            ...     for line in runtime.invoke_prompt_file("your-prompt-file.md"):
            ...         print(line)
        """
        runtime_env = {
            str(MCP_ENDPOINT_ENV): self._bridge.agent_endpoint_uri(),
            str(MCP_RUN_ID_ENV): self._agent_session.run_id,
            str(AGENT_LABEL_SCOPE_ENV): self._agent_session.run_id,
        }
        if extra_env is not None:
            reserved_env = {
                str(MCP_ENDPOINT_ENV),
                str(MCP_RUN_ID_ENV),
                str(AGENT_LABEL_SCOPE_ENV),
            }
            runtime_env.update(
                {key: value for key, value in extra_env.items() if key not in reserved_env}
            )
        options = build_invoke_options_from_config(
            self._config.general,
            InvokeRuntimeOptions(
                verbose=False,
                show_progress=False,
                workspace_path=self._workspace_root,
                extra_env=runtime_env,
                pure=self._agent_config.transport == AgentTransport.OPENCODE,
                session_id=session_id,
                system_prompt_file=self._system_prompt_file,
                waiting_listener=waiting_listener,
                permission_prompt_listener=permission_prompt_listener,
                required_artifact=required_artifact,
            ),
        )
        max_retries = default_direct_mcp_retry_limit(self._config.general.max_same_agent_retries)
        reset_tool_registry = _reset_tool_registry_callback(self._bridge)
        base_session_id = session_id

        def _invoke_with_retry_session(retry_session_id: str | None) -> Iterable[str]:
            return self._deps.invoke_agent(
                self._agent_config,
                str(prompt_file),
                _with_session_id(options, retry_session_id or base_session_id),
            )

        return iter_with_direct_mcp_recovery(
            _invoke_with_retry_session,
            max_retries=max_retries,
            reset_tool_registry=reset_tool_registry,
            on_session_observed=session_id_sink,
            on_retry_failure=lambda lines: logger.warning(
                "Retrying managed agent session after retryable failure: {}",
                summarize_retry_failure_evidence(lines),
            ),
        )


def _with_session_id(options: InvokeOptions, session_id: str | None) -> InvokeOptions:
    return replace(options, session_id=session_id)


def _reset_tool_registry_callback(bridge: object) -> Callable[[], object] | None:
    reset_tool_registry_obj: object = getattr(bridge, "reset_tool_registry", None)
    if not callable(reset_tool_registry_obj):
        return None
    return cast("Callable[[], object]", reset_tool_registry_obj)


def _resolve_session_plan(
    *,
    request: ManagedAgentSessionRequest,
    deps: ManagedAgentSessionDeps,
    agent_config: AgentConfig,
    workspace_root: Path,
    agents_policy: AgentsPolicy | None,
) -> SessionMcpPlan:
    if request.session_mcp_plan is not None:
        return request.session_mcp_plan
    if request.capabilities is not None:
        return SessionMcpPlan(
            capabilities=request.capabilities,
            server_env=request.server_env,
        )
    return deps.build_session_mcp_plan(
        agent_config.transport,
        request.drain,
        workspace_root,
        agents_policy,
        SessionModelOpts(model_flag=agent_config.model_flag),
        None,
    )


__all__ = ["ManagedAgentSessionRuntime"]
