from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.chain import ChainManager, DrainNotBoundError
from ralph.agents.timeout_clock import SystemClock
from ralph.display.parallel_display import resolve_active_display
from ralph.git.scoped_auto_commit import list_dirty_paths
from ralph.language_detector import get_project_stack
from ralph.pipeline._runner_session import (
    pop_last_captured_session_id,
    set_last_captured_session_id,
)
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.agent_retry_intent import (
    pop_last_captured_retry_intent,
    set_last_captured_retry_intent,
)
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    register_entry_point,
    registered_entry_points,
    run_agent_attempt,
)
from ralph.pipeline.attempt_runner_deps import (
    AttemptInvoke,
    AttemptRunnerDeps,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.project_policy import _prompt_ui
from ralph.project_policy import _schema_upgrade as policy_schema_upgrade
from ralph.project_policy import agents_md as policy_agents_md
from ralph.project_policy import evidence as policy_evidence
from ralph.project_policy import finalization as policy_finalization
from ralph.project_policy import markers as policy_markers
from ralph.project_policy import models as policy_models
from ralph.project_policy import remediation as policy_remediation
from ralph.project_policy.pipeline_driver import run_policy_pipeline
from ralph.project_policy.pipeline_graph import (
    DEFAULT_ANALYSIS_CAP,
    DEFAULT_MAX_REMEDIATION_ATTEMPTS,
    PHASE_ANALYSIS,
    PHASE_REMEDIATION,
)
from ralph.project_policy.policy_mode import PolicyMode
from ralph.project_policy.preflight import run_policy_readiness_preflight
from ralph.project_policy.reset import reset_policy_state
from ralph.project_policy.status_bar import (
    push_readiness_assessment_status_bar,
    push_remediation_status_bar,
    remediation_status_bar_session,
)
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.classifier import FailureContext
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.cli.commands._load_result import _LoadResult
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.language_detector.models import ProjectStack
    from ralph.pipeline.agent_retry_intent import AgentRetryIntent
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.factory import PipelineDeps
    from ralph.project_policy.analysis import InvokePolicyAgent
    from ralph.project_policy.models import ReadinessResult
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.protocol import Workspace
    from ralph.workspace.scope import WorkspaceScope


_EXIT_SUCCESS: int = 0

_EXIT_PREFLIGHT: int = 2


EmitFn = Callable[[str], None]
WorkingTreeSnapshot = Callable[["WorkspaceScope"], frozenset[str]]
PolicyCommit = Callable[["WorkspaceScope", frozenset[str] | None], None]

_SETUP_ESTIMATE: str = "roughly 30 minutes"

_MAX_PROMPT_ROUNDS: int = 8

_CHOICE_ADOPT: str = "adopt"
_CHOICE_KEEP: str = "keep"
_CHOICE_EXPLAIN: str = "explain"
_CHOICE_UPGRADE: str = "upgrade"
_CHOICE_FREEZE: str = "freeze"

_INIT_PANEL: str = (
    "AGENTS.md already contains agent instructions, so this repository may "
    "already have a process of its own.\n\n"
    "Adopting Ralph Workflow's managed policy is a ONE-TIME setup that runs "
    "before your first task. Ralph Workflow seeds the core policy files under "
    f"{policy_markers.CANONICAL_DIR} — testing, type checking, linting, "
    "dependencies, verification, agents, clean code, documentation, security, "
    "architecture — plus any that apply to your stack, then an agent fills "
    "each one in against your actual codebase. Expect "
    f"{_SETUP_ESTIMATE} of agent work and a meaningful token spend.\n\n"
    "The result is plain markdown checked into your repository. It guides any "
    "AI coding assistant that reads AGENTS.md — Claude Code, Cursor, and the "
    "rest — not just Ralph Workflow runs.\n\n"
    "Your choices:\n\n"
    "  • Adopt Ralph Workflow's managed policy. Pick this if you are not an "
    "experienced software developer, or if you are not confident that your "
    "current process already covers testing, review, and verification. Your "
    "existing AGENTS.md content is preserved byte-for-byte — Ralph Workflow "
    "only appends a managed block.\n"
    "  • Keep my existing policy. Pick this if you already have a strong "
    "engineering process in place and know it holds. AGENTS.md is left "
    "untouched, an opt-out marker is written, and policy enforcement stays "
    "off for this repository."
)

_INIT_QUESTION: str = "What should Ralph Workflow do about this repository's policy?"

_INIT_CHOICES: tuple[_prompt_ui.PromptChoice, ...] = (
    _prompt_ui.PromptChoice(
        key=_CHOICE_ADOPT,
        title=f"Adopt Ralph Workflow's managed policy (one-time setup, {_SETUP_ESTIMATE})",
        description="Best choice if you are not an experienced developer.",
    ),
    _prompt_ui.PromptChoice(
        key=_CHOICE_KEEP,
        title="Keep my existing policy (no setup, no enforcement)",
        description="For teams whose engineering process is already strong.",
    ),
    _prompt_ui.PromptChoice(
        key=_CHOICE_EXPLAIN,
        title="What exactly does Ralph Workflow's policy contain?",
        description="Lists the files that would be created. Writes nothing.",
    ),
)

_SCHEMA_QUESTION: str = "What should Ralph Workflow do with these policy files?"


def _default_is_tty() -> bool:
    try:
        stdin_tty: bool = sys.stdin.isatty()
        stdout_tty: bool = sys.stdout.isatty()
    except Exception:
        return False
    return stdin_tty and stdout_tty


def _ask(
    select: _prompt_ui.SelectFn,
    emit: EmitFn,
    question: str,
    choices: Sequence[_prompt_ui.PromptChoice],
    default: str,
    *,
    fallback_notice: str,
) -> str:
    try:
        return select(question, choices, default)
    except Exception as exc:
        logger.debug("policy prompt failed (non-fatal): {}", exc)
        emit(fallback_notice)
        return default


def _policy_contents_detail(workspace: Workspace) -> str:
    core = "\n".join(
        f"  • {policy_markers.CANONICAL_DIR}{name}" for name in policy_markers.CORE_POLICY_FILES
    )
    stack = get_project_stack(workspace)
    requirements = policy_evidence.conditional_domain_requirements(workspace, stack)
    conditional = [
        f"  • {policy_markers.CANONICAL_DIR}{name}"
        for domain, name in policy_markers.CONDITIONAL_POLICY_FILES.items()
        if requirements[domain][0]
    ]
    detail = (
        "Ralph Workflow would create these core policy files, one per "
        f"quality domain every software project needs:\n\n{core}\n"
    )
    if conditional:
        detail += (
            "\nPlus these, because this project's code shows it needs "
            "them:\n\n" + "\n".join(conditional) + "\n"
        )
    return (
        detail + "\nEach file starts as a template and is filled in against "
        "your codebase: your real build, test, and lint commands, your "
        "frameworks, your exceptions. That authoring pass is the "
        f"{_SETUP_ESTIMATE} of one-time agent work."
    )


def _maybe_offer_inline_policy_skip(
    workspace: Workspace,
    emit: EmitFn,
    *,
    select: _prompt_ui.SelectFn | None,
    is_tty: Callable[[], bool] | None,
) -> None:
    tty_check = is_tty if is_tty is not None else _default_is_tty
    if not tty_check():
        return
    if not policy_agents_md.has_significant_unmanaged_content(workspace):
        return
    select_fn = select if select is not None else _prompt_ui.select
    emit(_INIT_PANEL)
    for _round in range(_MAX_PROMPT_ROUNDS):
        choice = _ask(
            select_fn,
            emit,
            _INIT_QUESTION,
            _INIT_CHOICES,
            _CHOICE_ADOPT,
            fallback_notice=(
                "Prompt unavailable — proceeding with the default: Ralph "
                "Workflow's managed policy block will be added to AGENTS.md."
            ),
        )
        if choice == _CHOICE_EXPLAIN:
            emit(_policy_contents_detail(workspace))
            continue
        if choice == _CHOICE_KEEP:
            policy_agents_md.write_opt_out(workspace)
            emit(
                "Keeping the existing AGENTS.md policy — wrote the opt-out "
                "marker; Ralph Workflow policy enforcement is disabled for "
                "this repository."
            )
        return


def _resolve_chain_agents(load_result: _LoadResult, drain: str) -> list[str]:
    bundle = load_result.policy_bundle
    if bundle is None:
        return []
    try:
        chain = ChainManager(bundle.agents).chain_for_drain(drain)
    except (DrainNotBoundError, ValueError):
        return []
    return [agent.strip() for agent in chain.agents if agent.strip()]


def _build_pipeline_deps_for_remediation(
    load_result: _LoadResult,
    display_context: DisplayContext,
) -> PipelineDeps | None:
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
    except Exception as exc:
        logger.warning("Could not build pipeline deps for remediation: {}", exc)
        return None


def _make_production_invoke_agent(
    load_result: _LoadResult,
    pipeline_deps: PipelineDeps | None,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
) -> InvokePolicyAgent:
    invoke_effect = _production_effect_invoke()
    attempt_deps = AttemptRunnerDeps(
        clock=SystemClock(),
        invoke=invoke_effect,
        probe_runner=None,
        workspace_probe=None,
        connectivity=None,
    )
    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.PROJECT_POLICY,
        config=load_result.config,
        policy_bundle=load_result.policy_bundle,
        deps=attempt_deps,
    )

    def invoke_agent(*, phase: str, prompt_path: str) -> bool:
        if pipeline_deps is None or load_result.policy_bundle is None:
            return False
        chain_agents = _resolve_chain_agents(load_result, phase)
        last_error: Exception | None = None
        remaining_agents = list(chain_agents)
        while remaining_agents:
            selection = controller.preferred_agent_index(phase, remaining_agents)
            if selection.agent is None:
                wait_ms = controller.earliest_available_wait_ms(phase, remaining_agents)
                if wait_ms > 0:
                    attempt_deps.clock.sleep(wait_ms / 1000.0)
                    continue
                break
            agent_name = selection.agent
            remaining_agents.remove(agent_name)
            effect = InvokeAgentEffect(
                agent_name=agent_name,
                phase=phase,
                prompt_file=prompt_path,
                drain=phase,
                chain_name=phase,
                requires_completion_evidence=(phase == PHASE_ANALYSIS),
            )
            with _capture_pipeline_state():
                try:
                    policy_config = load_result.config.model_copy(
                        update={
                            "general": load_result.config.general.model_copy(
                                update={"auto_integrate_enabled": False}
                            )
                        }
                    )
                    result = run_agent_attempt(
                        AgentEntryPoint.PROJECT_POLICY,
                        effect=effect,
                        config=policy_config,
                        controller=controller,
                        deps=attempt_deps,
                        state=PipelineState(phase=phase, rebase=RebaseState()),
                        pipeline_deps=pipeline_deps,
                        workspace_scope=workspace_scope,
                        display=display,
                        display_context=display_context,
                        run_id=load_result.run_id,
                        policy_bundle=load_result.policy_bundle,
                    )
                except Exception as exc:
                    classified = controller.classify_conflict_attempt(
                        exc,
                        agent=agent_name,
                        phase=phase,
                    )
                    controller.handle(
                        PipelineState(
                            phase=phase,
                            phase_chains={
                                phase: AgentChainState(
                                    agents=chain_agents,
                                    current_index=chain_agents.index(agent_name),
                                    retries=0,
                                )
                            },
                            rebase=RebaseState(),
                        ),
                        exc,
                        FailureContext(
                            phase=phase,
                            agent=agent_name,
                            classified_failure=classified,
                        ),
                    )
                    logger.warning(
                        "Policy {} agent {} could not be launched: {}", phase, agent_name, exc
                    )
                    last_error = exc
                    continue
            if result.outcome.kind is AttemptOutcomeKind.COMPLETED:
                return True
        if last_error is not None:
            raise policy_remediation.RemediationInvocationError(str(last_error)) from last_error
        return False

    typed: InvokePolicyAgent = invoke_agent
    return typed


