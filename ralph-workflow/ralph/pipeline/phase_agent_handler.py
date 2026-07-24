"""Phase artifact rendering and post-agent-run event handling."""

from __future__ import annotations

from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.chain import ChainManager
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import Verbosity
from ralph.display.parallel_display import (
    ParallelDisplay,
    get_display_context,
    resolve_active_display,
)
from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_ARTIFACT
from ralph.phases import PhaseContext, handle_phase
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline.effects import CommitEffect, InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent

if TYPE_CHECKING:
    from typing import Protocol

    from ralph.config.models import UnifiedConfig
    from ralph.display.artifact_reader import AnalysisDecisionSummary, PlanSummary
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.events import Event
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle
    from ralph.workspace import FsWorkspace
    from ralph.workspace.scope import WorkspaceScope

    class _HandlePhaseFn(Protocol):
        def __call__(self, effect: Effect, ctx: PhaseContext) -> list[Event]: ...

    class _ReadLatestAnalysisDecisionFn(Protocol):
        def __call__(
            self,
            workspace_root: Path,
            drain: str,
        ) -> AnalysisDecisionSummary | None: ...

    class _ReadPlanArtifactFn(Protocol):
        def __call__(self, workspace_root: Path) -> PlanSummary | None: ...

    class _ArtifactReaderModule(Protocol):
        read_latest_analysis_decision: _ReadLatestAnalysisDecisionFn
        read_plan_artifact: _ReadPlanArtifactFn


def _read_latest_analysis_decision_func() -> _ReadLatestAnalysisDecisionFn:
    module = cast("_ArtifactReaderModule", import_module("ralph.display.artifact_reader"))
    return module.read_latest_analysis_decision


def _read_plan_artifact_func() -> _ReadPlanArtifactFn:
    module = cast("_ArtifactReaderModule", import_module("ralph.display.artifact_reader"))
    return module.read_plan_artifact


def _emit_via_display(
    display_context: DisplayContext,
    method_name: str,
    *args: object,
    **kwargs: object,
) -> bool:
    """Resolve an active display and dispatch to the named method.

    Returns True when a ParallelDisplay with the requested method was found
    and invoked. Returns False when no active display is available, allowing
    callers to fall back to the legacy free-function path if one exists.
    """
    try:
        display: ParallelDisplay = resolve_active_display(None, display_context)
    except Exception:
        return False
    method = getattr(display, method_name, None)  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    if method is None or not callable(method):  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        return False
    try:
        method(*args, **kwargs)
    except Exception:
        return False
    return True


def _phase_event_after_agent_run(
    *,
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle,
    workspace: FsWorkspace,
    workspace_scope: WorkspaceScope | None = None,
    display: ParallelDisplay | None = None,
    display_context: DisplayContext | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
    handle_phase_fn: _HandlePhaseFn | None = None,
) -> Event:
    ctx = PhaseContext.model_construct(
        workspace=workspace,
        registry=AgentRegistry.from_config(config),
        chain_manager=ChainManager(policy_bundle.agents),
        pipeline_policy=policy_bundle.pipeline,
        agents_policy=policy_bundle.agents,
        artifacts_policy=policy_bundle.artifacts,
        config=config,
        console=get_display_context(display, display_context).console,
    )
    try:
        _hp = handle_phase_fn or handle_phase
        events = _hp(effect, ctx)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        logger.exception(
            "Phase handler crashed in phase={phase}: {err}",
            phase=effect.phase,
            err=exc,
        )
        events = [
            PhaseFailureEvent(
                phase=effect.phase,
                reason=f"Phase handler crashed: {type(exc).__name__}: {exc}",
                recoverable=True,
            )
        ]
    event: Event = events[0] if events else PipelineEvent.AGENT_SUCCESS

    with suppress(Exception):
        _render_phase_artifact_handoff(
            effect.phase,
            event,
            Path(workspace.absolute_path(".")),
            display,
            display_context=display_context,
            verbosity=verbosity,
            drain=effect.drain,
            policy_bundle=policy_bundle,
            state=state,
        )

    if (
        display is not None
        and workspace_scope is not None
        and event in (PipelineEvent.ANALYSIS_SUCCESS, PipelineEvent.ANALYSIS_LOOPBACK)
        and hasattr(display, "emit_analysis_result")
    ):
        try:
            drain = effect.drain or effect.phase
            read_latest_analysis_decision = _read_latest_analysis_decision_func()
            summary = read_latest_analysis_decision(workspace_scope.root, drain)
            if summary is not None:
                display.emit_analysis_result(
                    phase=effect.phase,
                    decision=summary.decision,
                    reason=summary.reason,
                )
        except Exception:
            logger.debug("Failed to emit analysis result", exc_info=True)

    return event


