"""Injectable dependency bundle for Ralph-managed standalone agent sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.agents.invoke import invoke_agent
from ralph.mcp.server.lifecycle import SessionBridgeLike, start_mcp_server
from ralph.mcp.session_plan import SessionMcpPlan, build_session_mcp_plan
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server.lifecycle import McpServerExtras
    from ralph.mcp.session_plan import SessionModelOpts
    from ralph.policy.models import AgentsPolicy
    from ralph.workspace.protocol import Workspace

    BuildSessionMcpPlanFn = Callable[
        [
            AgentTransport | None,
            str,
            Path | None,
            AgentsPolicy | None,
            SessionModelOpts | None,
            str | None,
        ],
        SessionMcpPlan,
    ]
    StartMcpServerFn = Callable[
        [AgentSession, Workspace, McpServerExtras | None],
        SessionBridgeLike,
    ]
    InvokeAgentFn = Callable[[AgentConfig, str, InvokeOptions | None], Iterable[str]]
    MaterializeSystemPromptFn = Callable[[Path, str, str | None], str]
    WorkspaceFactoryFn = Callable[[Path], Workspace]
    ShutdownBridgeFn = Callable[[SessionBridgeLike], None]


def _build_session_mcp_plan(
    transport: AgentTransport | None,
    drain: str,
    workspace_path: Path | None,
    agents_policy: AgentsPolicy | None,
    model_opts: SessionModelOpts | None,
    model_flag: str | None,
) -> SessionMcpPlan:
    return build_session_mcp_plan(
        transport=transport,
        drain=drain,
        workspace_path=workspace_path,
        agents_policy=agents_policy,
        model_opts=model_opts,
        model_flag=model_flag,
    )


def _start_mcp_server(
    session: AgentSession,
    workspace: Workspace,
    extras: McpServerExtras | None,
) -> SessionBridgeLike:
    return start_mcp_server(session, workspace, extras=extras)


def _invoke_agent(
    config: AgentConfig,
    prompt_file: str,
    options: InvokeOptions | None,
) -> Iterable[str]:
    return invoke_agent(config, prompt_file, options=options)


def _materialize_system_prompt(
    workspace_root: Path,
    name: str,
    default_current_prompt: str | None,
    worker_namespace: Path | None = None,
) -> str:
    return materialize_system_prompt(
        workspace_root=workspace_root,
        name=name,
        default_current_prompt=default_current_prompt,
        worker_namespace=worker_namespace,
    )


def _workspace_factory(root: Path) -> Workspace:
    return FsWorkspace(root)


def _shutdown_bridge(bridge: SessionBridgeLike) -> None:
    bridge.shutdown()


@dataclass(frozen=True)
class ManagedAgentSessionDeps:
    """Injectable boundaries for black-box testing of the managed session runtime."""

    build_session_mcp_plan: BuildSessionMcpPlanFn = _build_session_mcp_plan
    start_mcp_server: StartMcpServerFn = _start_mcp_server
    invoke_agent: InvokeAgentFn = _invoke_agent
    materialize_system_prompt: MaterializeSystemPromptFn = _materialize_system_prompt
    workspace_factory: WorkspaceFactoryFn = _workspace_factory
    shutdown_bridge: ShutdownBridgeFn = _shutdown_bridge


__all__ = ["ManagedAgentSessionDeps"]