_effect_executor_module = importlib.import_module("ralph.pipeline.effect_executor")


def _production_effect_invoke() -> AttemptInvoke:
    def invoke(
        target: InvokeAgentEffect | AgentConfig,
        *args: object,
        **kwargs: object,
    ) -> object:
        config = kwargs.pop("config")
        pipeline_deps = kwargs.pop("pipeline_deps")
        workspace_scope = kwargs.pop("workspace_scope")
        runner: AttemptInvoke = _effect_executor_module.execute_agent_effect
        result: object = runner(target, config, pipeline_deps, workspace_scope, *args, **kwargs)
        return result

    return invoke


def _run_project_policy_attempt(
    *args: object,
    effect: object | None = None,
    invocation: object | None = None,
    config: UnifiedConfig,
    controller: RecoveryController,
    deps: AttemptRunnerDeps,
    state: object | None = None,
    **execute_kwargs: object,
) -> AttemptResult:
    del args
    return run_agent_attempt(
        AgentEntryPoint.PROJECT_POLICY,
        effect=effect,
        invocation=invocation,
        config=config,
        controller=controller,
        deps=deps,
        state=state,
        **execute_kwargs,
    )


if AgentEntryPoint.PROJECT_POLICY not in registered_entry_points():
    register_entry_point(
        AgentEntryPoint.PROJECT_POLICY,
        _run_project_policy_attempt,
    )


