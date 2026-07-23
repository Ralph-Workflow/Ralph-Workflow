"""Activity stream rendering and artifact handoff for the pipeline runner."""

from __future__ import annotations

import json
import shutil
from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger
from rich.text import Text

from ralph.agents.invoke import extract_transport_session_id
from ralph.agents.parsers import AgentOutputLine, AgentParser, get_parser, resolve_parser_key
from ralph.config.enums import AgentTransport, Verbosity
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_router import map_parser_type_to_kind
from ralph.display.parallel_display import (
    ParallelDisplay,
    emit_activity_line,
    get_display_context,
    resolve_active_display,
    subscriber_for_display,
)
from ralph.mcp.server._activity_sink import invoke_subagent_sink
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline.artifact_handoff_context import ArtifactHandoffContext
from ralph.pipeline.events import PipelineEvent

if TYPE_CHECKING:
    from collections import deque
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

    from ralph.agents.idle_watchdog import SubagentPidRegistry
    from ralph.config.agent_config import AgentConfig
    from ralph.display.artifact_reader import PlanSummary
    from ralph.display.context import DisplayContext
    from ralph.display.subscriber import PipelineSubscriber
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.events import Event

if TYPE_CHECKING:

    class _ReadPlanArtifactFn(Protocol):
        def __call__(self, workspace_root: Path) -> PlanSummary | None: ...

    class _ParallelDisplayModule(Protocol):
        ParallelDisplay: type[ParallelDisplay]


_MAX_TEXT_LENGTH = 200
_MAX_TOOL_RESULT_BRIEF = 80
_MAX_METADATA_SUMMARY_LENGTH = 120


def _parallel_display_cls() -> type[ParallelDisplay]:
    module = cast("_ParallelDisplayModule", import_module("ralph.display.parallel_display"))
    return module.ParallelDisplay


def _emit_via_display(
    display_context: DisplayContext,
    method_name: str,
    *args: object,
    **kwargs: object,
) -> bool:
    """Resolve an active display and call the named method, returning success.

    Returns True when a ParallelDisplay with the requested method was found
    and invoked. Returns False when no active display is available, allowing
    callers to fall back to the legacy free-function path if one exists.

    When ``display_context`` itself is a ``ParallelDisplay`` (test fakes,
    legacy paths) the method is called directly on the supplied object. When
    it is a ``DisplayContext`` (the canonical path), the active display is
    resolved via ``resolve_active_display``.
    """
    display: object | None = display_context
    if not isinstance(display_context, ParallelDisplay):
        try:
            display = resolve_active_display(None, display_context)
        except Exception:
            return False
    if display is None:
        return False
    method = getattr(display, method_name, None)  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    if method is None or not callable(method):  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        return False
    try:
        method(*args, **kwargs)
    except Exception:
        return False
    return True


def _read_plan_artifact_func() -> _ReadPlanArtifactFn:
    module = import_module("ralph.display.artifact_reader")
    return cast("_ReadPlanArtifactFn", module.read_plan_artifact)


def _terminal_width() -> int:
    return shutil.get_terminal_size().columns or 80


def _available_width(prefix_len: int) -> int:
    return max(40, _terminal_width() - prefix_len - 2)


@dataclass(frozen=True)
class _ArtifactRenderCtx:
    workspace_root: Path
    display_context: DisplayContext
    display: ParallelDisplay | None
    verbosity: Verbosity
    ra: RequiredArtifact


