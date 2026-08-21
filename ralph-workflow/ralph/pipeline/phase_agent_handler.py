"""Phase artifact rendering and post-agent-run event handling."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph.agents.chain import ChainManager
from ralph.agents.completion_signals import evaluate_completion, graded_phase_verdict
from ralph.agents.invoke._process_reader import _parent_broker_secret
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import Verbosity
from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.parallel_display import (
    ParallelDisplay,
    get_display_context,
    resolve_active_display,
)
from ralph.display.raw_overflow import (
    detect_raw_log_breaks,
    raw_log_path_for,
    raw_log_unit_id_for,
)
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
    shared_capture: bool = False,
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
            shared_capture=shared_capture,
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
    shared_capture: bool = False,
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
            shared_capture=shared_capture,
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
    unit_id = raw_log_unit_id_for(agent_config)
    if not unit_id:
        return None
    raw_path = raw_log_path_for(workspace_root, unit_id, model=agent_config.model)
    breaks = detect_raw_log_breaks(raw_path, transport=agent_config.transport)
    if not breaks:
        return None
    return f"raw transcript corrupted: {breaks[0].detail}"


#: Bytes of raw transcript tail scanned for a terminal transport failure.
#: The frame is emitted immediately before the agent process exits, so the
#: tail always carries it; bounding the read keeps the phase-close seam
#: cheap on the multi-megabyte captures a long unit produces.
_TRANSPORT_FAILURE_TAIL_BYTES: Final = 64 * 1024

#: Longest transport-failure message folded into the verdict. Long enough
#: for an API rejection with its parameter path, short enough that the
#: verdict line stays readable in a terminal.
_TRANSPORT_FAILURE_DETAIL_MAX_CHARS: Final = 240


#: Frame types that END a unit's turn successfully. A failure frame
#: followed by one of these was recovered from, so it is history rather
#: than the cause of a missing artifact. One raw capture accumulates
#: every attempt and every phase for an ``(executable, model)`` pair, so
#: without this the scan reports stale frames from attempts that already
#: succeeded.
#:
#: Both COMPLETION and START clear the pending cause, because the rule is
#: "report the outcome of the last turn that BEGAN". One raw capture is
#: keyed only by ``(executable, model)``, so it accumulates every retry,
#: every phase, and every parallel work unit sharing that agent. Without
#: the start clearing too, phase B's verdict inherits phase A's failure.
#:
#: The cost is silence when a retry is killed before writing anything.
#: That is the right trade: a missing cause makes an operator look at the
#: transcript, while a cause borrowed from a different phase makes them
#: chase a fault that phase never had.
#:
#: This orders SEQUENTIAL turns. It cannot separate CONCURRENT ones: the
#: capture is keyed by ``(executable, model)``, so same-workspace
#: parallel workers running the same agent interleave their frames in one
#: file and no ordering heuristic can attribute a failure to a unit.
#: Separating those would need a per-unit capture path.
_TRANSPORT_RECOVERY_FRAME_TYPES: Final = frozenset({"turn.completed", "turn.started"})


def _terminal_transport_failure_message(obj: dict[str, object]) -> str | None:
    """Return the human-readable message of a terminal transport frame.

    Recognises the Codex ``turn.failed`` frame, whose ``error`` field is
    either a string or an object carrying ``message``. Returns ``None``
    for any other frame shape.
    """
    if obj.get("type") != "turn.failed":
        return None
    error: object = obj.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        return str(message) if message else ""
    if isinstance(error, str) and error:
        return error
    # A failure frame carrying no message. Returning the empty string
    # says "a failure happened, with nothing to quote" -- synthesising
    # words here would put them in quotation marks the caller attributes
    # to the transcript.
    return ""


def _transport_failure_detail(
    workspace_root: Path, agent_config: AgentConfig | None
) -> str | None:
    """Return a bounded description of a turn killed at the transport.

    A work unit whose turn is rejected by the provider API never writes
    its artifact, so completion evidence grades it ``FAILED (no
    artifact)`` -- which reads as an agent-quality outcome and hides the
    infrastructure fault that actually happened. Measured 2026-08-20: a
    Codex turn died on a 400 (``Invalid value: 'output_text'``) and the
    verdict said only that the receipt was missing.

    Folding the transcript's terminal failure frame into the verdict
    detail keeps the grade unchanged (no artifact IS no artifact) while
    naming the cause. Returns ``None`` when no agent config is
    available, the transcript is absent, or no terminal failure frame is
    present -- never raises.
    """
    if agent_config is None:
        return None
    unit_id = raw_log_unit_id_for(agent_config)
    if not unit_id:
        return None
    raw_path = raw_log_path_for(workspace_root, unit_id, model=agent_config.model)
    try:
        with raw_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _TRANSPORT_FAILURE_TAIL_BYTES))
            tail = handle.read()
    except OSError:
        return None
    message: str | None = None
    for raw_line in tail.decode("utf-8", errors="replace").splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed: object = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if parsed.get("type") in _TRANSPORT_RECOVERY_FRAME_TYPES:
            # The unit kept going after the failure; drop what we had.
            message = None
            continue
        # Keep scanning: the LAST unrecovered failure frame is the one
        # that ended the unit. ``""`` is a real result (a failure with no
        # message), so test for None rather than truthiness.
        found = _terminal_transport_failure_message(parsed)
        if found is not None:
            message = found
    if message is None:
        return None
    # The message is agent-influenced text lifted out of the transcript
    # and rendered on the operator's terminal, so it goes through the
    # same control-sequence stripper as every other agent-origin string.
    # Pinned by
    # ``tests/test_phase_transport_failure_reporting.py::
    # test_transport_failure_detail_contains_terminal_escapes``.
    message = strip_terminal_control(message).replace("\n", " ").strip()
    if not message:
        return None
    if len(message) > _TRANSPORT_FAILURE_DETAIL_MAX_CHARS:
        message = message[: _TRANSPORT_FAILURE_DETAIL_MAX_CHARS - 1] + "..."
    # Quoted and attributed on purpose. The raw capture is the agent's own
    # stdout, so an agent can emit a frame shaped like a transport failure
    # and put words in this line. Presenting the text as a quotation from
    # the transcript -- rather than as Ralph Workflow's own finding --
    # keeps the provenance honest.
    return _format_transport_failure_detail(message)


def _format_transport_failure_detail(message: str) -> str:
    """Render the operator-facing detail for a failure frame's message."""
    if not message:
        return "agent turn failed at the transport (the frame carried no message)"
    quoted = f'"{message}"'
    return f"agent turn failed at the transport; transcript reports: {quoted}"