def _snapshot_pipeline_capture() -> tuple[str | None, AgentRetryIntent]:
    return (
        pop_last_captured_session_id(),
        pop_last_captured_retry_intent(),
    )


def _restore_pipeline_capture(snapshot: tuple[str | None, AgentRetryIntent]) -> None:
    session_id, retry_intent = snapshot
    set_last_captured_retry_intent(retry_intent)
    set_last_captured_session_id(session_id)


@contextmanager
def _capture_pipeline_state() -> Iterator[None]:
    snapshot = _snapshot_pipeline_capture()
    try:
        yield
    finally:
        try:
            _restore_pipeline_capture(snapshot)
        except Exception as exc:
            logger.debug("capture-restore failed (non-fatal): {}", exc)


def _build_workspace(
    load_result: _LoadResult,
    workspace_factory: Callable[[], Workspace] | None,
) -> Workspace:
    if workspace_factory is not None:
        return workspace_factory()
    scope = load_result.workspace_scope
    if scope is None:
        msg = "_build_workspace called with a missing workspace_scope"
        raise RuntimeError(msg)
    return FsWorkspace(scope.root, allowed_roots=scope.allowed_roots)


def _snapshot_working_tree(workspace_scope: WorkspaceScope) -> frozenset[str]:
    try:
        return list_dirty_paths(workspace_scope.root)
    except Exception as exc:
        logger.debug("working-tree snapshot failed (non-fatal): {}", exc)
        return frozenset()


