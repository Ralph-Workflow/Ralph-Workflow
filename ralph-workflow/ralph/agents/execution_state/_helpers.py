from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.mcp.tools.coordination import PROGRESS_PIPELINE_MARKER
from ralph.process.child_liveness import classify_child_snapshot

from .agent_execution_state import AgentExecutionState

if TYPE_CHECKING:
    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import _LiveDescendantHandle


_AGENT_LABEL_PREFIX = "agent:"


_CLAUDE_LIFECYCLE_EVENTS = frozenset(
    {
        "message_start",
        "message_stop",
        "content_block_start",
        "content_block_stop",
        "message_delta",
        "assistant",
        "user",
        "system",
    }
)


def _non_blank_output_signal(line: str) -> AgentActivitySignal | None:
    if not line.strip():
        return None
    return AgentActivitySignal(AgentActivityKind.OUTPUT_LINE, raw=line)


def _error_message_from_error_field(obj: dict[str, object]) -> str:
    """Extract a stable error message from a top-level ``error`` event object.

    Handles ``error`` as a dict (``error.message``/``error.name``), a bare string
    (``error`` is the message), or a top-level ``message`` fallback.
    """
    error_obj = obj.get("error")
    if isinstance(error_obj, dict):
        inner = cast("dict[str, object]", error_obj)
        return str(inner.get("message", inner.get("name", "unknown error")))
    if isinstance(error_obj, str) and error_obj:
        return error_obj
    return str(obj.get("message", "unknown error"))


def _tool_state_error_message(obj: dict[str, object]) -> str | None:
    """Extract the error message from an opencode tool event whose state errored.

    An MCP tool-call failure surfaces NOT as a top-level ``{"type":"error"}`` line
    but as a ``tool_use``/``tool_result`` line whose ``part.state.status`` is
    ``"error"`` (mirrors ``OpenCodeParser._parse_tool_use``). Returns the error
    string, or None when the line is not a tool-state error.
    """
    part = obj.get("part")
    if not isinstance(part, dict):
        return None
    state = cast("dict[str, object]", part).get("state")
    if not isinstance(state, dict):
        return None
    state_dict = cast("dict[str, object]", state)
    if str(state_dict.get("status", "")) != "error":
        return None
    return str(state_dict.get("error", "tool error"))