def render_phase_artifact_handoff(
    phase: str,
    event: Event,
    workspace_root: Path,
    display: ParallelDisplay | None,
    ctx: ArtifactHandoffContext | None = None,
) -> None:
    """Render the artifact handoff panel after a phase completes."""
    _ctx = ctx or ArtifactHandoffContext()
    display_ctx = get_display_context(display, _ctx.display_context)
    effective_drain = _ctx.drain or phase
    required_artifact = (
        resolve_phase_required_artifact(
            _ctx.policy_bundle.pipeline,
            _ctx.policy_bundle.artifacts,
            phase=phase,
            drain=effective_drain,
        )
        if _ctx.policy_bundle is not None
        else None
    )

    if required_artifact is None:
        if event != PipelineEvent.AGENT_SUCCESS:
            return
        if _ctx.policy_bundle is not None:
            phase_def = _ctx.policy_bundle.pipeline.phases.get(phase)
            role = phase_def.role if phase_def is not None else None
            if role == "analysis":
                _emit_via_display(
                    display_ctx, "emit_analysis_decision", workspace_root, effective_drain
                )
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
        _emit_via_display(display_ctx, "emit_analysis_decision", workspace_root, effective_drain)
        return

    if event == PipelineEvent.AGENT_SUCCESS:
        _render_success_artifact(
            artifact_type,
            _ArtifactRenderCtx(
                workspace_root=workspace_root,
                display_context=display_ctx,
                display=display,
                verbosity=_ctx.verbosity,
                ra=required_artifact,
            ),
        )


def _render_success_artifact(artifact_type: str, ctx: _ArtifactRenderCtx) -> None:
    def _emit_close(produced: str) -> None:
        if ctx.verbosity != Verbosity.QUIET and hasattr(ctx.display, "record_artifact_outcome"):
            with suppress(Exception):
                cast("ParallelDisplay", ctx.display).record_artifact_outcome(produced)

    if artifact_type == "plan":
        _emit_via_display(ctx.display_context, "emit_plan_artifact", ctx.workspace_root)
        with suppress(Exception):
            plan = _read_plan_artifact_func()(ctx.workspace_root)
            produced = (
                f"{plan.total_steps} step(s), {len(plan.risks_mitigations)} risk(s)"
                if plan is not None
                else "(no plan artifact on disk)"
            )
            _emit_close(produced)
        return

    if artifact_type == "development_result":
        _emit_via_display(ctx.display_context, "emit_development_artifact", ctx.workspace_root)
        produced = (
            "result produced"
            if (ctx.workspace_root / ctx.ra.json_path).exists()
            else "no result artifact"
        )
        _emit_close(produced)
        return

    if artifact_type == "issues":
        _emit_via_display(ctx.display_context, "emit_review_artifact", ctx.workspace_root)
        with suppress(Exception):
            issue_count = _count_issues(ctx.workspace_root / ctx.ra.json_path)
            _emit_close(f"{issue_count} issue(s)")
        return

    if artifact_type == "fix_result":
        _emit_via_display(ctx.display_context, "emit_fix_artifact", ctx.workspace_root)
        _emit_close("applied")


def _count_issues(issues_path: Path) -> int:
    if not issues_path.exists():
        return 0
    try:
        issues_text = issues_path.read_text(encoding="utf-8")
        issues_data = cast("object", json.loads(issues_text))
        content_obj = (
            cast("dict[str, object]", issues_data).get("content")
            if isinstance(issues_data, dict)
            else issues_data
        )
        issues_list = (
            cast("dict[str, object]", content_obj).get("issues")
            if isinstance(content_obj, dict)
            else content_obj
        )
        return len(issues_list) if isinstance(issues_list, list) else 0
    except Exception:
        return 0