_commit_policy_changes = policy_finalization.commit_policy_changes
_finalize_ready_state = policy_finalization.finalize_ready_state
_auto_commit_policy_changes = policy_finalization.auto_commit_policy_changes


def _build_emit(
    display_context: DisplayContext,
    emit_factory: Callable[[str], None] | None,
) -> Callable[[str], None]:
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
    display: ParallelDisplay | None,
    result: ReadinessResult,
    workspace_scope: WorkspaceScope,
    workspace: Workspace,
    stack: ProjectStack,
    mode: PolicyMode,
    emit: Callable[[str], None],
    invoke_remediation_agent_factory: Callable[[Workspace], InvokePolicyAgent] | None,
    pre_run_dirty: frozenset[str],
    working_tree_snapshot: WorkingTreeSnapshot,
    commit_policy_updates: PolicyCommit,
) -> int:
    chain_agents = _resolve_chain_agents(load_result, PHASE_REMEDIATION)
    if not chain_agents and invoke_remediation_agent_factory is None:
        logger.warning(
            "policy_remediation chain has no usable configured agent; "
            "skipping policy and continuing the run."
        )
        emit(
            "project-policy-readiness: the policy_remediation chain has no "
            "configured agent; continuing without a ready policy."
        )
        commit_policy_updates(workspace_scope, pre_run_dirty)
        return _exit_code_for_not_ready(mode)

    pipeline_deps = _build_pipeline_deps_for_remediation(load_result, display_context)
    active_display = resolve_active_display(display, display_context)
    if invoke_remediation_agent_factory is not None:
        invoke_agent = invoke_remediation_agent_factory(workspace)
    else:
        invoke_agent = _make_production_invoke_agent(
            load_result,
            pipeline_deps,
            workspace_scope,
            active_display,
            display_context,
        )

    display_scope = nullcontext(active_display) if display is not None else active_display
    resumed_phase = (
        load_result.initial_state.phase if load_result.initial_state is not None else None
    )
    with (
        display_scope,
        remediation_status_bar_session(
            active_display, workspace_scope, resumed_phase=resumed_phase
        ),
    ):
        anchor_value: object = getattr(active_display, "run_started_monotonic", None)
        run_started_monotonic = anchor_value if isinstance(anchor_value, float) else None

        def _on_remediation_attempt(attempt: int) -> None:
            push_remediation_status_bar(
                active_display,
                workspace_scope,
                DEFAULT_MAX_REMEDIATION_ATTEMPTS,
                attempt=attempt,
                run_started_monotonic=run_started_monotonic,
            )

        push_remediation_status_bar(
            active_display,
            workspace_scope,
            DEFAULT_MAX_REMEDIATION_ATTEMPTS,
            run_started_monotonic=run_started_monotonic,
        )
        with _capture_pipeline_state():
            final = run_policy_pipeline(
                workspace,
                stack,
                result.findings,
                invoke_agent=invoke_agent,
                entry_phase=mode.entry_phase(),
                analysis_cap=DEFAULT_ANALYSIS_CAP,
                emit=emit,
                on_remediation_attempt=_on_remediation_attempt,
            )
    if final.is_ready():
        _emit_and_push_readiness_assessment(
            emit,
            display,
            workspace_scope,
            final.assessment,
            push_status=(resumed_phase is None),
        )
        _finalize_ready_state(
            workspace,
            workspace_scope,
            stack,
            pre_run_dirty,
            commit_policy_updates=commit_policy_updates,
            assessment=final.assessment,
            command_outcomes=final.command_outcomes,
        )
        return _EXIT_SUCCESS
    commit_policy_updates(workspace_scope, pre_run_dirty)
    _emit_and_push_readiness_assessment(
        emit,
        display,
        workspace_scope,
        final.assessment,
        push_status=(resumed_phase is None),
    )
    emit("\n".join(final.report_lines))
    return _exit_code_for_not_ready(mode)


