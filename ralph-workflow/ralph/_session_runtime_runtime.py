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
from ralph.agents.timeout_clock import SystemClock
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    resolve_capability_profile,
)
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.lifecycle import McpServerExtras, SessionBridgeLike
from ralph.mcp.session_plan import SessionMcpPlan, SessionModelOpts, resolve_model_identity
from ralph.pipeline.agent_entry_point import (
    AgentEntryPoint,
    EntryPointRunner,
    register_entry_point,
    registered_entry_points,
)
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    run_agent_attempt,
)
from ralph.pipeline.attempt_runner_deps import AttemptInvoke, AttemptRunnerDeps
from ralph.pipeline.session_bridge import scoped_reset_tool_registry_callback

from ._session_runtime_deps import ManagedAgentSessionDeps

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from ralph.agents.idle_watchdog import WaitingStatusListener
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy
    from ralph.recovery.canary_probe import ProbeOutcome
    from ralph.recovery.controller import RecoveryController

    from ._session_runtime_request import ManagedAgentSessionRequest


def _run_session_runtime_attempt(
    *_args: object,
    effect: object | None = None,
    invocation: object | None = None,
    config: UnifiedConfig,
    controller: RecoveryController,
    deps: AttemptRunnerDeps,
    state: PipelineState | None = None,
    **execute_kwargs: object,
) -> AttemptResult:
    return run_agent_attempt(
        AgentEntryPoint.SESSION_RUNTIME,
        effect=effect,
        invocation=invocation,
        config=config,
        controller=controller,
        deps=deps,
        state=state,
        **execute_kwargs,
    )


if AgentEntryPoint.SESSION_RUNTIME not in registered_entry_points():
    register_entry_point(
        AgentEntryPoint.SESSION_RUNTIME,
        cast("EntryPointRunner", _run_session_runtime_attempt),
    )


class ManagedAgentSessionRuntime:
    def __init__(
        self,
        *,
        config: UnifiedConfig,
        workspace_root: Path,
        agent_config: AgentConfig,
        request: ManagedAgentSessionRequest,
        bridge: SessionBridgeLike,
        agent_session: AgentSession,
        master_prompt_file: str | None,
        deps: ManagedAgentSessionDeps,
        attempt_deps: AttemptRunnerDeps,
        controller: RecoveryController,
    ) -> None:
        self._config = config
        self._workspace_root = workspace_root
        self._agent_config = agent_config
        self._agent_name = _resolve_agent_name(config, agent_config)
        self._request = request
        self._bridge = bridge
        self._agent_session = agent_session
        self._master_prompt_file = master_prompt_file
        self._deps = deps
        self._attempt_deps = attempt_deps
        self._controller = controller

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
        runtime_deps = deps or ManagedAgentSessionDeps()
        session_plan = _resolve_session_plan(
            request=request,
            deps=runtime_deps,
            agent_config=agent_config,
            workspace_root=workspace_root,
            agents_policy=agents_policy,
        )
        session_id = f"{request.session_id_prefix}-{uuid4().hex[:8]}"
        agent_session = AgentSession(
            session_id=session_id,
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
            master_prompt_file = None
            if request.master_prompt_name is not None:
                master_prompt_file = runtime_deps.materialize_master_prompt(
                    workspace_root,
                    request.master_prompt_name,
                    request.default_product_criteria,
                    None,
                    agent_config.transport,
                )
            attempt_deps = AttemptRunnerDeps(
                clock=SystemClock(),
                invoke=_session_invoke(runtime_deps),
                probe_runner=_skip_probe,
                workspace_probe=None,
                connectivity=None,
            )
            controller = build_entry_point_recovery_controller(
                AgentEntryPoint.SESSION_RUNTIME,
                config=config,
                policy_bundle=None,
                deps=attempt_deps,
            )
            return cls(
                config=config,
                workspace_root=workspace_root,
                agent_config=agent_config,
                request=request,
                bridge=bridge,
                agent_session=agent_session,
                master_prompt_file=master_prompt_file,
                deps=runtime_deps,
                attempt_deps=attempt_deps,
                controller=controller,
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
                session_id=session_id,
                master_prompt_file=self._master_prompt_file,
                waiting_listener=waiting_listener,
                permission_prompt_listener=permission_prompt_listener,
                required_artifact=required_artifact,
            ),
        )
        max_retries = default_direct_mcp_retry_limit(self._config.general.max_same_agent_retries)
        reset_tool_registry = _reset_tool_registry_callback(
            self._bridge,
            session_id or "managed-session",
        )
        base_session_id = session_id

        def _invoke_with_retry_session(retry_session_id: str | None) -> Iterable[str]:
            result = run_agent_attempt(
                AgentEntryPoint.SESSION_RUNTIME,
                invocation=self._agent_config,
                config=self._config,
                controller=self._controller,
                deps=self._attempt_deps,
                phase=self._request.drain,
                agent=self._agent_name,
                prompt_file=str(prompt_file),
                options=_with_session_id(options, retry_session_id or base_session_id),
            )
            return cast("Iterable[str]", result.event)

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


def _resolve_agent_name(config: UnifiedConfig, agent_config: AgentConfig) -> str | None:
    for name, candidate in config.agents.items():
        if candidate == agent_config:
            return name
    return None


def _skip_probe(phase: str, agent: str) -> ProbeOutcome | None:
    del phase, agent
    return None


def _session_invoke(deps: ManagedAgentSessionDeps) -> AttemptInvoke:
    def _invoke(target: object, *args: object, **kwargs: object) -> object:
        del args
        return deps.invoke_agent(
            cast("AgentConfig", target),
            str(kwargs["prompt_file"]),
            cast("InvokeOptions | None", kwargs.get("options")),
        )

    return _invoke


def _reset_tool_registry_callback(
    bridge: object,
    scope_key: str,
) -> Callable[[], object] | None:
    return scoped_reset_tool_registry_callback(bridge, scope_key)


def _transport_tagged_identity(transport: AgentTransport | None) -> MultimodalModelIdentity:
    if transport is None:
        return UNKNOWN_IDENTITY
    return MultimodalModelIdentity(
        provider=UNKNOWN_IDENTITY.provider,
        model_id=None,
        transport=transport.value,
    )


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
        identity = (
            resolve_model_identity(agent_config.transport, agent_config.model_flag)
            if agent_config.model_flag is not None
            else _transport_tagged_identity(agent_config.transport)
        )
        return SessionMcpPlan(
            capabilities=request.capabilities,
            server_env=request.server_env,
            model_identity=identity,
            capability_profile=resolve_capability_profile(identity),
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
