"""Phase artifact rendering and post-agent-run event handling."""

from __future__ import annotations

import shlex
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.chain import ChainManager
from ralph.agents.completion_signals import evaluate_completion, graded_phase_verdict
from ralph.agents.invoke._process_reader import _parent_broker_secret
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import Verbosity
from ralph.display.parallel_display import (
    ParallelDisplay,
    get_display_context,
    resolve_active_display,
)
from ralph.display.raw_overflow import detect_raw_log_breaks, raw_log_path_for
from ralph.phases import PhaseContext, handle_phase
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent

if TYPE_CHECKING:
    from typing import Protocol

    from ralph.config.agent_config import AgentConfig
    from ralph.config.models import UnifiedConfig
    from ralph.display.artifact_reader import AnalysisDecisionSummary, PlanSummary
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.effects import Effect, InvokeAgentEffect
    from ralph.pipeline.events import Event
    from ralph.pipeline.plumbing.smoke_evidence import Provenance
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
    module = cast(
        "_ArtifactReaderModule", import_module("ralph.display.artifact_reader")
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    return module.read_latest_analysis_decision


def _read_plan_artifact_func() -> _ReadPlanArtifactFn:
    module = cast(
        "_ArtifactReaderModule", import_module("ralph.display.artifact_reader")
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
    run_id: str | None = None,
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
            run_id=run_id,
            # S-4 (G4 / DoD 15): the registry built above already resolves
            # every agent from ``config``; reuse it here so the shared
            # phase-verdict seam can name the exact raw log the real
            # writer used for this phase's agent.
            agent_config=ctx.registry.get(effect.agent_name),
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
    run_id: str | None = None,
    agent_config: AgentConfig | None = None,
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
            run_id=run_id,
            agent_config=agent_config,
        )


def _raw_transcript_break_detail(
    workspace_root: Path, agent_config: AgentConfig | None
) -> str | None:
    """S-4 (G4 / DoD 15): the shared non-smoke phase-verdict seam.

    Mirrors ``smoke_plumbing._raw_transcript_corruption_errors``'s path
    formula exactly (``unit_id`` via ``shlex.split(agent_config.cmd)[0]``,
    matching the private ``_agent_command_name`` helper in
    ``_process_reader.py``, and ``model`` read directly from
    ``agent_config.model``) so this reads the exact file the real
    ``RawOverflowLog`` writer used. Returns ``None`` when no agent config is
    available (the caller could not resolve one) or no break is found --
    never raises.
    """
    if agent_config is None:
        return None
    try:
        unit_id = shlex.split(agent_config.cmd)[0]
    except (ValueError, IndexError):
        return None
    raw_path = raw_log_path_for(workspace_root, unit_id, model=agent_config.model)
    breaks = detect_raw_log_breaks(raw_path)
    if not breaks:
        return None
    return f"raw transcript corrupted: {breaks[0].detail}"


def _compute_graded_phase_verdict(
    workspace_root: Path,
    required_artifact: RequiredArtifact,
    run_id: str | None,
    agent_config: AgentConfig | None = None,
) -> tuple[str, Provenance, str]:
    """Recompute graded completion evidence at the phase-close render boundary.

    Safe recomputation, not unplanned/unsafe re-derivation: every input
    ``evaluate_completion`` needs -- ``workspace_root``, the already-resolved
    ``required_artifact``, the ``run_id`` threaded from the effect-execution
    call site, and the broker secret via the sanctioned
    ``_parent_broker_secret`` accessor -- is in scope here with no further
    plumbing. This reads the same durable receipt/sentinel state the
    completion-enforcement path already gated on; it does not derive a new
    decision (S-2).

    ``agent_config`` (S-4 / G4 / DoD 15) additionally folds a detected raw
    transcript corruption into the returned detail string, so a corrupted or
    truncated capture is a reported break at this shared phase-verdict seam,
    not only at the smoke gate. Optional and additive: omitting it (the
    default) reproduces the pre-S-4 behavior exactly.
    """
    secret = _parent_broker_secret()
    signals = evaluate_completion(
        workspace_root,
        required_artifact=required_artifact,
        run_id=run_id,
        receipt_secret=secret,
        sentinel_secret=secret,
    )
    label, weakest, detail = graded_phase_verdict(signals, required_artifact=required_artifact)
    break_detail = _raw_transcript_break_detail(workspace_root, agent_config)
    if break_detail is not None:
        detail = f"{detail}; {break_detail}" if detail else break_detail
    return label, weakest, detail


def _format_graded_phase_verdict(label: str, weakest: Provenance, detail: str) -> str:
    """Format ``_compute_graded_phase_verdict``'s tuple as operator-facing text.

    S-4 (G4 / DoD 15): a non-empty ``detail`` (e.g. a folded-in raw
    transcript corruption break) is appended for EVERY label, not only
    ``FAILED`` -- a corrupted transcript is a reported break regardless of
    whether the phase otherwise graded PASS or DEGRADED on its completion
    evidence alone. Empty ``detail`` reproduces the pre-S-4 text exactly.
    """
    if label == "PASS":
        return f"PASS — {detail}" if detail else "PASS"
    if label == "FAILED":
        return f"FAILED (no artifact) — {detail}" if detail else "FAILED (no artifact)"
    base = f"DEGRADED ({weakest.name.lower().replace('_', '-')})"
    return f"{base} — {detail}" if detail else base


def _render_success_artifact(
    artifact_type: str,
    workspace_root: Path,
    display_context: DisplayContext,
    display: ParallelDisplay | None,
    verbosity: Verbosity,
    ra: RequiredArtifact,
    *,
    run_id: str | None = None,
    agent_config: AgentConfig | None = None,
) -> None:
    def _emit_close(produced: str) -> None:
        if verbosity != Verbosity.QUIET and hasattr(display, "record_artifact_outcome"):
            with suppress(Exception):
                cast("ParallelDisplay", display).record_artifact_outcome(
                    produced
                )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)

    def _with_verdict(produced: str) -> str:
        # Additive only (F6 / DoD 12): surfaces the graded PASS / DEGRADED
        # (<rung>) / FAILED (no artifact) text alongside the existing
        # step/risk-count string, never replacing it. Best-effort -- a
        # grading failure must not blank out the artifact summary that
        # already rendered successfully.
        with suppress(Exception):
            label, weakest, detail = _compute_graded_phase_verdict(
                workspace_root, ra, run_id, agent_config
            )
            return f"{produced} — {_format_graded_phase_verdict(label, weakest, detail)}"
        return produced

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
            _emit_close(_with_verdict(produced))
        return

    if artifact_type == "development_result":
        _emit_via_display(display_context, "emit_development_artifact", workspace_root)
        produced = (
            "result produced"
            if (workspace_root / ra.artifact_path).exists()
            else "no result artifact"
        )
        _emit_close(_with_verdict(produced))
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
            _emit_close(_with_verdict(f"{issue_count} issue(s)"))
        return

    if artifact_type == "fix_result":
        _emit_via_display(display_context, "emit_fix_artifact", workspace_root)
        _emit_close(_with_verdict("applied"))


