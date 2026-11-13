from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import ralph.agents.invoke as agents_invoke
from ralph.mcp.server.lifecycle import SessionBridgeLike, start_mcp_server
from ralph.mcp.session_plan import SessionMcpPlan, build_session_mcp_plan
from ralph.prompts.master_prompt import materialize_master_prompt
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
    MaterializeMasterPromptFn = Callable[
        [Path, str, str | None, Path | None, AgentTransport | None], str
    ]
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
    return agents_invoke.invoke_agent(config, prompt_file, options=options)


def _materialize_master_prompt(
    workspace_root: Path,
    name: str,
    default_product_criteria: str | None,
    worker_namespace: Path | None = None,
    transport: AgentTransport | None = None,
) -> str:
    return materialize_master_prompt(
        workspace_root=workspace_root,
        name=name,
        default_product_criteria=default_product_criteria,
        worker_namespace=worker_namespace,
        transport=transport,
    )


def _workspace_factory(root: Path) -> Workspace:
    return FsWorkspace(root)


def _shutdown_bridge(bridge: SessionBridgeLike) -> None:
    bridge.shutdown()


@dataclass(frozen=True)
class ManagedAgentSessionDeps:
    build_session_mcp_plan: BuildSessionMcpPlanFn = _build_session_mcp_plan
    start_mcp_server: StartMcpServerFn = _start_mcp_server
    invoke_agent: InvokeAgentFn = _invoke_agent
    materialize_master_prompt: MaterializeMasterPromptFn = _materialize_master_prompt
    workspace_factory: WorkspaceFactoryFn = _workspace_factory
    shutdown_bridge: ShutdownBridgeFn = _shutdown_bridge


__all__ = ["ManagedAgentSessionDeps"]
