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
    """Injectable dependency bundle for the managed session runtime.

    :class:`ManagedAgentSessionDeps` is the single seam that lets tests
    replace the default production collaborators used by
    :class:`ralph.session_runtime.ManagedAgentSessionRuntime` with
    fakes or stubs. The production default wraps the canonical
    implementations in :mod:`ralph.mcp.session_plan`,
    :mod:`ralph.mcp.server.lifecycle`, :mod:`ralph.agents.invoke`,
    :mod:`ralph.prompts.system_prompt`, and :mod:`ralph.workspace.fs`.

    All fields are public callables; tests may overwrite any subset
    while leaving the rest at the production defaults. Because the
    dataclass is ``frozen=True`` the bundle itself cannot be mutated
    after construction, so a runtime that captures a deps value is
    guaranteed to use the same callables throughout its lifetime.

    Attributes:
        build_session_mcp_plan: Resolve the per-session MCP plan for a
            given ``(transport, drain, workspace_path, agents_policy,
            model_opts, model_flag)`` tuple. Side effect: none. The
            production implementation reads policy and writes nothing.
        start_mcp_server: Launch the MCP server subprocess for a given
            session and workspace, returning the bridge handle the
            runtime uses to talk to that server. Side effects: spawns
            a subprocess and registers the bridge handle for
            shutdown; the bridge is also exposed for
            ``shutdown_bridge``.
        invoke_agent: Run an agent CLI against a prompt file with the
            given options. Returns an iterable of stdout chunks.
            Side effects: spawns a subprocess, injects the resolved
            environment into the agent, and yields streamed output.
        materialize_system_prompt: Resolve the prompt inputs for a
            named system prompt and, when the named prompt is supplied,
            write the materialized system-prompt file the agent will
            consume. Side effects: reads prompt inputs from
            ``workspace_root`` (and the engine-owned current-prompt
            mirror under the ``.agent`` directory), and may write the
            materialized system-prompt file under ``workspace_root`` at
            ``.agent/tmp/<name>_system_prompt.md`` (or under the worker
            namespace when one is provided), plus the synchronized
            current-prompt mirror and any prompt-history snapshot.
            Returns the filesystem path of the written system-prompt
            file as a string.
        workspace_factory: Build a :class:`ralph.workspace.protocol.Workspace`
            rooted at the given path. Side effects: instantiates the
            workspace implementation; the production default returns
            :class:`ralph.workspace.fs.FsWorkspace`.
        shutdown_bridge: Terminate a running bridge handle, releasing
            any subprocess it owns. Side effects: stops the MCP server
            subprocess and frees the bridge handle. The runtime calls
            this from its cleanup path after :class:`Exception` is
            observed, so tests can verify cleanup behavior.

    Example:
        Replace just one field for a focused unit test::

            deps = ManagedAgentSessionDeps(
                start_mcp_server=lambda session, workspace, extras: fake_bridge,
            )
            runtime = ManagedAgentSessionRuntime.open(
                config=cfg,
                request=req,
                deps=deps,
            )
    """

    build_session_mcp_plan: BuildSessionMcpPlanFn = _build_session_mcp_plan
    start_mcp_server: StartMcpServerFn = _start_mcp_server
    invoke_agent: InvokeAgentFn = _invoke_agent
    materialize_system_prompt: MaterializeSystemPromptFn = _materialize_system_prompt
    workspace_factory: WorkspaceFactoryFn = _workspace_factory
    shutdown_bridge: ShutdownBridgeFn = _shutdown_bridge


__all__ = ["ManagedAgentSessionDeps"]
