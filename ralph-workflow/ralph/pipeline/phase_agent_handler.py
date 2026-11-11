
from __future__ import annotations

import json
import os
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph.agents._broker_secret import _parent_broker_secret
from ralph.agents.chain import ChainManager
from ralph.agents.completion_signals import evaluate_completion, graded_phase_verdict
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
from ralph.mcp.server.lifecycle import McpServerError
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
    from ralph.workspace.probe import WorkspaceChangeProbe
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
    )
    return module.read_latest_analysis_decision


def _read_plan_artifact_func() -> _ReadPlanArtifactFn:
    module = cast(
        "_ArtifactReaderModule", import_module("ralph.display.artifact_reader")
    )
    return module.read_plan_artifact


def _emit_via_display(
    display_context: DisplayContext,
    method_name: str,
    *args: object,
    **kwargs: object,
) -> bool:
    try:
        display: ParallelDisplay = resolve_active_display(None, display_context)
    except Exception:
        return False
    method = getattr(display, method_name, None)
    if method is None or not callable(method):
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
    workspace_probe: WorkspaceChangeProbe | None = None,
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
        workspace_probe=workspace_probe,
    )
    try:
        _hp = handle_phase_fn or handle_phase
        events = _hp(effect, ctx)
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit) as exc:
        if isinstance(exc, McpServerError):
            logger.warning(
                "Phase handler McpServerError in phase={phase}: "
                "last_stage={last_stage} restart_reason={restart_reason} failure_kind={failure_kind} "
                "error={exc}",
                phase=effect.phase,
                last_stage=exc.last_stage,
                restart_reason=exc.restart_reason,
                failure_kind=exc.failure_kind,
                exc=exc,
            )
        else:
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


_TRANSPORT_FAILURE_TAIL_BYTES: Final = 64 * 1024

_TRANSPORT_FAILURE_DETAIL_MAX_CHARS: Final = 240


_TRANSPORT_RECOVERY_FRAME_TYPES: Final = frozenset({"turn.completed", "turn.started"})


def _terminal_transport_failure_message(obj: dict[str, object]) -> str | None:
    if obj.get("type") != "turn.failed":
        return None
    error: object = obj.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        return str(message) if message else ""
    if isinstance(error, str) and error:
        return error
    return ""


def _transport_failure_detail(workspace_root: Path, agent_config: AgentConfig | None) -> str | None:
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
            message = None
            continue
        found = _terminal_transport_failure_message(parsed)
        if found is not None:
            message = found
    if message is None:
        return None
    message = strip_terminal_control(message).replace("\n", " ").strip()
    if not message:
        return None
    if len(message) > _TRANSPORT_FAILURE_DETAIL_MAX_CHARS:
        message = message[: _TRANSPORT_FAILURE_DETAIL_MAX_CHARS - 1] + "..."
    return _format_transport_failure_detail(message)


def _format_transport_failure_detail(message: str) -> str:
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
    secret = _parent_broker_secret()
    signals = evaluate_completion(
        workspace_root,
        required_artifact=required_artifact,
        run_id=run_id,
        receipt_secret=secret,
        sentinel_secret=secret,
    )
    label, weakest, detail = graded_phase_verdict(signals, required_artifact=required_artifact)
    if label == "FAILED" and not shared_capture:
        transport_detail = _transport_failure_detail(workspace_root, agent_config)
        if transport_detail is not None:
            detail = f"{detail}; {transport_detail}" if detail else transport_detail
    break_detail = _raw_transcript_break_detail(workspace_root, agent_config)
    if break_detail is not None:
        detail = f"{detail}; {break_detail}" if detail else break_detail
    return label, weakest, detail


def _format_graded_phase_verdict(label: str, weakest: Provenance, detail: str) -> str:
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
                )

    def _with_verdict(produced: str) -> str:
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
            AgentRegistry.from_config(config).get(effect.agent_name) if config is not None else None
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