def _error_output_signal(line: str) -> AgentActivitySignal | None:
    """Return an ERROR_LINE signal when the line is a JSON error event.

    Covers BOTH shapes an opencode error can take so the repeated-error circuit
    breaker sees the retry storm regardless of wire form:

    - a top-level ``{"type":"error", ...}`` event, and
    - a ``tool_use``/``tool_result`` event whose ``part.state.status == "error"``
      (how an MCP tool-call failure such as ``MCP error -32001: Request timed
      out`` actually surfaces).

    The signal's ``raw`` carries the extracted error message (not the full JSON
    envelope) so fingerprinting is stable across occurrences. Returns None for
    non-error or non-JSON lines so callers fall through to normal classification.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    event_type = str(obj.get("type", ""))
    if event_type == "error":
        return AgentActivitySignal(
            AgentActivityKind.ERROR_LINE, raw=_error_message_from_error_field(obj)
        )
    if event_type in {"tool_use", "tool_result"}:
        tool_error = _tool_state_error_message(obj)
        if tool_error is not None:
            return AgentActivitySignal(AgentActivityKind.ERROR_LINE, raw=tool_error)
    return None


_PROGRESS_STATUS = re.compile(r"status='([^']*)'")
_PROGRESS_NOTE = re.compile(r"note='([^']*)'")


def _progress_report_signal(line: str) -> AgentActivitySignal | None:
    """Return a PROGRESS_REPORT signal when the line echoes a report_progress result.

    Detected by the stable ``PROGRESS_PIPELINE_MARKER`` the coordination tool
    appends. The signal's ``raw`` carries only ``status``/``note`` (not the
    per-call timestamp) so the watchdog can tell a repeated cosmetic heartbeat
    from a genuinely changed status. Returns None for non-progress lines.
    """
    if PROGRESS_PIPELINE_MARKER not in line:
        return None
    status_match = _PROGRESS_STATUS.search(line)
    note_match = _PROGRESS_NOTE.search(line)
    status = status_match.group(1) if status_match else ""
    note = note_match.group(1) if note_match else ""
    return AgentActivitySignal(
        AgentActivityKind.PROGRESS_REPORT, raw=f"status={status} note={note}"
    )


_OPENCODE_CHILD_SPAWN_TYPES = frozenset({"child_started", "child.spawned"})
_OPENCODE_CHILD_PROGRESS_TYPES = frozenset(
    {"child_progress", "progress", "tool_call", "writing_artifact"}
)
_OPENCODE_CHILD_HEARTBEAT_TYPES = frozenset({"child_heartbeat", "heartbeat"})
_OPENCODE_CHILD_TERMINAL_TYPES = frozenset({"child_complete", "child_failed", "child.terminal"})


_OPENCODE_CHILD_KIND: dict[str, AgentActivityKind] = {
    **dict.fromkeys(_OPENCODE_CHILD_SPAWN_TYPES, AgentActivityKind.CHILD_PROCESS),
    **dict.fromkeys(_OPENCODE_CHILD_PROGRESS_TYPES, AgentActivityKind.CHILD_PROGRESS),
    **dict.fromkeys(_OPENCODE_CHILD_HEARTBEAT_TYPES, AgentActivityKind.CHILD_HEARTBEAT),
    **dict.fromkeys(_OPENCODE_CHILD_TERMINAL_TYPES, AgentActivityKind.CHILD_TERMINAL_ACK),
}


def _classify_opencode_child_signal(line: str) -> AgentActivitySignal | None:
    """Return a child-lifecycle ActivitySignal when the line is a recognised OpenCode event."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    event_type = str(obj.get("type", ""))
    kind = _OPENCODE_CHILD_KIND.get(event_type)
    if kind is None:
        return None
    return AgentActivitySignal(kind, raw=line)