def _render_phase_artifact_handoff(
    phase: str,
    event: Event,
    workspace_root: Path,
    display: ParallelDisplay | None,
    *,
    display_context: DisplayContext | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    drain: str | None = None,
    policy_bundle: PolicyBundle | None = None,
    state: PipelineState | None = None,
) -> None:
    ctx = get_display_context(display, display_context)
    effective_drain = drain or phase
    required_artifact = (
        resolve_phase_required_artifact(
            policy_bundle.pipeline,
            policy_bundle.artifacts,
            phase=phase,
            drain=effective_drain,
        )
        if policy_bundle is not None
        else None
    )

    if required_artifact is None:
        if event != PipelineEvent.AGENT_SUCCESS:
            return
        if policy_bundle is not None:
            phase_def = policy_bundle.pipeline.phases.get(phase)
            role = phase_def.role if phase_def is not None else None
            if role == "analysis":
                _emit_via_display(ctx, "emit_analysis_decision", workspace_root, effective_drain)
            else:
                logger.debug(
                    "policy: no renderer for phase '{}' (role={});"
                    " skipping artifact handoff render",
                    phase,
                    role,
                )
        return

    artifact_type = required_artifact.artifact_type
    if artifact_type.endswith("_analysis_decision"):
        _emit_via_display(ctx, "emit_analysis_decision", workspace_root, effective_drain)
        return

    if event == PipelineEvent.AGENT_SUCCESS:
        _render_success_artifact(
            artifact_type,
            workspace_root,
            ctx,
            display,
            verbosity,
            required_artifact,
        )


def _render_success_artifact(
    artifact_type: str,
    workspace_root: Path,
    display_context: DisplayContext,
    display: ParallelDisplay | None,
    verbosity: Verbosity,
    ra: RequiredArtifact,
) -> None:
    def _emit_close(produced: str) -> None:
        if verbosity != Verbosity.QUIET and hasattr(display, "record_artifact_outcome"):
            with suppress(Exception):
                cast("ParallelDisplay", display).record_artifact_outcome(produced)

    if artifact_type == "plan":
        _emit_via_display(display_context, "emit_plan_artifact", workspace_root)
        with suppress(Exception):
            read_plan_artifact = _read_plan_artifact_func()
            plan = read_plan_artifact(workspace_root)
            produced = (
                f"{plan.total_steps} step(s), {len(plan.risks_mitigations)} risk(s)"
                if plan is not None
                else "(no plan artifact on disk)"
            )
            _emit_close(produced)
        return

    if artifact_type == "development_result":
        _emit_via_display(display_context, "emit_development_artifact", workspace_root)
        produced = (
            "result produced" if (workspace_root / ra.artifact_path).exists() else "no result artifact"
        )
        _emit_close(produced)
        return

    if artifact_type == "issues":
        _emit_via_display(display_context, "emit_review_artifact", workspace_root)
        with suppress(Exception):
            issue_count = 0
            issues_path = workspace_root / ra.artifact_path
            if issues_path.exists():
                with suppress(Exception):
                    from ralph.mcp.artifacts.markdown import parse_and_validate
                    from ralph.mcp.artifacts.markdown.registry import get_spec

                    import_module("ralph.mcp.artifacts.markdown.specs")
                    content, diagnostics = parse_and_validate(
                        issues_path.read_text(encoding="utf-8"),
                        get_spec("issues"),
                    )
                    if not any(item.severity == "error" for item in diagnostics):
                        issues_list = content.get("issues")
                        if isinstance(issues_list, list):
                            issue_count = len(issues_list)
            _emit_close(f"{issue_count} issue(s)")
        return

    if artifact_type == "fix_result":
        _emit_via_display(display_context, "emit_fix_artifact", workspace_root)
        _emit_close("applied")


def _commit_effect(workspace_root: Path) -> CommitEffect:
    return CommitEffect(message_file=str(workspace_root / COMMIT_MESSAGE_ARTIFACT))


phase_event_after_agent_run = _phase_event_after_agent_run
render_phase_artifact_handoff = _render_phase_artifact_handoff
render_success_artifact = _render_success_artifact
commit_effect = _commit_effect