def _exit_code_for_not_ready(mode: PolicyMode) -> int:
    if mode.exits_after():
        return _EXIT_PREFLIGHT
    return _EXIT_SUCCESS


def run_project_policy_readiness(
    *,
    load_result: _LoadResult,
    display_context: DisplayContext,
    display: ParallelDisplay | None = None,
    mode: PolicyMode = PolicyMode.NORMAL,
    workspace_factory: Callable[[], Workspace] | None = None,
    emit_factory: Callable[[str], None] | None = None,
    invoke_remediation_agent_factory: Callable[[Workspace], InvokePolicyAgent] | None = None,
    select_factory: _prompt_ui.SelectFn | None = None,
    is_tty: Callable[[], bool] | None = None,
    working_tree_snapshot: WorkingTreeSnapshot = _snapshot_working_tree,
    commit_policy_updates: PolicyCommit = _commit_policy_changes,
) -> int:
    try:
        return _run_policy_readiness(
            load_result=load_result,
            display_context=display_context,
            display=display,
            mode=mode,
            workspace_factory=workspace_factory,
            emit_factory=emit_factory,
            invoke_remediation_agent_factory=invoke_remediation_agent_factory,
            select_factory=select_factory,
            is_tty=is_tty,
            working_tree_snapshot=working_tree_snapshot,
            commit_policy_updates=commit_policy_updates,
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            "project-policy preflight crashed ({}); continuing to the "
            "development run without a ready policy.",
            exc,
        )
        _emit_safely(
            display_context,
            emit_factory,
            "project-policy-readiness: the policy preflight failed unexpectedly "
            f"({type(exc).__name__}: {exc}). Continuing to the development run.",
        )
        return _exit_code_for_not_ready(mode)