# Cross-transport generic child-signal classifier.
#
# The OpenCode strategy uses the specialised ``_classify_opencode_child_signal``
# above (which understands the OpenCode wire format). For every other
# transport (Claude, Codex, Generic, Agy, Nanocoder) we run a STRICT
# classifier that recognises ONLY event names that explicitly carry a
# child- or subagent-scope marker. Bare provider-level event names
# (``progress``, ``tool_call``, ``task_progress``, ``heartbeat``,
# ``alive``) are NOT classified -- these are generic provider wire
# frames that appear in parent-level stdout and do NOT prove that a
# subagent is active. Treating bare frames as child activity was a
# false-positive deferral: a parent agent that emitted any
# ``{"type":"heartbeat"}`` frame during its own startup would be
# marked as alive, masking the short NO_OUTPUT_AT_START kill and the
# StuckClassifier path. The pre-fix behaviour was reproduced by
# forwarding both raw ``{"type":"heartbeat"}`` and
# ``{"type":"tool_call"}`` lines into the subagent sink in a
# ``uv run python`` probe (see
# ``tests/agents/execution_state/test_generic_child_signal.py``
# for the regression tests that pin the no-false-positive
# contract).
#
# The STRICT classifier recognises ONLY:
#
#   * child-scoped event names (``child_progress``, ``subagent_progress``,
#     ``child_heartbeat``, ``subagent_heartbeat``, ``child_alive``,
#     ``subagent_alive``) -> CHILD_PROGRESS / CHILD_HEARTBEAT.
#   * the ``child_`` / ``subagent_`` prefix family -> CHILD_PROGRESS.
#
# Plain-text markers (``[child]``, ``[subagent]``,
# ``subagent progress``, ``child progress``) classify
# as CHILD_PROGRESS; ``subagent heartbeat`` / ``child heartbeat``
# classify as CHILD_HEARTBEAT. The plain-text marker set is
# intentionally narrow: only markers at the START of the line with
# optional leading whitespace are recognised, so an arbitrary prose
# line like ``User wrote a YAML snippet: subagent: worker`` does NOT
# classify as child activity. Bare ``subagent: `` / ``child: ``
# substring matches are NOT classified (no leading marker).
#
# Terminal signals (``child_complete``, ``child_failed``,
# ``child_terminal``, ``subagent_complete``, ``subagent_failed``,
# ``subagent_cancelled``, ``subagent_terminal``, ``child_cancelled``)
# are NOT classified by the generic classifier -- terminal signals
# do not invoke the sink (same contract as OpenCode).
_GENERIC_CHILD_PROGRESS_KIND: frozenset[str] = frozenset(
    {
        "child_progress",
        "subagent_progress",
    }
)
_GENERIC_CHILD_HEARTBEAT_KIND: frozenset[str] = frozenset(
    {
        "child_heartbeat",
        "subagent_heartbeat",
        "child_alive",
        "subagent_alive",
    }
)
_GENERIC_CHILD_TERMINAL_KIND: frozenset[str] = frozenset(
    {
        "child_complete",
        "child_failed",
        "child_terminal",
        "child_cancelled",
        "subagent_complete",
        "subagent_failed",
        "subagent_cancelled",
        "subagent_terminal",
    }
)
_GENERIC_CHILD_SCOPE_PREFIXES: tuple[str, ...] = ("child_", "subagent_")
# Plain-text child-status markers. These are anchored at the START of
# the line (with optional leading whitespace) so an arbitrary prose
# line like ``User wrote a YAML snippet: subagent: worker`` does NOT
# classify as child activity. The marker set is intentionally narrow:
# only ``[child]``, ``[subagent]``, ``subagent progress``,
# ``child progress`` classify as CHILD_PROGRESS; ``subagent heartbeat``
# and ``child heartbeat`` classify as CHILD_HEARTBEAT. Bare
# ``progress`` / ``task progress`` / ``heartbeat`` / ``alive``
# plain-text lines are NOT classified (no explicit child scoping).
_GENERIC_PROGRESS_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*\[child\]", re.IGNORECASE),
    re.compile(r"^\s*\[subagent\]", re.IGNORECASE),
    re.compile(r"^\s*subagent progress\b", re.IGNORECASE),
    re.compile(r"^\s*child progress\b", re.IGNORECASE),
)
_GENERIC_HEARTBEAT_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*subagent heartbeat\b", re.IGNORECASE),
    re.compile(r"^\s*child heartbeat\b", re.IGNORECASE),
)