def stream_parsed_agent_activity(
    lines: Iterable[object],
    parser_type: str,
    agent_name: str,
    display: ParallelDisplay | None = None,
    *,
    agent_config: AgentConfig | None = None,
    **kwargs: object,
) -> None:
    """Stream and render parsed agent output lines.

    Accepts and forwards the per-invocation
    ``subagent_pid_registry=`` and ``subagent_source_label=`` kwargs
    into the resolved parser so the parser's structured-event hook
    registers any embedded PID into the shared registry (R1 / R5 of
    the Trustworthy Idle Watchdog spec). Both kwargs are optional;
    legacy callers continue to work without them.
    """
    transport = cast("AgentTransport | None", kwargs.get("transport"))
    display_context = cast("DisplayContext | None", kwargs.get("display_context"))
    raw_output_sink = cast("deque[str] | list[str] | None", kwargs.get("raw_output_sink"))
    rendered_output_sink = cast("deque[str] | list[str] | None", kwargs.get("rendered_output_sink"))
    session_id_sink = cast("Callable[[str], None] | None", kwargs.get("session_id_sink"))
    subagent_pid_registry = cast(
        "SubagentPidRegistry | None",
        kwargs.get("subagent_pid_registry"),
    )
    subagent_source_label = cast(
        "str | None",
        kwargs.get("subagent_source_label"),
    )

    if agent_config is not None:
        parser_key = resolve_parser_key(
            agent_config.cmd,
            agent_config.json_parser,
            cast("AgentTransport", agent_config.transport),
        )
    else:
        parser_key = (
            "claude_interactive" if transport == AgentTransport.CLAUDE_INTERACTIVE else parser_type
        )
    parser = _resolve_parser(
        parser_key,
        subagent_pid_registry=subagent_pid_registry,
        subagent_source_label=subagent_source_label,
    )

    def _iter_lines() -> Iterator[str]:
        for line in lines:
            text = str(line)
            if raw_output_sink is not None:
                raw_output_sink.append(text)
            session_id = extract_transport_session_id((text,))
            if session_id is not None and session_id_sink is not None:
                session_id_sink(session_id)
            yield text

    parallel_display_cls = _parallel_display_cls()
    subscriber = subscriber_for_display(display)
    emit_hook_raw: object = getattr(parser, "emit_subagent_activity", None)
    # Cache for the latest sanitized subagent summary so the
    # SUBAGENT_PROGRESS display event can re-use the exact string the
    # sink received (avoids re-sanitizing or re-emitting raw payload).
    last_subagent_summary: list[str] = []
    for parsed_line in parser.parse(_iter_lines()):
        # Forward parsed lines to the per-parser subagent sink so the
        # idle watchdog's per-channel evidence surface stays fresh for
        # ALL parsers (Claude, OpenCode, Codex, Gemini, Pi, Agy,
        # Generic, ClaudeInteractive).  The contextvar is bound by the
        # line readers (_process_reader / _pty_line_reader) before the
        # first yield so the sink reaches the per-run watchdog
        # closure.  The call is wrapped in try/except so a buggy
        # parser hook cannot crash the activity stream.
        if callable(emit_hook_raw):
            emit_hook = cast(
                "Callable[[AgentOutputLine, Callable[[str], None]], None]",
                emit_hook_raw,
            )
            last_subagent_summary.clear()
            try:
                _capture_summary_into(parsed_line, emit_hook, last_subagent_summary)
            except Exception:
                logger.debug("parser.emit_subagent_activity failed", exc_info=True)
        rendered = _render_agent_activity_line(parsed_line, agent_name)
        if rendered is not None and rendered_output_sink is not None:
            rendered_output_sink.append(rendered.plain)
        if isinstance(display, parallel_display_cls):
            kind = map_parser_type_to_kind(parsed_line.type)
            display.emit_parsed_event(
                agent_name, kind, parsed_line.content, parsed_line.metadata or {}
            )
            # emit_parsed_event already records a tool_use on the display's
            # subscriber; recording it again here would double-count the repeat
            # counter (a single call would render "(x2)"). Record only non-tool
            # lines here on the parallel path.
            record_on_subscriber = parsed_line.type != "tool_use"
        else:
            if rendered is not None:
                emit_activity_line(display, None, rendered.plain, display_context=display_context)
            record_on_subscriber = True
        if subscriber is not None and record_on_subscriber:
            _record_activity_on_subscriber(subscriber, parsed_line, rendered, agent_name)

        # Surface the sanitized subagent summary as a SUBAGENT_PROGRESS
        # event on the parallel display so the operator sees
        # real-time per-tool progress on the console transcript.  We
        # only fire when (a) we are using a parallel display, (b) the
        # parser hook emitted a summary for this line, and (c) the
        # summary is non-empty.  The summary was already sanitized by
        # the parser hook so no further sanitization is needed here.
        if isinstance(display, parallel_display_cls) and last_subagent_summary:
            summary = last_subagent_summary[0]
            try:
                display.emit_parsed_event(
                    agent_name,
                    ActivityEventKind.SUBAGENT_PROGRESS,
                    summary,
                    parsed_line.metadata or {},
                )
            except Exception:
                logger.debug(
                    "display.emit_parsed_event for SUBAGENT_PROGRESS failed",
                    exc_info=True,
                )