def _emit_and_push_readiness_assessment(
    emit: Callable[[str], None],
    display: ParallelDisplay | None,
    workspace_scope: WorkspaceScope,
    assessment: policy_models.ReadinessAssessment | None,
    *,
    push_status: bool = True,
) -> None:
    if assessment is not None:
        dims = ", ".join(
            f"{name}={'true' if value else 'false'}" for name, value in assessment.as_dict().items()
        )
        emit(f"readiness-assessment: {dims}")
        if assessment.structure_complete and not (
            assessment.implementation_verified and assessment.outcome_evidence_reviewed
        ):
            false_dims: list[str] = []
            if not assessment.implementation_verified:
                false_dims.append("implementation verified")
            if not assessment.outcome_evidence_reviewed:
                false_dims.append("outcome evidence reviewed")
            verb = "is" if len(false_dims) == 1 else "are"
            dims_str = " and ".join(false_dims)
            emit(
                "readiness-assessment-note: structural READY is the "
                "caching gate; structural READY is NOT semantic "
                f"correctness ({dims_str} {verb} still false)"
            )
    if display is not None and push_status:
        run_started_monotonic: float | None = None
        active_display_obj: object = display
        if active_display_obj is not None:
            anchor: object = getattr(active_display_obj, "run_started_monotonic", None)
            if isinstance(anchor, float):
                run_started_monotonic = anchor
        try:
            push_readiness_assessment_status_bar(
                display,
                workspace_scope,
                assessment,
                run_started_monotonic=run_started_monotonic,
            )
        except Exception as exc:
            logger.debug(
                "readiness-assessment status-bar push failed (non-fatal): {}",
                exc,
            )


def _emit_safely(
    display_context: DisplayContext,
    emit_factory: Callable[[str], None] | None,
    message: str,
) -> None:
    try:
        _build_emit(display_context, emit_factory)(message)
    except Exception as exc:
        logger.debug("policy failure could not be displayed: {}", exc)


def _run_policy_readiness(
    *,
    load_result: _LoadResult,
    display_context: DisplayContext,
    display: ParallelDisplay | None,
    mode: PolicyMode,
    workspace_factory: Callable[[], Workspace] | None,
    emit_factory: Callable[[str], None] | None,
    invoke_remediation_agent_factory: Callable[[Workspace], InvokePolicyAgent] | None,
    select_factory: _prompt_ui.SelectFn | None,
    is_tty: Callable[[], bool] | None,
    working_tree_snapshot: WorkingTreeSnapshot,
    commit_policy_updates: PolicyCommit,
) -> int:
    workspace_scope = load_result.workspace_scope
    if workspace_scope is None:
        return _EXIT_SUCCESS

    pre_run_dirty = working_tree_snapshot(workspace_scope)

    emit = _build_emit(display_context, emit_factory)
    workspace = _build_workspace(load_result, workspace_factory)

    if mode.resets_policy():
        changed = reset_policy_state(workspace)
        emit(
            f"project-policy-readiness: reset {len(changed)} policy path(s); "
            "regenerating from scratch"
        )

    if not mode.is_explicit():
        _maybe_offer_inline_policy_skip(workspace, emit, select=select_factory, is_tty=is_tty)
        if not policy_schema_upgrade._maybe_resolve_schema_upgrade(
            workspace, emit, select=select_factory, is_tty=is_tty
        ):
            return _EXIT_SUCCESS

    stack = get_project_stack(workspace)
    result = run_policy_readiness_preflight(workspace, stack, emit=emit)

    if result.is_skipped() and not mode.is_explicit():
        emit("project-policy-readiness: skipped (opt-out marker present)")
        return _EXIT_SUCCESS

    if result.is_ready() and not mode.is_explicit():
        emit(f"project-policy-readiness: ready ({len(result.changed_files)} files updated)")
        _emit_and_push_readiness_assessment(
            emit,
            display,
            workspace_scope,
            result.assessment,
        )
        _finalize_ready_state(
            workspace,
            workspace_scope,
            stack,
            pre_run_dirty,
            commit_policy_updates=commit_policy_updates,
        )
        return _EXIT_SUCCESS

    return _dispatch_preflight_result(
        load_result=load_result,
        display_context=display_context,
        display=display,
        result=result,
        workspace_scope=workspace_scope,
        workspace=workspace,
        stack=stack,
        mode=mode,
        emit=emit,
        invoke_remediation_agent_factory=invoke_remediation_agent_factory,
        pre_run_dirty=pre_run_dirty,
        working_tree_snapshot=working_tree_snapshot,
        commit_policy_updates=commit_policy_updates,
    )


__all__ = [
    "_EXIT_PREFLIGHT",
    "_EXIT_SUCCESS",
    "EmitFn",
    "PolicyMode",
    "run_project_policy_readiness",
]