def _extract_event_kind_from_json(line: str) -> str | None:
    """Return the lowercased ``type`` / ``event`` value from a JSON envelope, or None.

    A best-effort extractor: returns None for non-JSON, non-dict, or
    empty inputs. The two keys ``type`` and ``event`` are tried in order
    so a single helper handles both OpenCode-style and Codex-style wire
    formats.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    raw_type = obj.get("type")
    if isinstance(raw_type, str) and raw_type:
        return raw_type.strip().lower()
    raw_event = obj.get("event")
    if isinstance(raw_event, str) and raw_event:
        return raw_event.strip().lower()
    return None


def _classify_generic_child_signal(line: str) -> AgentActivitySignal | None:
    """Cross-transport generic child-signal classifier (PLAN AC-08 contract).

    Returns an ``AgentActivitySignal`` for lines that carry a recognised
    cross-transport child-scope marker; returns ``None`` for empty /
    whitespace lines, regular stdout without markers, and terminal
    child events (which do not invoke the sink).

    The classifier recognises three families of markers so
    Claude / Codex / Generic / Agy / Nanocoder transports (which
    emit child-scope markers under their own format) still feed the
    watchdog's per-channel evidence surface per PLAN step 8. The
    classifier is intentionally STRICT: only explicit child- or
    subagent-scoped event names classify. Bare provider-level
    event names (``progress``, ``tool_call``, ``task_progress``,
    ``heartbeat``, ``alive``) are NOT classified -- they appear
    in parent-level stdout and do NOT prove that a subagent is
    active. See :func:`_classify_generic_child_signal_from_json`
    for the full false-positive contract.

    1. JSON envelopes whose ``type`` / ``event`` value is in
       :data:`_GENERIC_CHILD_PROGRESS_KIND` (e.g. ``child_progress``,
       ``subagent_progress``) -> CHILD_PROGRESS. Bare
       ``progress`` / ``tool_call`` / ``task_progress`` events are
       NOT classified (no child scoping).
    2. JSON envelopes whose ``type`` / ``event`` value is in
       :data:`_GENERIC_CHILD_HEARTBEAT_KIND` (e.g. ``child_heartbeat``,
       ``subagent_heartbeat``, ``child_alive``, ``subagent_alive``)
       -> CHILD_HEARTBEAT. Bare ``heartbeat`` / ``alive`` events are
       NOT classified (no child scoping).
    3. Any ``type`` / ``event`` value starting with the ``child_`` /
       ``subagent_`` prefix (e.g. ``subagent_progress_phase1``,
       ``child_progress_phase1``) -> CHILD_PROGRESS.
    4. Plain-text markers (anchored regex match at line start with
       optional leading whitespace) ``[child]``, ``[subagent]``,
       ``subagent progress``, ``child progress`` -> CHILD_PROGRESS;
       ``subagent heartbeat`` / ``child heartbeat`` -> CHILD_HEARTBEAT.
       Bare ``progress`` / ``task progress`` / ``heartbeat`` /
       ``alive`` plain-text lines (without the ``subagent`` /
       ``child`` qualifier) are NOT classified (no explicit
       child scoping).

    Terminal events (``child_complete``, ``child_failed``,
    ``child_terminal``, ``child_cancelled``, ``subagent_complete``,
    ``subagent_failed``, ``subagent_cancelled``, ``subagent_terminal``)
    return ``None`` (terminal signals do not invoke the sink,
    same contract as OpenCode).

    This classifier is purely additive on top of the existing
    ``_classify_opencode_child_signal`` specialised classifier.
    Transport-specific strategies continue to use the specialised
    classifier for their own wire format; the generic classifier is
    the safety net for transports that emit child-scope markers
    under a different format (Claude ``[child]``, Codex JSON
    ``event=child_progress`` or ``event=progress``, plain-text
    ``subagent heartbeat``).
    """
    if not line.strip():
        return None
    signal = _classify_generic_child_signal_from_json(line)
    if signal is not None:
        return signal
    return _classify_generic_child_signal_from_text(line)


def _classify_generic_child_signal_from_json(
    line: str,
) -> AgentActivitySignal | None:
    """Classify a JSON envelope by its ``type``/``event`` field (PLAN AC-08).

    Returns ``None`` for non-JSON, non-dict, or unknown type/event
    envelopes (the caller falls through to plain-text matching).
    Terminal events return ``None`` (terminal signals do not invoke
    the sink).

    Per the analysis-feedback fix, the classifier recognises ONLY
    explicit child- or subagent-scoped event names:

    * values in :data:`_GENERIC_CHILD_PROGRESS_KIND`
      (``child_progress``, ``subagent_progress``) -> CHILD_PROGRESS.
    * values in :data:`_GENERIC_CHILD_HEARTBEAT_KIND`
      (``child_heartbeat``, ``subagent_heartbeat``, ``child_alive``,
      ``subagent_alive``) -> CHILD_HEARTBEAT.
    * any value starting with the ``child_`` / ``subagent_`` prefix
      (e.g. ``subagent_progress_phase1``, ``child_progress_phase1``,
      ``subagent_heartbeat_extra``) -> CHILD_PROGRESS. The prefix
      match defaults to CHILD_PROGRESS because that is the safer
      interpretation -- an unknown child-scoped event name still
      means a child agent is producing output.

    Bare provider-level event names (``progress``, ``tool_call``,
    ``task_progress``, ``heartbeat``, ``alive``) are NOT classified
    -- they are generic provider wire frames (Gemini ``tool_call``
    in ``tests/test_parsers_1.py``, Generic ``heartbeat`` in
    ``tests/test_parsers_1.py``, Codex ``heartbeat`` in
    ``tests/test_parsers_1.py``) that appear in parent-level stdout
    and do NOT prove that a subagent is active. Pre-fix, the
    classifier forwarded these bare frames into the subagent sink
    via ``BaseExecutionStrategy.observe_line`` and refreshed
    ``record_subagent_work`` for genuine parent-level traffic,
    masking the short NO_OUTPUT_AT_START kill.
    """
    event_kind = _extract_event_kind_from_json(line)
    if event_kind is None:
        return None
    if event_kind in _GENERIC_CHILD_TERMINAL_KIND:
        return None
    if event_kind in _GENERIC_CHILD_HEARTBEAT_KIND:
        return AgentActivitySignal(AgentActivityKind.CHILD_HEARTBEAT, raw=line)
    if event_kind in _GENERIC_CHILD_PROGRESS_KIND:
        return AgentActivitySignal(AgentActivityKind.CHILD_PROGRESS, raw=line)
    if event_kind.startswith(_GENERIC_CHILD_SCOPE_PREFIXES):
        return AgentActivitySignal(AgentActivityKind.CHILD_PROGRESS, raw=line)
    return None


def _classify_generic_child_signal_from_text(
    line: str,
) -> AgentActivitySignal | None:
    """Classify a plain-text line by its child-scope marker tokens.

    Heartbeat markers take precedence over progress markers. The
    classifier matches the marker patterns (anchored at the start
    of the line with optional leading whitespace) so an arbitrary
    prose line that happens to mention ``subagent: `` or
    ``child: `` mid-sentence does NOT classify as child activity.

    The plain-text marker set is intentionally narrow: only
    ``[child]``, ``[subagent]``, ``subagent progress``,
    ``child progress`` (each at line start with optional
    whitespace) are recognised as CHILD_PROGRESS; ``subagent
    heartbeat`` and ``child heartbeat`` classify as
    CHILD_HEARTBEAT. Bare ``progress`` / ``task progress`` /
    ``heartbeat`` / ``alive`` plain-text lines (without the
    ``subagent`` / ``child`` qualifier) are NOT classified (no
    explicit child scoping). The JSON path handles the broader
    contract for those bare transport-level event names.
    """
    for pattern in _GENERIC_HEARTBEAT_MARKERS:
        if pattern.match(line):
            return AgentActivitySignal(AgentActivityKind.CHILD_HEARTBEAT, raw=line)
    for pattern in _GENERIC_PROGRESS_MARKERS:
        if pattern.match(line):
            return AgentActivitySignal(AgentActivityKind.CHILD_PROGRESS, raw=line)
    return None


def _route_opencode_line_to_registry(
    line: str,
    registry: ChildLivenessRegistry,
    scope_prefix: str,
) -> None:
    """Parse an OpenCode NDJSON line and route lifecycle events into the registry."""
    stripped = line.strip()
    if not stripped:
        return
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except json.JSONDecodeError:
        return
    if not isinstance(parsed, dict):
        return
    obj = cast("dict[str, object]", parsed)
    event_type = str(obj.get("type", ""))
    child_id = str(obj.get("child_id") or obj.get("id") or "")
    if not child_id:
        return
    if event_type in _OPENCODE_CHILD_SPAWN_TYPES:
        pid_raw = obj.get("pid")
        pid = int(pid_raw) if isinstance(pid_raw, int | float) else None
        registry.register_child(child_id, scope_prefix, pid=pid)
    elif event_type in _OPENCODE_CHILD_PROGRESS_TYPES:
        phase = str(obj.get("phase")) if obj.get("phase") is not None else None
        registry.record_progress(child_id, phase=phase)
    elif event_type in _OPENCODE_CHILD_HEARTBEAT_TYPES:
        registry.record_heartbeat(child_id)
    elif event_type in _OPENCODE_CHILD_TERMINAL_TYPES:
        terminal_state = str(obj.get("terminal_state", "complete"))
        registry.record_terminal_ack(child_id, terminal_state=terminal_state)


def _check_signals_terminal(completion_signals: CompletionSignals) -> bool:
    try:
        if completion_signals.terminal_ack_seen:
            return True
    except AttributeError:
        pass
    try:
        if completion_signals.artifact_optional:
            return True
    except AttributeError:
        pass
    try:
        if completion_signals.required_artifact_present:
            return True
    except AttributeError:
        pass
    # ``explicit_complete`` by itself is not authoritative: the plain-text
    # marker emitted by handle_declare_complete can be spoofed by ordinary
    # agent output. Require corroboration from either the completion sentinel
    # (written by the real declare_complete MCP tool) or a present artifact.
    try:
        if completion_signals.explicit_complete:
            try:
                if completion_signals.completion_sentinel_present:
                    return True
            except AttributeError:
                pass
            try:
                return bool(completion_signals.required_artifact_present)
            except AttributeError:
                pass
    except AttributeError:
        pass
    return False


def _os_descendant_state(
    handle: _LiveDescendantHandle,
    default: AgentExecutionState,
) -> AgentExecutionState:
    if hasattr(handle, "has_live_descendants"):
        try:
            if bool(handle.has_live_descendants()):
                return AgentExecutionState.WAITING_ON_CHILD
        except Exception:
            pass
    return default


def _probe_check_quiet(
    liveness_probe: LivenessProbe,
    probe_prefix: str,
) -> tuple[AgentExecutionState | None, bool]:
    """Returns (decided_state_or_none, scoped_evidence_stale)."""
    try:
        snap = liveness_probe.child_snapshot(probe_prefix)
        verdict = classify_child_snapshot(snap)
        if verdict.deferral_allowed:
            return AgentExecutionState.WAITING_ON_CHILD, False
        return None, snap.has_process
    except Exception:
        try:
            if bool(liveness_probe.any_agent_active(probe_prefix)):
                return AgentExecutionState.WAITING_ON_CHILD, False
        except Exception:
            pass
    return None, False


def _registry_check_for_exit(
    registry: ChildLivenessRegistry,
    label_prefix: str | None,
) -> tuple[AgentExecutionState | None, bool]:
    """Returns (decided_state_or_none, scoped_evidence_stale)."""
    scope_prefix = label_prefix if label_prefix is not None else ""
    had_scoped_records = registry.has_records(scope_prefix)
    try:
        reg_snap = registry.snapshot(scope_prefix)
        verdict = classify_child_snapshot(reg_snap)
        if verdict.all_children_terminal:
            return AgentExecutionState.TERMINAL_COMPLETE, False
        if verdict.deferral_allowed:
            return AgentExecutionState.WAITING_ON_CHILD, False
        return None, had_scoped_records
    except Exception:
        pass
    return None, False


def _probe_check_exit(
    liveness_probe: LivenessProbe,
    label_prefix: str | None,
) -> tuple[AgentExecutionState | None, bool]:
    """Returns (decided_state_or_none, scoped_evidence_stale)."""
    prefix = label_prefix if label_prefix is not None else ""
    try:
        snap = liveness_probe.child_snapshot(prefix)
        verdict = classify_child_snapshot(snap)
        if verdict.deferral_allowed:
            return AgentExecutionState.WAITING_ON_CHILD, False
        return None, snap.has_process
    except Exception:
        if label_prefix is not None:
            try:
                if bool(liveness_probe.any_agent_active(label_prefix)):
                    return AgentExecutionState.WAITING_ON_CHILD, False
            except Exception:
                pass
    return None, False


def _evidence_precedence(
    handle: _LiveDescendantHandle,
    completion_signals: CompletionSignals,
    liveness_probe: LivenessProbe | None,
    label_prefix: str | None,
    *,
    registry: ChildLivenessRegistry | None = None,
) -> AgentExecutionState:
    """Evidence-precedence exit classification.

    Priority:
      1. terminal_ack_seen or required_artifact_present -> TERMINAL_COMPLETE
      2. registry: all children acked with no remaining active -> TERMINAL_COMPLETE
      3. registry: deferral_allowed -> WAITING_ON_CHILD
      4. probe: deferral_allowed -> WAITING_ON_CHILD
      5. scoped Ralph child evidence exists but is stale -> RESUMABLE_CONTINUE
      6. OS descendants (only when no scoped Ralph evidence exists at all) -> WAITING_ON_CHILD
      7. else -> RESUMABLE_CONTINUE
    """
    if _check_signals_terminal(completion_signals):
        return AgentExecutionState.TERMINAL_COMPLETE

    stale = False

    if registry is not None:
        state, reg_stale = _registry_check_for_exit(registry, label_prefix)
        if state is not None:
            return state
        stale = reg_stale

    if liveness_probe is not None:
        state, probe_stale = _probe_check_exit(liveness_probe, label_prefix)
        if state is not None:
            return state
        stale = stale or probe_stale

    if stale:
        return AgentExecutionState.RESUMABLE_CONTINUE
    return _os_descendant_state(handle, AgentExecutionState.RESUMABLE_CONTINUE)


def _classify_claude_prefixed_line(stripped: str) -> AgentActivitySignal | None:
    if stripped.startswith("[claude]:"):
        return AgentActivitySignal(AgentActivityKind.LIFECYCLE, raw=stripped)
    if not (stripped.startswith("claude ") or stripped.startswith("claude/")):
        return None
    if " tool: " in stripped:
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=stripped)
    return AgentActivitySignal(AgentActivityKind.LIFECYCLE, raw=stripped)


def _classify_claude_json_object(
    obj: dict[str, object],
    raw: str,
) -> AgentActivitySignal:
    event_obj = obj.get("event")
    if obj.get("type") == "stream_event" and isinstance(event_obj, dict):
        return _classify_claude_json_object(cast("dict[str, object]", event_obj), raw)

    event_type = str(obj.get("type", ""))
    kind = _claude_activity_kind_for_event(event_type, obj)
    return AgentActivitySignal(kind, raw=raw)


def _claude_activity_kind_for_event(
    event_type: str,
    obj: dict[str, object],
) -> AgentActivityKind:
    if event_type == "content_block_delta":
        return AgentActivityKind.STREAM_DELTA
    if event_type == "content_block_start":
        return _claude_activity_kind_for_content_block(obj)
    if event_type in {"tool_use", "mcp_tool_call"}:
        return AgentActivityKind.TOOL_USE
    if event_type in {"tool_result", "mcp_tool_result"}:
        return AgentActivityKind.TOOL_RESULT
    if event_type in _CLAUDE_LIFECYCLE_EVENTS:
        return AgentActivityKind.LIFECYCLE
    return AgentActivityKind.OUTPUT_LINE


def _claude_activity_kind_for_content_block(obj: dict[str, object]) -> AgentActivityKind:
    content_block = obj.get("content_block")
    if isinstance(content_block, dict):
        block_type = str(content_block.get("type", ""))
        if block_type == "tool_use":
            return AgentActivityKind.TOOL_USE
        if block_type == "tool_result":
            return AgentActivityKind.TOOL_RESULT
    return AgentActivityKind.LIFECYCLE