def _compute_graded_phase_verdict(
    workspace_root: Path,
    required_artifact: RequiredArtifact,
    run_id: str | None,
    agent_config: AgentConfig | None = None,
    *,
    shared_capture: bool = False,
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
    # Only a phase that actually graded FAILED gets the transport cause
    # folded in. A PASS or DEGRADED phase produced its artifact, so a
    # failure frame in its transcript was recovered from and naming it
    # would read as an alarm about a run that worked.
    # ``shared_capture``: several concurrent units write one capture, so a
    # failure frame in it cannot be attributed to this unit. Quoting a
    # sibling's rejection here would be materially wrong, not merely
    # unhelpful.
    if label == "FAILED" and not shared_capture:
        transport_detail = _transport_failure_detail(workspace_root, agent_config)
        if transport_detail is not None:
            detail = f"{detail}; {transport_detail}" if detail else transport_detail
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
    shared_capture: bool = False,
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
        # already rendered successfully -- but the failure is LOGGED. A
        # bare suppression here dropped the whole PASS/DEGRADED/FAILED
        # label and any corruption detail with no trace, leaving an
        # operator (and an investigator) a bare artifact summary and no
        # way to learn that grading had raised at all.
        try:
            label, weakest, detail = _compute_graded_phase_verdict(
                workspace_root,
                ra,
                run_id,
                agent_config,
                shared_capture=shared_capture,
            )
        except Exception:
            logger.debug(
                "graded phase verdict failed for {artifact}", artifact=artifact_type, exc_info=True
            )
            return produced
        return f"{produced} — {_format_graded_phase_verdict(label, weakest, detail)}"

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
    shared_capture: bool = False,
) -> None:
    """Render ``Verdict: FAILED (no artifact)`` when an agent invocation did
    not succeed for a required-artifact phase (F6 / DoD 12).

    ``shared_capture`` marks a caller whose raw capture is shared with
    other concurrently-running units -- parallel workers key the capture
    by ``(executable, model)``, so several of them interleave frames in
    one file. Transport-failure attribution is suppressed for those
    callers: no ordering heuristic can tell which unit a failure frame
    belongs to, and quoting a sibling unit's API rejection in this unit's
    verdict is worse than reporting no cause at all.

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
            workspace_root,
            required_artifact,
            run_id,
            agent_config,
            shared_capture=shared_capture,
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
