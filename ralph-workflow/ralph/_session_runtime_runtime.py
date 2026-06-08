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
    """Host-owned context for running prompt-like mini workflows through Ralph."""

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
        """Create a managed Ralph session that another host loop can drive."""
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
        """Invoke the configured agent for one host-owned turn."""
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
