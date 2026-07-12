"""Run-pipeline integration helpers for the project-policy-readiness preflight.

The preflight lives in :mod:`ralph.project_policy`; the run-pipeline CLI
in :mod:`ralph.cli.commands.run` only needs the orchestrator entry point
plus the dependency-injection helpers (workspace factory, emit factory,
remediation-agent factory). Moving the helpers here keeps the CLI module
under the 1000-line repository cap without dragging the orchestrator's
helpers into the public package surface.

Every helper here is independent and small; the orchestrator entry point
:func:`run_project_policy_readiness` stitches them together so the CLI
call site reads as one line.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.chain import ChainManager, DrainNotBoundError
from ralph.display.parallel_display import resolve_active_display
from ralph.language_detector import get_project_stack
from ralph.pipeline import effect_executor as _effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.project_policy import _auto_commit as policy_auto_commit
from ralph.project_policy import remediation as policy_remediation
from ralph.project_policy.preflight import run_policy_readiness_preflight
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.cli.commands._load_result import _LoadResult
    from ralph.display.context import DisplayContext
    from ralph.language_detector.models import ProjectStack
    from ralph.pipeline.factory import PipelineDeps
    from ralph.project_policy.models import ReadinessResult
    from ralph.project_policy.remediation import _InvokeRemediationAgent
    from ralph.workspace.protocol import Workspace
    from ralph.workspace.scope import WorkspaceScope


#: Process-level success exit code.
_EXIT_SUCCESS: int = 0

#: Process-level preflight-blocked exit code. Kept in sync with the
#: constant of the same name in :mod:`ralph.cli.commands.run`.
_EXIT_PREFLIGHT: int = 2


EmitFn = Callable[[str], None]


def _resolve_remediation_chain_agents(load_result: _LoadResult) -> list[str]:
    """Return the fallback agents of the chain bound to the remediation drain.

    Resolution reuses :class:`ralph.agents.chain.ChainManager` — the exact
    strict drain->chain lookup the pipeline uses — so the out-of-graph
    remediation path cannot drift from pipeline routing. The loader may
    alias the ``policy_remediation`` drain to the user's review chain.
    Returns an empty list when the bundle is missing or the drain does not
    resolve to a non-empty chain.
    """
    bundle = load_result.policy_bundle
    if bundle is None:
        return []
    try:
        chain = ChainManager(bundle.agents).chain_for_drain("policy_remediation")
    except (DrainNotBoundError, ValueError):
        return []
    return [agent.strip() for agent in chain.agents if agent.strip()]


def _build_pipeline_deps_for_remediation(
    load_result: _LoadResult,
    display_context: DisplayContext,
) -> PipelineDeps | None:
    """Build ``PipelineDeps`` for the synchronous remediation driver.

    Returns ``None`` when the bundle is missing or factory construction
    fails (defensive: a missing deps block simply prevents the production
    agent invocation from running, but tests inject a fake and pass).
    """
    if load_result.policy_bundle is None:
        return None
    try:
        return DefaultPipelineFactory().build(
            load_result.config,
            display_context,
            model_identity=None,
            policy_bundle=load_result.policy_bundle,
            pro_hooks=None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not build pipeline deps for remediation: {}", exc)
        return None


def _make_production_invoke_remediation_agent(
    load_result: _LoadResult,
    pipeline_deps: PipelineDeps | None,
    workspace_scope: WorkspaceScope,
    chain_agents: list[str],
    display_context: DisplayContext | None,
) -> _InvokeRemediationAgent:
    """Build the production ``invoke_remediation_agent`` closure.

    One call walks the resolved fallback chain in order — the same
    semantics a pipeline drain gets — invoking each agent through
    :func:`execute_agent_effect` until one succeeds. A chain that ran and
    failed returns ``False`` (the driver retries within its budget); a
    launch crash raises :class:`RemediationInvocationError` so the driver
    aborts instead of spinning through the budget.
    """

    def invoke_remediation_agent(prompt_path: str) -> bool:
        if pipeline_deps is None or load_result.policy_bundle is None:
            return False
        for agent_name in chain_agents:
            effect = InvokeAgentEffect(
                agent_name=agent_name,
                phase="policy_remediation",
                prompt_file=prompt_path,
                drain="policy_remediation",
                chain_name="policy_remediation",
            )
            try:
                event = _effect_executor_module.execute_agent_effect(
                    effect,
                    load_result.config,
                    pipeline_deps,
                    workspace_scope,
                    run_id=load_result.run_id,
                    policy_bundle=load_result.policy_bundle,
                    display_context=display_context,
                )
            except Exception as exc:
                logger.warning("Remediation agent invocation failed: {}", exc)
                raise policy_remediation.RemediationInvocationError(str(exc)) from exc
            if event == PipelineEvent.AGENT_SUCCESS:
                return True
        return False

    typed: _InvokeRemediationAgent = invoke_remediation_agent
    return typed


def _resolve_max_attempts(load_result: _LoadResult) -> int:
    """Return the remediation attempt budget.

    Always the remediation driver's own small budget. The global recovery
    ``cycle_cap`` (default 200) governs pipeline recovery cycles and must
    NOT leak in here: 200 synchronous (agent, revalidate) rounds at startup
    is a display flood, not a remediation strategy.
    """
    del load_result
    return policy_remediation.DEFAULT_MAX_ATTEMPTS


def _build_workspace(
    load_result: _LoadResult,
    workspace_factory: Callable[[], Workspace] | None,
) -> Workspace:
    """Return the workspace, using the injected factory when available."""
    if workspace_factory is not None:
        return workspace_factory()
    scope = load_result.workspace_scope
    if scope is None:
        msg = "_build_workspace called with a missing workspace_scope"
        raise RuntimeError(msg)
    return FsWorkspace(scope.root, allowed_roots=scope.allowed_roots)


def _auto_commit_policy_changes(workspace_scope: WorkspaceScope) -> None:
    """Best-effort auto-commit of the policy surfaces after READY.

    Mirrors the wt-025 skill auto-commit so the next run's development
    agent never sees readiness drift in its working tree. Failures are
    logged and swallowed — a broken git state must not block the run.
    """
    try:
        from ralph.git.operations import create_commit  # noqa: PLC0415

        sha = policy_auto_commit.commit_policy_updates(workspace_scope.root, create_commit)
        if sha is not None:
            logger.debug("project-policy auto-commit created: {}", sha)
    except Exception as exc:
        logger.debug("project-policy auto-commit failed (non-fatal): {}", exc)


def _build_emit(
    display_context: DisplayContext,
    emit_factory: Callable[[str], None] | None,
) -> Callable[[str], None]:
    """Return the display emit, using the injected callback when available."""
    if emit_factory is not None:
        return emit_factory

    def emit(message: str) -> None:
        display = resolve_active_display(None, display_context)
        display.emit_info_panel(
            title="Project-Policy Readiness",
            content=message,
        )

    return emit


def _dispatch_preflight_result(
    *,
    load_result: _LoadResult,
    display_context: DisplayContext,
    result: ReadinessResult,
    workspace_scope: WorkspaceScope,
    workspace: Workspace,
    stack: ProjectStack,
    emit: Callable[[str], None],
    invoke_remediation_agent_factory: Callable[[Workspace], Callable[[str], bool]]
    | None,
) -> int:
    """Map a :class:`ReadinessResult` to a CLI exit code.

    Extracted from :func:`run_project_policy_readiness` so the
    orchestrator stays under PLR0911 while the dispatch logic keeps its
    explicit state-machine branches.
    """
    if not result.requires_remediation() and not result.is_blocked():
        return _EXIT_PREFLIGHT

    chain_agents = _resolve_remediation_chain_agents(load_result)
    if not chain_agents and invoke_remediation_agent_factory is None:
        logger.warning(
            "policy_remediation chain has no usable configured agent; "
            "blocking the run."
        )
        emit(
            "Project-policy-readiness: BLOCKED \u2014 policy_remediation chain "
            "has no configured agent."
        )
        return _EXIT_PREFLIGHT

    pipeline_deps = _build_pipeline_deps_for_remediation(load_result, display_context)
    if invoke_remediation_agent_factory is not None:
        invoke_remediation_agent: _InvokeRemediationAgent = cast(
            "_InvokeRemediationAgent",
            invoke_remediation_agent_factory(workspace),
        )
    else:
        invoke_remediation_agent = _make_production_invoke_remediation_agent(
            load_result,
            pipeline_deps,
            workspace_scope,
            chain_agents,
            display_context,
        )

    max_attempts = _resolve_max_attempts(load_result)
    final = policy_remediation.remediate(
        workspace,
        stack,
        result.findings,
        invoke_remediation_agent=invoke_remediation_agent,
        max_attempts=max_attempts,
        emit=emit,
    )
    if final.is_ready():
        _auto_commit_policy_changes(workspace_scope)
        return _EXIT_SUCCESS
    report_lines = ["Project-policy-readiness: BLOCKED"]
    report_lines.extend(final.report_lines)
    emit("\n".join(report_lines))
    return _EXIT_PREFLIGHT


def run_project_policy_readiness(
    *,
    load_result: _LoadResult,
    display_context: DisplayContext,
    workspace_factory: Callable[[], Workspace] | None = None,
    emit_factory: Callable[[str], None] | None = None,
    invoke_remediation_agent_factory: Callable[[Workspace], Callable[[str], bool]]
    | None = None,
) -> int:
    """Run the project-policy-readiness preflight at run_pipeline startup.

    Steps:

    #. Build the workspace + project stack via the injected seams.
    #. Call :func:`ralph.project_policy.run_policy_readiness_preflight`.
    #. Map the result status to a CLI exit code: ``READY`` and ``SKIPPED``
       continue. ``REMEDIATION_REQUIRED`` triggers an in-process bounded
       remediation loop. ``BLOCKED`` returns the recoverable
       ``_EXIT_PREFLIGHT`` exit.

    Tests can inject ``workspace_factory``, ``emit_factory``, and
    ``invoke_remediation_agent_factory`` to exercise the preflight without
    real filesystem I/O or real agent invocation.
    """
    workspace_scope = load_result.workspace_scope
    if workspace_scope is None:
        return _EXIT_SUCCESS

    emit = _build_emit(display_context, emit_factory)
    workspace = _build_workspace(load_result, workspace_factory)
    stack = get_project_stack(workspace)
    result = run_policy_readiness_preflight(workspace, stack, emit=emit)

    if result.is_skipped():
        emit("project-policy-readiness: skipped (opt-out marker present)")
        return _EXIT_SUCCESS

    if result.is_ready():
        emit(
            f"project-policy-readiness: ready "
            f"({len(result.changed_files)} files updated)"
        )
        _auto_commit_policy_changes(workspace_scope)
        return _EXIT_SUCCESS

    return _dispatch_preflight_result(
        load_result=load_result,
        display_context=display_context,
        result=result,
        workspace_scope=workspace_scope,
        workspace=workspace,
        stack=stack,
        emit=emit,
        invoke_remediation_agent_factory=invoke_remediation_agent_factory,
    )


__all__ = [
    "_EXIT_PREFLIGHT",
    "_EXIT_SUCCESS",
    "EmitFn",
    "run_project_policy_readiness",
]