def _capture_summary_into(
    parsed_line: AgentOutputLine,
    emit_hook: Callable[[AgentOutputLine, Callable[[str], None]], None],
    sink_buffer: list[str],
) -> None:
    """Invoke the parser hook with a capturing sink that records the summary.

    Mirrors the activity-stream ``emit_subagent_activity`` invocation
    but records the emitted summary into ``sink_buffer`` so the
    parallel display can re-use the same sanitized string for the
    ``SUBAGENT_PROGRESS`` display event.  A buggy hook that raises
    is swallowed here too so the activity stream continues.
    """

    def _capturing_sink(summary: str) -> None:
        sink_buffer.append(summary)
        try:
            invoke_subagent_sink(summary)
        except Exception:
            return

    try:
        emit_hook(parsed_line, _capturing_sink)
    except Exception:
        return


def _record_activity_on_subscriber(
    subscriber: PipelineSubscriber,
    parsed_line: AgentOutputLine,
    rendered: Text | None,
    agent_name: str,
) -> None:
    try:
        if parsed_line.type == "thinking" and parsed_line.content.strip():
            line_text = parsed_line.content.strip()
        else:
            line_text = "" if rendered is None else rendered.plain
        metadata = parsed_line.metadata
        tool_name: str | None = None
        metadata_tool = metadata.get("tool")
        if isinstance(metadata_tool, str) and metadata_tool.strip():
            tool_name = metadata_tool.strip()
        elif parsed_line.type == "tool_use":
            stripped = parsed_line.content.strip()
            if stripped:
                tool_name = stripped
        path = _format_metadata_value(metadata.get("path")) or None
        workdir = _format_metadata_value(metadata.get("workdir")) or None
        command = _format_metadata_value(metadata.get("command")) or None
        subscriber.record_activity(
            unit_id=agent_name,
            line=line_text,
            agent_name=agent_name,
            tool_name=tool_name,
            path=path,
            workdir=workdir,
            command=command,
        )
    except Exception:
        logger.debug("subscriber.record_activity failed", exc_info=True)


def _resolve_parser(
    parser_type: str,
    *,
    subagent_pid_registry: SubagentPidRegistry | None = None,
    subagent_source_label: str | None = None,
) -> AgentParser:
    """Resolve a parser instance by ``parser_type``.

    R1 / R5 (Trustworthy Idle Watchdog spec): when the caller threads a
    shared ``SubagentPidRegistry`` plus a per-transport source label
    through this helper, the registry is forwarded into
    ``get_parser`` so the parser's structured-event handler can
    register any embedded PID into the registry. The registration
    flows back to ``ProcessMonitor.spawned_subagent_count()`` through
    the existing per-transport ``SubagentPidSource`` seam, so the
    watchdog sees real subagent PIDs as they appear in the agent's
    stream (defense-in-depth against the broader
    ``descendant_snapshot()`` count).

    Both kwargs are keyword-only and default to ``None`` so legacy
    callers (the smoke plumbing, commit plumbing) that invoke
    ``_resolve_parser(parser_type)`` continue to work without
    changes.
    """
    try:
        return get_parser(
            parser_type,
            subagent_pid_registry=subagent_pid_registry,
            subagent_source_label=subagent_source_label,
        )
    except ValueError:
        logger.warning("Unknown parser '{}'; falling back to generic", parser_type)
        return get_parser(
            "generic",
            subagent_pid_registry=subagent_pid_registry,
            subagent_source_label=subagent_source_label,
        )


def _truncate(text: str, max_length: int) -> str:
    if max_length <= 1 or len(text) <= max_length:
        return text
    return text[:max_length] + "…"