def _render_phase_failure_report(
    effect: InvokeAgentEffect,
    *,
    policy_bundle: PolicyBundle,
    workspace: FsWorkspace,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    run_id: str | None = None,
    config: UnifiedConfig | None = None,
) -> None:
    """Render ``Verdict: FAILED (no artifact)`` when an agent invocation did
    not succeed for a required-artifact phase (F6 / DoD 12).

    ``config`` (S-4 / G4 / DoD 15) is optional and additive: when supplied,
    ``effect.agent_name``'s :class:`AgentConfig` is resolved from it so the
    shared phase-verdict seam can also fold a detected raw transcript
    corruption into the rendered detail. Omitting it (the default)
    reproduces the pre-S-4 behavior exactly.

    Reachability (PA-001): called directly from the runner/worker call
    sites' ``else`` branch on a non-``AGENT_SUCCESS`` event -- NOT through
    ``_render_phase_artifact_handoff`` (which only ever runs on
    ``AGENT_SUCCESS``) and NOT deferred to a later phase-transition-
    triggered render (most retrying ``AGENT_FAILURE`` events never change
    ``state.phase``, so that render never fires for them). This function is
    a display-only side effect: it never calls ``handle_phase``, mutates
    the pipeline event, or otherwise changes what the reducer sees
    afterward.

    An optional-artifact or artifact-free phase failing is out of this
    feature's scope -- returns immediately when no required artifact is
    registered for the phase, or the registered artifact is optional.
    """
    effective_drain = effect.drain or effect.phase
    with suppress(Exception):
        required_artifact = resolve_phase_required_artifact(
            policy_bundle.pipeline,
            policy_bundle.artifacts,
            phase=effect.phase,
            drain=effective_drain,
        )
        if required_artifact is None or not required_artifact.artifact_required:
            return
        workspace_root = Path(workspace.absolute_path("."))
        agent_config = (
            AgentRegistry.from_config(config).get(effect.agent_name)
            if config is not None
            else None
        )
        label, weakest, detail = _compute_graded_phase_verdict(
            workspace_root, required_artifact, run_id, agent_config
        )
        if label != "FAILED":
            return
        if verbosity == Verbosity.QUIET:
            return
        # Resolve the active display the same way
        # ``_render_phase_artifact_handoff`` does (some call sites, e.g.
        # the parallel worker runtime, only have a ``DisplayContext`` in
        # scope, not a live ``ParallelDisplay``).
        ctx = get_display_context(display, display_context)
        _emit_via_display(
            ctx,
            "emit",
            effect.agent_name,
            f"Verdict: {_format_graded_phase_verdict(label, weakest, detail)}",
        )


phase_event_after_agent_run = _phase_event_after_agent_run
render_phase_failure_report = _render_phase_failure_report
render_phase_artifact_handoff = _render_phase_artifact_handoff
render_success_artifact = _render_success_artifact