def _render_agent_activity_line(output: AgentOutputLine, agent_name: str) -> Text | None:
    """Render an agent event through the single registry.

    After the wt-028-display consolidation, this function is a thin
    adapter that delegates every presentation decision to
    :mod:`ralph.display.agent_event_renderer`. It maps the parser-shaped
    :class:`AgentOutputLine` to the canonical ``ActivityEventKind``
    (via the shared :func:`activity_router.map_parser_type_to_kind`),
    runs :func:`agent_event_renderer.render_event_kind_text` to
    produce a stable plain-text line (the same line the registry's
    ring-buffer / activity-router path consumes), and wraps the
    result in a :class:`rich.text.Text` so downstream callers that
    expect a styled Text keep working unchanged.

    The previous per-type helpers (``_render_text_line`` /
    ``_render_tool_use_line`` / ``_render_tool_result_line`` /
    ``_render_error_line`` / ``_render_metadata_event_line`` /
    ``_tool_input_summary`` / ``_metadata_summary`` / ``_kv_summary``
    / ``_styled_prefix``) were the pipeline runner's competing
    formatter; they have been deleted and this function is the
    single rendering seam on the pipeline-runnner side.
    """
    from ralph.display.agent_event_renderer import render_event_kind_text

    kind = map_parser_type_to_kind(output.type)
    plain = render_event_kind_text(
        kind,
        output.content or "",
        metadata=output.metadata or {},
        agent_name=agent_name,
    )
    if not plain:
        return None
    return Text(plain)


def _format_metadata_value(value: object) -> str | None:
    """Return the metadata value if it's a non-empty string, else ``None``.

    Used by :func:`_record_activity_on_subscriber` to extract clean
    string values for ``path`` / ``workdir`` / ``command`` slots.
    """
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    return None


# NOTE: the per-type render helpers ``_render_text_line``,
# ``_render_tool_use_line``, ``_render_tool_result_line``,
# ``_render_error_line``, ``_render_metadata_event_line``,
# ``_tool_input_summary``, ``_metadata_summary``, ``_kv_summary``,
# ``_styled_prefix`` were the pipeline runner's competing
# agent-output formatter. After the wt-028-display consolidation
# they are deleted; :mod:`ralph.display.agent_event_renderer` is
# the single source of truth for agent-event presentation
# decisions.


render_agent_activity_line = _render_agent_activity_line
record_activity_on_subscriber = _record_activity_on_subscriber
# ``truncate`` / ``available_width`` / ``terminal_width`` / ``MAX_*``
# were removed when the per-type render helpers were consolidated into
# the agent-event renderer registry. External callers should use
# :mod:`ralph.display.agent_event_renderer` directly instead.
truncate = _truncate
available_width = _available_width
terminal_width = _terminal_width
# Truncation limits exposed for backward compatibility with tests that
# assert the legacy ``MAX_TEXT_LENGTH`` / ``MAX_TOOL_RESULT_BRIEF``
# constants. The registry's own cell-aware truncation uses 200 cells
# by default (see ``_METADATA_SUMMARY_MAX_CHARS`` and the
# ``_truncate_to_cells`` helper); these aliases pin the historical
# values so the suite's outer surface stays unchanged.
MAX_TEXT_LENGTH = _MAX_TEXT_LENGTH
MAX_TOOL_RESULT_BRIEF = _MAX_TOOL_RESULT_BRIEF
MAX_METADATA_SUMMARY_LENGTH = _MAX_METADATA_SUMMARY_LENGTH


def metadata_summary(metadata: dict[str, object]) -> str:
    """Backwards-compatible metadata summary shim.

    Returns the registry's stable ``key=value, ...`` summary for the
    preferred metadata keys (status / summary / phase / decision /
    message / event / tool / path / workdir / command). Kept as a
    free function so the existing pipeline-runner test that asserts
    ``runner_module.metadata_summary`` still works.
    """
    from ralph.display.agent_event_renderer import _metadata_summary

    return _metadata_summary(metadata)
