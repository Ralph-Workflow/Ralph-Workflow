"""Idle watchdog for detecting stalled agents.

Two-State Invariant
-------------------

The watchdog is one half of the recovery contract; the recovery
controller is the other. The pipeline can only enter TWO recovery
states; no third state is allowed:

  1. **Exponential backoff to the next agent** -- driven by
     ``AgentUnavailabilityTracker.mark_unavailable`` in
     ``ralph/recovery/agent_unavailability_tracker.py``. The current
     agent is marked unavailable for a per-reason backoff; the chain
     advances to the next agent whose cooldown has expired. The
     ``wrap=True`` re-arming in
     ``RecoveryController._next_available_agent_index`` reconsiders
     earlier agents whose cooldown has expired.

  2. **Retry with the same agent** -- driven by
     ``AgentChain.record_retry``. The same agent is retried in-place
     (chain.retries is incremented; the budget is debited but the
     chain index does not advance).

The watchdog contributes to state (1) only indirectly: when the
watchdog fires and the controller classifies the failure as
unavailable, the tracker applies the per-reason backoff. The
watchdog contributes to state (2) when it fires and the controller
classifies the failure as retryable.

Hard rules
----------

  - The watchdog NEVER calls ``sys.exit``, ``os._exit``, or
    ``raise SystemExit``. The run loop owns the exit decision.
  - The watchdog NEVER marks an agent as permanently unavailable.
    Every fire reason is transient; the cooldown math is owned by
    ``AgentUnavailabilityTracker`` and the only way for an agent to
    leave the unavailable set is for the cooldown to expire.
  - Every non-absolute fire is gated by the ``StuckClassifier``
    (``_stuck_classifier.py``) returning ``StuckKind.STUCK``. The
    absolute ``SESSION_CEILING_EXCEEDED`` reason is the ONLY reason
    that bypasses the gate (it is an operator-set hard cap, not a
    stuck-detection signal). Every other reason -- including
    ``CHILDREN_PERSIST_TOO_LONG`` -- is gated: the watchdog consults
    ``classify_stuck`` and returns CONTINUE for any non-STUCK kind
    so a productive session that has not yet been classified as
    "stuck" is not killed.
  - The watchdog is the sole owner of in-stream fire decisions;
    ``PostExitWatchdog`` is the sole owner of post-exit fire
    decisions. The import-time assertion on
    ``WatchdogFireReason.__members__`` (below) locks the enum set
    so a future PR cannot silently widen or narrow the fire set
    without updating the watchdog owner.

Channel freshness gate
----------------------

The ``evaluate()`` method consults a per-channel evidence summary
before returning ``WatchdogVerdict.FIRE``. A fire is deferred
(``WatchdogVerdict.CONTINUE``) when any of the following are true:

  - ``state.is_waiting_state`` is True (the pipeline has already
    committed to a wait -- this is the strongest signal and is
    checked first).
  - The connectivity monitor reports ``offline``.
  - A first-party channel (``mcp_tool`` or ``subagent_output``) is
    fresher than ``activity_evidence_ttl_seconds``.
  - The subagent-liveness side-channel is fresh.
  - The ``classify_quiet`` strategy returns ``WAITING_ON_CHILD`` or
    ``RESUMABLE_CONTINUE`` (these branches are evaluated by the
    live ``classify_quiet`` callable the watchdog receives from
    ``evaluate()`` -- the watchdog stores the most recent callable
    in ``self._classify_quiet_provider`` so the gate can consult it
    on every ``_classify_stuck_now`` call).

The classifier is a deterministic 7-kind enum (THINKING, LOADING,
WAITING_ON_CONNECTIVITY, TRANSITIONING, STUCK, DUPLICATE_KILL,
SILENT_SUBAGENT) and is a pure function of its inputs.
See ``_stuck_classifier.py`` for the full contract.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog._evidence_tier import (
    CHANNEL_DEFERS_BY_DEFAULT,
    CHANNEL_TIERS,
    ChannelEvidenceSummary,
    ChannelName,
    EvidenceSummary,
    EvidenceTier,
)
from ralph.agents.idle_watchdog._stuck_classifier import StuckKind, classify_stuck
from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.process.child_liveness import AliveBy

from .corroboration_snapshot import (
    CorroborationSnapshot,
    WaitingCorroborator,
)
from .repetition_tracker import RepetitionTracker
from .waiting_status_event import WaitingStatusEvent, WaitingStatusListener
from .waiting_status_kind import WaitingStatusKind
from .watchdog_fire_reason import WatchdogFireReason
from .watchdog_verdict import WatchdogVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.clock import Clock
    from ralph.process.monitor import ProcessMonitor, SubagentOutputCapture

    from .timeout_policy import TimeoutPolicy


# Lock the WatchdogFireReason enum set. IdleWatchdog is the sole owner of
# in-stream fire decisions; PostExitWatchdog is the sole owner of post-exit
# fire decisions. Any future addition (or removal) of a reason requires
# updating this assertion AND the watchdog owner's classification logic
# so a future PR cannot silently widen (or narrow) the fire set.
_EXPECTED_FIRE_REASONS: frozenset[str] = frozenset(
    {
        WatchdogFireReason.NO_OUTPUT_DEADLINE.value,
        WatchdogFireReason.NO_OUTPUT_AT_START.value,
        WatchdogFireReason.STALLED_AFTER_TOOL_RESULT.value,
        WatchdogFireReason.REPEATED_ERROR_LOOP.value,
        WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL.value,
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG.value,
        WatchdogFireReason.NO_PROGRESS_QUIET.value,
        WatchdogFireReason.STRICTLY_STUCK.value,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED.value,
        WatchdogFireReason.PROCESS_EXIT_HANG.value,
        WatchdogFireReason.DESCENDANT_HANG.value,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER.value,
    }
)
_actual = frozenset(member.value for member in WatchdogFireReason.__members__.values())
if _actual != _EXPECTED_FIRE_REASONS:
    missing = _EXPECTED_FIRE_REASONS - _actual
    extra = _actual - _EXPECTED_FIRE_REASONS
    msg = (
        "WatchdogFireReason.__members__ drifted from the IdleWatchdog owner's"
        " allowlist. The watchdog owner is the single source of truth for"
        " fire decisions. Missing:"
        f" {sorted(missing)}; extra: {sorted(extra)}."
        " Update BOTH this assertion AND the watchdog owner's classification"
        " logic so the fire decision is consistent with the new enum set."
    )
    raise RuntimeError(msg)


_SUBAGENT_DESCRIPTION_MAX = 200


# Control characters that must NEVER reach operator-visible waiting-status
# text: newline/CR (would split a single line into many in the UI),
# backspace / form-feed / vertical tab (corrupt rendering), DEL,
# ANSI CSI introducer (ESC [ ... letter), and C0 control codes. The
# pattern also strips raw escape characters and the OSC introducer.
# Tab (\x09) is also stripped because raw provider lines frequently
# contain literal tabs from indented JSON or quoted multiline strings
# that would otherwise render as unpredictable spacing in the UI.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]|\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


# Marker prefixes that almost always precede sensitive payload (a tool
# argument, a file path under a sensitive root, a prompt fragment the
# model is repeating verbatim). Stripping the value after these markers
# means the operator still sees WHICH category of subagent activity
# happened (tool, file read, prompt echo) without leaking the content.
#
# Patterns chosen to match provider-specific output frames whose value
# is potentially sensitive:
#   * ``"arguments": "<value>"`` -- JSON-encoded tool arguments
#   * ``"file_path": "<path>"`` -- Gemini/Claude file-path field
#   * ``"input": "<value>"`` -- Gemini input envelope
#   * ``"prompt": "<value>"`` -- echoed prompt fragment
#   * ``"content": "<value>"`` -- content fragment
#   * ``/etc/<path>``, ``/proc/<path>``, ``/sys/<path>`` -- sensitive roots + content
#   * ``/root/<path>``, ``~/.ssh/<path>`` -- private homes + content
#   * ``Authorization: Bearer <token>`` (case-insensitive) -- bearer token leakage
#     (rest of line redacted). The ``(?i:authorization)`` / ``(?i:bearer)``
#     inline flags cover ``authorization: bearer``, ``Authorization: Bearer``,
#     ``AUTHORIZATION: BEARER``, ``authorization:BEARER``, and any mixed-case
#     variant; case-sensitive regexes previously missed lowercase
#     ``authorization: bearer SECRET123`` and let the token leak into the
#     operator-visible subagent_activity field.
#   * ``-----BEGIN ... PRIVATE KEY-----`` -- PEM private key fragments (rest of line redacted)
#
# The JSON-quoted variants use ``"(?:[^"\\\n]|\\.)*"`` so the entire
# JSON string value is consumed, INCLUDING any escaped quotes (``\"``)
# or other JSON escape sequences (``\n``, ``\t``, ``\\``, ``\u00ff``,
# etc.). The pattern handles inputs like:
#   * ``"arguments": "secret"``               -> redacted as ``<redacted>``
#   * ``"arguments": "secret\"tail"``         -> redacted as ``<redacted>``
#     (the trailing ``tail"`` would otherwise leak)
#   * ``"prompt": "line1\nline2"``            -> redacted as ``<redacted>``
#   * ``"content": "say \"hi\""``             -> redacted as ``<redacted>``
# Without the ``\\.`` alternation, the inner ``[^"\n]*`` would stop at
# the first escaped quote and the rest of the line would reach
# operator-visible waiting-status output verbatim -- the analysis
# feedback that motivated the fix.
#
# The bare-path variants consume the rest of the line so ``/etc/passwd``
# becomes ``<redacted>``.
_SENSITIVE_PATH_TOKEN_RE = re.compile(
    r"""
    (?:/etc/|/proc/|/sys/|/root/|~/\.ssh/)[^\s\x1b\n]*
    |
    (?i:authorization)\s*:\s*(?i:bearer)[^\n]*
    |
    -----BEGIN\s+[A-Z ]*PRIVATE\s+KEY-----[^\n]*
    """,
    re.VERBOSE,
)


# Fallback pattern for malformed JSON where the value contains
# unescaped quotes. The strict pattern
# ``"(?:[^"\\\n]|\\.)*"`` requires the value to be a well-formed
# JSON string (closing ``"`` after a sequence of non-quote /
# non-backslash characters OR an escape sequence). When the value
# contains an UNESCAPED inner quote, the strict pattern stops at
# the first unescaped quote and leaves the rest of the value
# visible (e.g. ``{"arguments": "secret"tail"}`` -> redacts
# only ``{"arguments": "secret"`` and leaves ``tail"}`` visible).
#
# The fallback matches the marker, opening quote, and EVERYTHING
# up to a sensible boundary: closing quote, comma, brace,
# bracket, or newline. The non-greedy ``*?`` with the trailing
# positive-lookahead ``(?=["\\,\}\]\n]|$)`` ensures the match
# stops at the FIRST boundary character so the redacted text
# does not consume JSON structural characters.
#
# ``(?i)`` makes the key names case-insensitive so mixed-case
# provider keys such as ``Prompt`` / ``Arguments`` / ``Input`` /
# ``Content`` are redacted exactly like their lowercase variants.
_SENSITIVE_MARKER_FALLBACK_RE = re.compile(
    r"""
    "(?i:arguments|args|file_path|input|prompt|content)"\s*:\s*"
    .*?
    (?=[,\}\]\n]|$)
    """,
    re.VERBOSE | re.DOTALL,
)


# JSON keys whose value is treated as sensitive in raw provider
# lines. Used by ``_redact_json_values`` to walk a parsed JSON
# structure and replace matching values with ``<redacted>``.
#
# The set MUST include ``args`` (alongside ``arguments``) so that
# tool-call payloads using the JSON-RPC / OpenAI-style ``args`` key
# (e.g. ``{"name":"bash","args":{"command":"rm -rf /","token":"abc"}}``)
# have the ENTIRE value replaced with ``<redacted>``. Pre-fix the
# set listed only ``arguments``; ``args`` payloads leaked tool
# arguments (command, secret tokens) into operator-visible
# ``subagent_activity`` and waiting-status output. The full-value
# replacement rule (no recursive walk) ensures non-sensitive sibling
# fields cannot leak either -- a sensitive key whose value is a
# nested object or list is fully redacted.
#
# Key lookup is case-insensitive so mixed-case provider keys such as
# ``Prompt`` / ``Arguments`` / ``Input`` / ``Content`` are redacted
# exactly like their lowercase variants.
_SENSITIVE_JSON_KEYS: frozenset[str] = frozenset(
    {"arguments", "args", "file_path", "input", "prompt", "content"}
)


def _redact_json_values(obj: object) -> object:
    """Walk a parsed JSON structure and redact sensitive key values.

    When a key is sensitive (``arguments``, ``file_path``, ``input``,
    ``prompt``, ``content``) the ENTIRE value is replaced with the
    literal string ``<redacted>`` regardless of whether that value
    is a scalar, an object, or a list. This is the analysis-feedback
    fix: a sensitive key whose value is a nested object or list
    (e.g. ``{"arguments": {"command": "rm -rf /", "token": "abc"}}``)
    must NOT have its value walked recursively -- a recursive walk
    would still leak the non-sensitive sibling fields (``command`` in
    the example) into operator-visible waiting-status output.

    The replacement is a JSON-valid string so the surrounding JSON
    structure remains well-formed after redaction.
    """
    if isinstance(obj, dict):
        result: dict[str, object] = {}
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_JSON_KEYS:
                result[key] = "<redacted>"
            else:
                result[key] = _redact_json_values(value)
        return result
    if isinstance(obj, list):
        return [_redact_json_values(item) for item in obj]
    return obj


def _sanitize_subagent_description(line: str) -> str:
    """Return a safe operator-visible summary of a subagent observation.

    The watchdog receives raw provider lines via
    ``IdleWatchdog.record_subagent_work(description=line)`` from the
    subprocess and PTY readers. The raw line can contain tool
    arguments, file paths, prompt fragments, ANSI escapes, or
    control characters that must NOT be echoed verbatim into the
    waiting-status UI / log / breadcrumbs (operators may be on a
    shared terminal or sharing log output with non-engineers).

    Sanitization is intentionally conservative -- it strips
    anything that looks sensitive and truncates the result -- so a
    leaked payload never reaches operator-visible text. The
    truncated prefix still gives the operator a useful hint
    ("agent invoked a tool", "agent read a file under /etc",
    "agent echoed a prompt fragment") without echoing the
    payload itself.

    Returns an empty string when the sanitized text is empty or
    only whitespace.

    Implementation note: the sanitizer applies a multi-pass
    redaction so a single line that mixes well-formed JSON,
    malformed JSON, and free-form text is fully redacted:

    1. JSON structural pass: if the line parses as a JSON object
       or array, walk the structure and redact sensitive key
       values (``arguments``, ``file_path``, ``input``,
       ``prompt``, ``content``). The structural walker handles
       escaped quotes correctly because it uses the JSON
       parser.

    2. Strict regex pass: re-apply the well-formed JSON regex
       (``_SENSITIVE_MARKER_RE``) which matches
       ``"key": "value"`` patterns with proper escaping. This
       catches any sensitive markers that survived the JSON pass
       (e.g. multiple objects on the same line, or trailing text
       after a JSON object).

    3. Fallback regex pass: apply the unescaped-quote fallback
       (``_SENSITIVE_MARKER_FALLBACK_RE``) to catch malformed
       JSON values that contain unescaped inner quotes. This is
       the analysis-feedback fix: ``{"arguments": "secret"tail"}``
       must redact the entire ``secret"tail`` value, not just
       ``secret``.
    """
    if not line:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub("", line)
    cleaned = _redact_json_fragments(cleaned)
    cleaned = _SENSITIVE_MARKER_FALLBACK_RE.sub("<redacted>", cleaned)
    cleaned = _SENSITIVE_PATH_TOKEN_RE.sub("<redacted>", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > _SUBAGENT_DESCRIPTION_MAX:
        cleaned = cleaned[:_SUBAGENT_DESCRIPTION_MAX]
    return cleaned


_JSON_DECODER = json.JSONDecoder(strict=False)


def _decode_json_at(text: str, pos: int) -> tuple[object, int]:
    """Parse a JSON object/array starting at ``pos`` and return ``(value, end_offset)``.

    On parse failure, returns ``(None, -1)`` so the caller can fall
    through to character-level emission. The wrapper exists to
    give mypy a typed return value -- ``json.JSONDecoder.raw_decode``
    is annotated ``tuple[Any, int]`` in the standard library, and
    bare use would require a per-call ``cast``.
    """
    try:
        decoded = cast("tuple[object, int]", _JSON_DECODER.raw_decode(text, pos))
    except (json.JSONDecodeError, ValueError):
        return None, -1
    value, end = decoded
    return value, end


def _redact_json_fragments(text: str) -> str:
    """Walk ``text`` and redact every JSON object/array it contains.

    Lines reaching the watchdog from raw provider output frequently
    mix free-form text with one or more embedded JSON fragments
    (``prefix {"a":1} middle {"arguments": {"token":"abc"}} suffix``).
    A scanner that only inspects lines starting with ``{`` / ``[``
    misses fragments embedded after a textual prefix, and a regex
    fallback that stops at the first comma or brace leaks the
    remainder of a comma-bearing or nested-object value.

    The robust fix is to scan the line and try to parse a JSON
    object or array starting at every ``{`` / ``[`` byte. On a
    successful parse the structural walker (``_redact_json_values``)
    replaces the entire value for every sensitive key with
    ``<redacted>`` so nested objects / lists are redacted in full
    -- the surrounding JSON structure stays well-formed. On a parse
    failure the scanner moves past the byte and tries again at the
    next ``{`` / ``[``.

    This is the analysis-feedback fix for the comma-bearing
    ``prefix {"prompt": "hello, world"}`` case and the
    prefix-prefixed nested-object case
    ``prefix {"name": "tool", "arguments": {...}}``.
    """
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in {"{", "["}:
            parsed, end = _decode_json_at(text, i)
            if parsed is not None and isinstance(parsed, (dict, list)) and end > i:
                try:
                    redacted_obj = _redact_json_values(parsed)
                    redacted_text = json.dumps(redacted_obj, ensure_ascii=False)
                except (TypeError, ValueError):
                    out.append(text[i])
                    i += 1
                    continue
                out.append(redacted_text)
                i = end
                continue
        out.append(ch)
        i += 1
    return "".join(out)


# Fresh AliveBy states -- the only states that should defer the short
# NO_OUTPUT_AT_START kill. Every other AliveBy value (including None)
# describes either a stale signal or no signal at all.
#
# The set is consumed by ``_alive_by_is_fresh`` which is consulted from
# ``_evaluate_no_output_at_start``. Pre-fix the deferral gate was
# ``corroboration.alive_by is not None`` which deferred on every
# AliveBy value including stale states -- a wedged startup that
# reported ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` (or one of the other
# stale states) would defer the short kill and never reach
# ``_gate_fire`` / StuckClassifier. The fresh-evidence subset is
# ``FRESH_PROGRESS`` and ``FRESH_HEARTBEAT_ONLY`` -- both describe a
# child that has produced a recent progress / heartbeat signal.
#
# The stale states that DO NOT defer:
#   * ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` -- the agent has a process
#     tree descendant but no recent progress or heartbeat. This is
#     the classic wedged-startup signal: the orchestrator is
#     blocked on an unrelated long-lived process (e.g. an MCP server,
#     a Playwright browser) but the AGENT itself is not producing
#     output. The watchdog MUST still fire NO_OUTPUT_AT_START so the
#     agent is killed and the parent process is not stuck forever.
#   * ``CPU_IDLE_WHILE_ALIVE`` -- the descendant process is alive but
#     has not used CPU recently. Same wedged-startup pattern.
#   * ``LOG_STALE_WHILE_ALIVE`` -- the descendant's log output is
#     stale. Same wedged-startup pattern.
#   * ``STALE_LABEL_ONLY`` -- the child has no fresh heartbeat or
#     progress and is past the stale_label_ttl grace window. The
#     label is the only evidence left, and it is stale.
_FRESH_ALIVE_BY_STATES: frozenset[AliveBy] = frozenset(
    {AliveBy.FRESH_PROGRESS, AliveBy.FRESH_HEARTBEAT_ONLY}
)


def _alive_by_is_fresh(alive_by: AliveBy | None) -> bool:
    """Return True when ``alive_by`` describes a TRULY live child agent.

    The fresh states are ``FRESH_PROGRESS`` and ``FRESH_HEARTBEAT_ONLY``
    -- both describe a child that has produced a recent
    progress / heartbeat signal. Every other ``AliveBy`` value
    (including ``None``) is NOT fresh: the corroborator either
    reported a stale signal or no signal at all.

    The watchdog consults this helper from
    ``_evaluate_no_output_at_start`` so the live-corroboration
    deferral gate only suppresses ``NO_OUTPUT_AT_START`` for
    FRESH evidence. Stale evidence falls through to ``_gate_fire``
    and the StuckClassifier so a wedged startup that reports
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` (or one of the other
    stale states) is still killed by the short-fire path.
    See ``TestNoOutputAtStartStaleAliveByDoesNotDefer`` in
    ``tests/agents/test_idle_watchdog_no_output_at_start_lifecycle.py``
    for the regression test that pins this contract.
    """
    return alive_by in _FRESH_ALIVE_BY_STATES


@dataclass
class IdleWatchdog:
    """Tracks agent idle time and decides when to fire the timeout.

    The watchdog owns the last_activity timestamp; the caller's loop must NEVER
    mutate `_last_activity` directly. Activity must flow through `record_activity()`,
    which preserves the cumulative WAITING_ON_CHILD ceiling while advancing the
    idle baseline. Direct resets here previously caused a false-negative bug where
    WAITING_ON_CHILD deferred the deadline forever.

    Cumulative WAITING_ON_CHILD time is an absolute ceiling that is preserved across
    every transition (heartbeat activity, drain windows, classify_quiet outcomes).
    Once recorded, cumulative time never decays during the session — this mirrors
    max_session_seconds semantics so neither ceiling can be defeated by a process
    that alternates between producing output and waiting on children.

    The session ceiling (max_session_seconds) is checked first on every evaluate()
    call and cannot be defeated by activity — record_activity() does not reset it.

    Status events are emitted via the optional listener.

    - ENTERED once when WAITING_ON_CHILD deferral begins.
    - PROGRESS at most once per waiting_status_interval_seconds (rate-limited).
    - SUSPECTED_FROZEN once per WAITING run when suspect threshold is crossed.
    - EXITED when transitioning out of WAITING_ON_CHILD.
    - HARD_STOP immediately before returning FIRE for CHILDREN_PERSIST_TOO_LONG.

    Listener exceptions are caught and logged at DEBUG; they never propagate.

    Per-channel activity evidence (NEW): the watchdog tracks three non-stdout
    channels in addition to the stdout baseline.

      - mcp_tool: MCP tools/call invocations/completions routed via the
        Ralph MCP server. Updated by ``record_mcp_tool_call``.
      - subagent: subagent progress signals (heartbeat, phase change) routed
        from the opencode child_liveness registry. Updated by
        ``record_subagent_work``.
      - workspace: workspace file change events captured by
        WorkspaceMonitor. Updated by ``record_workspace_event``, which is
        invoked by the readers' ``on_event`` callback passed to
        ``WorkspaceMonitor.set_on_event`` (the monitor is constructed in
        ``invoke_agent`` before the per-run watchdog exists, so the
        readers register the callback on the monitor immediately after
        the watchdog is created in ``read_lines``; the binding is
        cleared in the ``finally`` block so a stale callback can never
        fire after the run ends).

    The three recorders do NOT touch ``_last_activity`` (the stdout baseline);
    the existing "stdout only resets idle baseline" invariant is preserved.
    Instead, they update per-channel ``_last_at`` timestamps and counters. The
    verdict hook in ``evaluate()`` defers a NO_OUTPUT_DEADLINE fire when ANY
    non-stdout channel is fresher than ``activity_evidence_ttl_seconds``,
    returning CONTINUE with a debug log. Absolute ceilings
    (SESSION_CEILING_EXCEEDED, CHILDREN_PERSIST_TOO_LONG) are checked before
    the deferral hook and remain absolute.
    """

    _config: TimeoutPolicy
    _clock: Clock
    _last_activity: float = field(init=False)
    _session_started_at: float = field(init=False)
    _last_meaningful_output_at: float | None = field(default=None, init=False)
    _has_meaningful_output: bool = field(default=False, init=False)
    _invocation_started_at: float | None = field(default=None, init=False)
    _waiting_on_child_started_at: float | None = field(default=None, init=False)
    _cumulative_waiting_on_child_seconds: float = field(default=0.0, init=False)
    # STRICTLY_STUCK run counter. Set when the corroborator reports an
    # alive_by in the strictly-stuck set so the next call can compute
    # the elapsed run. Reset to None on transitions OUT of the strictly-
    # stuck alive_by set so a brief liveness gap does not accumulate.
    _strictly_stuck_run_started_at: float | None = field(default=None, init=False)
    _in_drain_window: bool = field(default=False, init=False)
    _drain_started_at: float | None = field(default=None, init=False)
    _last_fire_reason: WatchdogFireReason | None = field(default=None, init=False)
    # The ``StuckKind`` the gate used to defer the most recent
    # would-be fire.  ``None`` when the watchdog has not deferred a
    # fire yet OR when the most recent fire actually fired (the
    # kind is only set when ``_gate_fire`` returns CONTINUE).  The
    # field is the runtime surface for the SILENT_SUBAGENT
    # diagnostic described in AC-05: the watchdog's
    # ``last_fire_reason`` property collapses every non-FIRE
    # deferral to ``DEFERRED_BY_STUCK_CLASSIFIER``, but
    # ``last_deferred_kind`` retains the precise kind (e.g.
    # ``StuckKind.SILENT_SUBAGENT``) so an operator can see WHY a
    # would-be fire was deferred ("a subagent dispatched then went
    # silent for >180s").
    _last_deferred_kind: StuckKind | None = field(default=None, init=False)
    # Per-(fire_reason, deferred_kind) log throttle map. The PROMPT log showed
    # ~10 DEBUG records/sec at ``_gate_fire:949`` while a fire was deferred
    # (SILENT_SUBAGENT or generic non-STUCK kind) -- per-tick log spam. The
    # map keys on ``(fire_reason.value, deferred_kind.value)`` and stores the
    # monotonic timestamp of the most recent emission so a subsequent call
    # within ``watchdog_log_throttle_seconds`` is suppressed. Reset to empty
    # in ``record_invocation_start`` so a new invocation starts with an empty
    # throttle map; the throttle MUST survive long-lived WAITING runs but
    # MUST NOT carry state across invocations.
    _last_deferred_log_at: dict[tuple[str, str], float] = field(
        default_factory=dict, init=False
    )
    # Corroborator's alive_by signal at the moment of the most recent
    # NO_PROGRESS_QUIET fire. ``None`` when the watchdog has not fired
    # yet OR when the most recent fire was not NO_PROGRESS_QUIET
    # (other fire helpers do not capture alive_by because the
    # live-child vs dead-child differentiation only matters for the
    # NO_PROGRESS_QUIET path). Surfaced via ``last_alive_by`` and
    # consumed by ``IdleWatchdogKilledError.child_alive`` so the
    # failure classifier can read the live-child signal end-to-end
    # via the typed exception's ``__cause__`` chain.
    _last_alive_by: AliveBy | None = field(default=None, init=False)
    _last_waiting_status_at: float | None = field(default=None, init=False)
    _suspicion_announced_for_run: bool = field(default=False, init=False)
    # Post-tool-result progression state. The watchdog tracks when a
    # TOOL_RESULT activity was last recorded and whether we are still
    # waiting for the follow-up STREAM_DELTA/OUTPUT_LINE activity. When
    # ``_awaiting_post_tool_result_progression`` is True and the
    # configured ``post_tool_result_progression_seconds`` budget elapses
    # without a follow-up activity, the watchdog fires
    # STALLED_AFTER_TOOL_RESULT. This is a NEW BEHAVIOR: pre-fix, the
    # watchdog only fired NO_OUTPUT_DEADLINE at the full idle timeout,
    # which let the post-tool-result wedge linger for ~300s.
    _last_tool_result_at: float | None = field(default=None, init=False)
    _awaiting_post_tool_result_progression: bool = field(default=False, init=False)
    # Per-channel activity evidence state (NEW). The three recorders
    # ``record_mcp_tool_call``, ``record_subagent_work``, and
    # ``record_workspace_event`` only update these fields; they do NOT
    # touch ``_last_activity`` (the stdout baseline) or the cumulative
    # waiting-on-child ceiling. The verdict hook in ``evaluate()``
    # consults these fields via ``_channel_evidence_active`` and
    # ``last_evidence_summary``.
    _mcp_tool_call_count: int = field(default=0, init=False)
    _last_mcp_tool_call_at: float | None = field(default=None, init=False)
    _subagent_progress_count: int = field(default=0, init=False)
    _last_subagent_progress_at: float | None = field(default=None, init=False)
    # Throttle timestamp for the SUBAGENT_PROGRESS waiting-status emit
    # in ``_handle_waiting_branch``. Separate from
    # ``_last_subagent_progress_at`` (which is the channel-evidence
    # timestamp): this field tracks the LAST EMIT TIME so the emit
    # cadence is bounded by
    # ``TimeoutPolicy.watchdog_subagent_progress_interval_seconds``.
    _last_subagent_progress_emit_at: float | None = field(default=None, init=False)
    _subagent_output_count: int = field(default=0, init=False)
    _last_subagent_output_at: float | None = field(default=None, init=False)
    _workspace_event_count_internal: int = field(default=0, init=False)
    _last_workspace_event_at: float | None = field(default=None, init=False)
    _last_workspace_event_weight: float = field(default=0.0, init=False)
    # Subagent output capture state. The watchdog polls the injected
    # DiscoveryStrategy for output paths and reuses capture instances per
    # worker so only new lines are ingested as first-party evidence.
    _subagent_output_captures: dict[str, SubagentOutputCapture] = field(
        default_factory=dict, init=False
    )
    # Per-kind workspace event counter. The watchdog tracks how many
    # file changes have been observed for each WorkspaceChangeKind
    # (source / log / cache / artifact / other) so the post-mortem
    # can see WHICH kinds were most active at the moment of a fire.
    # The workspace_kind_counts property returns a defensive copy.
    _workspace_kind_counts: dict[str, int] = field(default_factory=dict, init=False)
    # Smart-verdict gate state. The watchdog consults the StuckClassifier
    # before every non-absolute fire; the classifier returns one of six
    # kinds and the gate returns CONTINUE for any non-STUCK kind so a
    # productive session that does not look productive is not killed.
    # The two state fields below are the inputs the classifier needs
    # from the run-loop / connectivity monitor (the watchdog does not
    # own these signals itself):
    #   - is_waiting_state: True when the pipeline has already entered a
    #     wait state (the run loop will sleep and re-enter the phase).
    #     The classifier returns DUPLICATE_KILL when this is True so a
    #     second FIRE during a wait is impossible.
    #   - connectivity_state_provider: optional callable returning the
    #     current connectivity state label ("online" / "offline" /
    #     "unknown" / "degraded"). When "offline" the classifier returns
    #     WAITING_ON_CONNECTIVITY and the gate defers the fire. The
    #     callable is optional so the watchdog is constructible in tests
    #     without a real ConnectivityMonitor.
    _is_waiting_state: bool = field(default=False, init=False)
    _connectivity_state_provider: Callable[[], str | None] | None = field(default=None, init=False)
    # The most recent ``classify_quiet`` callable received by
    # ``evaluate()``. The gate (``_gate_fire``) consults the classifier
    # on every non-absolute fire, and the classifier's
    # ``WAITING_ON_CHILD`` / ``RESUMABLE_CONTINUE`` branches require a
    # live callable (a noop stub would always return ACTIVE and the
    # branches would never fire). Storing the callable here lets the
    # gate consult the same state the rest of ``evaluate()`` is
    # already consulting. ``None`` means ``evaluate()`` has not been
    # called yet; the gate falls back to a noop ACTIVE stub in that
    # case.
    _classify_quiet_provider: Callable[[], AgentExecutionState] | None = field(
        default=None, init=False
    )
    # Tick-scoped corroboration cache. ``evaluate()`` captures one
    # ``CorroborationSnapshot`` at the top of every tick and reuses it
    # for ALL sub-evaluators on that tick (``_evaluate_no_output_at_start``,
    # ``_evaluate_strictly_stuck``, ``_evaluate_no_progress_quiet``,
    # ``_handle_waiting_branch``, etc.) so a single corroborator call
    # drives both the decision path and the diagnostic surface. Without
    # this cache the watchdog would call the corroborator once per
    # sub-evaluator, and a flaky corroborator that rotates alive_by
    # values on each call would produce inconsistent decisions vs.
    # diagnostics on the same tick (the bug the regression test
    # ``test_single_tick_corroboration_snapshot_reused_for_all_decisions_and_diagnostics``
    # pins). ``None`` means ``evaluate()`` is not running; calls outside
    # the tick (e.g. external probing) bypass the cache and invoke the
    # corroborator directly.
    _tick_corroboration: CorroborationSnapshot | None = field(default=None, init=False)

    def __init__(
        self,
        config: TimeoutPolicy,
        clock: Clock,
        listener: WaitingStatusListener | None = None,
        *,
        corroborator: WaitingCorroborator | None = None,
        process_monitor: ProcessMonitor | None = None,
        connectivity_state_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._listener = listener
        self._corroborator = corroborator
        self._process_monitor = process_monitor
        self._connectivity_state_provider = connectivity_state_provider
        now = clock.monotonic()
        self._last_activity = now
        self._session_started_at = now
        self._invocation_started_at = None
        self._last_meaningful_output_at = None
        self._has_meaningful_output = False
        self._waiting_on_child_started_at = None
        self._cumulative_waiting_on_child_seconds = 0.0
        self._in_drain_window = False
        self._drain_started_at = None
        self._last_fire_reason = None
        self._last_deferred_kind = None
        self._last_deferred_log_at = {}
        self._last_waiting_status_at = None
        self._suspicion_announced_for_run = False
        self._last_tool_result_at = None
        self._awaiting_post_tool_result_progression = False
        self._mcp_tool_call_count = 0
        self._last_mcp_tool_call_at = None
        self._subagent_progress_count = 0
        self._last_subagent_progress_at = None
        # Optional human-readable description of the most recent subagent
        # observation (truncated to 200 chars). Surfaced via the
        # ``subagent_activity`` field on ``WaitingStatusEvent``s so
        # operators see what the subagent was doing at the moment of
        # the event (transition, suspicion, fire). Reset to ``None`` in
        # ``record_invocation_start`` and updated by ``record_subagent_work``.
        self._last_subagent_progress_description: str | None = None
        self._default_subagent_activity_listener: WaitingStatusListener | None = None
        self._subagent_output_count = 0
        self._last_subagent_output_at = None
        self._subagent_output_captures = {}
        self._workspace_event_count_internal = 0
        self._last_workspace_event_at = None
        self._last_workspace_event_weight = 0.0
        self._workspace_kind_counts = {}
        self._entry_corroboration: CorroborationSnapshot | None = None
        self._repetition_tracker = RepetitionTracker(
            clock,
            consecutive_threshold=config.repeated_error_consecutive_threshold,
            window_count=config.repeated_error_window_count,
            window_seconds=config.repeated_error_window_seconds,
        )
        self._last_progress_fingerprint: str | None = None
        self._is_waiting_state = False
        self._classify_quiet_provider = None
        self._log = logger.bind(component="idle_watchdog")

    @property
    def last_fire_reason(self) -> WatchdogFireReason | None:
        """The reason the watchdog fired, or None if it hasn't fired yet."""
        return self._last_fire_reason

    @property
    def last_deferred_kind(self) -> StuckKind | None:
        """The ``StuckKind`` that deferred the most recent would-be fire.

        ``None`` when the watchdog has not deferred a fire yet OR
        when the most recent fire actually FIREd (the gate only sets
        this when it returns ``WatchdogVerdict.CONTINUE`` to defer).

        The diagnostic surface for the SILENT_SUBAGENT label
        described in AC-05: ``last_fire_reason`` collapses every
        non-FIRE deferral to ``WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER``,
        but ``last_deferred_kind`` retains the precise
        ``StuckKind`` (e.g. ``StuckKind.SILENT_SUBAGENT``) so an
        operator can see WHY a would-be fire was deferred ("a
        subagent dispatched then went silent for >180s").  See
        ``tests/agents/idle_watchdog/test_silent_subagent_runtime.py``
        for the runtime-facing contract test.
        """
        return self._last_deferred_kind

    @property
    def last_alive_by(self) -> AliveBy | None:
        """The corroborator's ``alive_by`` signal at the most recent fire.

        ``None`` when the watchdog has not fired yet OR when the most
        recent fire was not ``NO_PROGRESS_QUIET`` (the
        live-child vs dead-child differentiation only matters for the
        NO_PROGRESS_QUIET path; other fire helpers do not capture
        ``alive_by``).

        Consumed by ``IdleWatchdogKilledError.child_alive`` so the
        failure classifier can read the live-child signal end-to-end
        via the typed exception's ``__cause__`` chain.
        """
        return self._last_alive_by

    def diagnostic_snapshot(self, now: float | None = None) -> dict[str, object]:
        """Return a JSON-serializable dict of the watchdog's full state.

        The snapshot is a PURE READ of watchdog state (no side effects).
        The only clock touch is the injected ``self._clock.monotonic()``
        when ``now`` is None; tests pass an explicit ``now`` to drive
        the FakeClock deterministically without time travel.

        Shape (forward-compatible; ``None`` when the field has never
        been populated):

        - ``last_fire_reason``: ``str | None`` (WatchdogFireReason.value)
        - ``last_deferred_kind``: ``str | None`` (StuckKind.value)
        - ``last_alive_by``: ``str | None`` (AliveBy.value)
        - ``idle_elapsed_seconds``: ``float``
        - ``invocation_elapsed_seconds``: ``float``
        - ``cumulative_waiting_on_child_seconds``: ``float``
        - ``last_subagent_progress_description``: ``str | None``
        - ``live_subagent_count``: ``int`` (0 when no monitor)
        - ``subagent_progress_count``: ``int``
        - ``subagent_output_count``: ``int``
        - ``mcp_tool_call_count``: ``int``
        - ``workspace_event_count``: ``int``
        - ``evidence_summary``: ``list[dict[str, object]]`` (per-channel)
        - ``resumable_session_id``: ``str | None`` (None; populated
          externally by the watchdog kill path that captures the
          subprocess transport session id)
        """
        timestamp = now if now is not None else self._clock.monotonic()
        live_subagent_count = (
            self._process_monitor.live_subagent_count()
            if self._process_monitor is not None
            else 0
        )
        snapshot: dict[str, object] = {
            "last_fire_reason": (
                self._last_fire_reason.value
                if self._last_fire_reason is not None
                else None
            ),
            "last_deferred_kind": (
                self._last_deferred_kind.value
                if self._last_deferred_kind is not None
                else None
            ),
            "last_alive_by": (
                self._last_alive_by.value if self._last_alive_by is not None else None
            ),
            "idle_elapsed_seconds": round(self.idle_elapsed_seconds(timestamp), 1),
            "invocation_elapsed_seconds": round(self.invocation_elapsed_seconds, 1),
            "cumulative_waiting_on_child_seconds": round(
                self._cumulative_waiting_on_child_seconds, 1
            ),
            "last_subagent_progress_description": self._last_subagent_progress_description,
            "live_subagent_count": live_subagent_count,
            "subagent_progress_count": self._subagent_progress_count,
            "subagent_output_count": self._subagent_output_count,
            "mcp_tool_call_count": self._mcp_tool_call_count,
            "workspace_event_count": self._workspace_event_count_internal,
            "evidence_summary": self.last_evidence_summary(timestamp).to_dict_list(),
            "resumable_session_id": None,
        }
        return snapshot

    @property
    def cumulative_waiting_on_child_seconds(self) -> float:
        """Cumulative seconds spent in WAITING_ON_CHILD state across all runs."""
        return self._cumulative_waiting_on_child_seconds

    @property
    def last_subagent_progress_description(self) -> str | None:
        """The most recent subagent progress description.

        Set by ``record_subagent_work`` and reset to ``None`` by
        ``record_invocation_start``. Surfaced publicly so operators and
        tooling can see what the subagent was doing at any moment without
        needing to supply a full ``WaitingStatusListener``.
        """
        return self._last_subagent_progress_description

    def register_default_subagent_activity_listener(
        self,
        listener: WaitingStatusListener | None,
    ) -> None:
        """Register a listener that receives every subagent activity event.

        The listener is invoked from ``_emit`` for every ``WaitingStatusEvent``
        whose ``subagent_activity`` field is non-None. This gives a cheap,
        real-time view of what the subagent is doing (e.g. the last child
        progress line) without requiring callers to implement a full
        ``WaitingStatusListener``.

        The listener is reset to ``None`` on ``record_invocation_start`` so
        state does not leak across invocations. Listener exceptions are
        caught and logged at DEBUG; they never propagate.
        """
        self._default_subagent_activity_listener = listener

    def record_invocation_start(self) -> None:
        """Record the start of the invocation."""
        now = self._clock.monotonic()
        self._invocation_started_at = now
        self._last_meaningful_output_at = now
        self._has_meaningful_output = False
        self._last_subagent_progress_description = None
        self._default_subagent_activity_listener = None
        self._last_deferred_kind = None
        # Reset the per-key log throttle so a new invocation starts with an
        # empty map. The throttle MUST survive long-lived WAITING runs but
        # MUST NOT carry state across invocations (different run = different
        # operator-relevant history).
        self._last_deferred_log_at = {}

    def set_is_waiting_state(self, is_waiting_state: bool) -> None:
        """Update the pipeline's wait-state flag for the StuckClassifier gate.

        The run loop calls this once per phase iteration with the live
        ``state.is_waiting_state`` value. The watchdog does not own this
        state; it only mirrors it so the classifier can return
        DUPLICATE_KILL when a candidate fire would land during a wait.
        """
        self._is_waiting_state = is_waiting_state

    def set_connectivity_state_provider(
        self,
        provider: Callable[[], str | None] | None,
    ) -> None:
        """Inject a callable returning the current connectivity state label.

        The watchdog does not own connectivity; it only mirrors the live
        state so the classifier can return WAITING_ON_CONNECTIVITY when
        the network is offline. None disables the connectivity branch
        of the classifier (returns None for the connectivity_state
        input, which the classifier treats as "online" - the gate does
        not defer on the connectivity branch).
        """
        self._connectivity_state_provider = provider

    def _current_connectivity_state(self) -> str | None:
        """Return the current connectivity state label, or None.

        Calls the injected provider if available; otherwise returns None
        (the classifier treats None as "online" / no deferral).
        """
        if self._connectivity_state_provider is None:
            return None
        try:
            return self._connectivity_state_provider()
        except Exception:
            self._log.debug("idle watchdog: connectivity provider raised (suppressed)")
            return None

    def _classify_stuck_now(
        self,
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        """Build the classifier inputs from the watchdog's own state and return the kind.

        This is a thin wrapper that calls the pure ``classify_stuck``
        function with the watchdog's own per-channel evidence summary,
        the cached ``is_waiting_state``, the live connectivity state,
        a noop ``classify_quiet`` that always returns ``ACTIVE``, and
        the configured TTL.

        When ``corroboration`` is provided, the live ``CorroborationSnapshot``
        is threaded into the classifier as the canonical "live child"
        input. The classifier's CURRENT verdict policy is INTENTIONALLY
        NON-DECISIVE on corroboration alone: the value is plumbed so the
        gate can surface the live corroboration at every fire path (the
        analysis-feedback contract for ``CHILDREN_PERSIST_TOO_LONG`` and
        ``NO_OUTPUT_AT_START``: the gate must see the LIVE corroboration,
        not the stale ``self._last_alive_by`` post-fire field which is
        only populated post-fire by ``NO_PROGRESS_QUIET``), but the
        classifier does NOT change its verdict based on the
        corroboration alone. The watchdog's own evaluators
        (``_is_no_progress_quiet``, ``_effective_waiting_ceiling``) own
        the ``alive_by``-driven deferrals; the classifier labels the
        apparent stall, it does not re-derive the wait/defer verdict
        from a different snapshot. See ``ClassifyStuckInputs.corroboration``
        and the ``test_corroboration_*`` regression tests in
        ``tests/agents/idle_watchdog/test_stuck_classifier.py`` for the
        full contract.

        The classifier's ``WAITING_ON_CHILD`` and ``RESUMABLE_CONTINUE``
        branches are intentionally NOT consulted from the gate. The
        watchdog enters the WAITING_ON_CHILD branch precisely because
        the previous ``classify_quiet()`` call returned
        ``WAITING_ON_CHILD``; consulting the same callable again from
        the gate would always report ``LOADING`` and defer every
        cumulative-ceiling fire -- the dumb-kill protection the gate
        is supposed to provide becomes a deadlock. The
        ``subagent_liveness`` channel (which the classifier consults
        BEFORE the ``classify_quiet`` branches) is the real signal
        for "live child": a live OS descendant / subagent process
        keeps the channel fresh, so ``LOADING`` wins via that branch
        first. When the corroboration does not see a live child
        (e.g. a deadlocked agent whose child has exited) the
        ``classify_quiet`` branches must NOT veto the fire.

        The live ``classify_quiet`` is still consulted by
        ``evaluate()`` itself to decide which branch to enter; the
        gate's call site is the boundary between "which branch am I
        in" (live signal) and "is the agent actually stuck" (noop
        signal). The watchdog stores the most recent callable in
        ``self._classify_quiet_provider`` for diagnostic exposure
        (e.g. ``last_evidence_summary`` consumers and the dumb-kill
        regression tests in
        ``tests/agents/idle_watchdog/test_smart_verdict_dumb_kills.py``
        that exercise the gate's deferral via the
        ``subagent_liveness`` channel).

        The function is intentionally side-effect free: it does not
        update any watchdog state, does not log, and does not mutate
        the fire reason. The gate is the side-effect boundary.
        """
        summary = self.last_evidence_summary(now)
        connectivity = self._current_connectivity_state()

        def _noop_classify_quiet() -> AgentExecutionState:
            return AgentExecutionState.ACTIVE

        return classify_stuck(
            is_waiting_state=self._is_waiting_state,
            connectivity_state=connectivity,
            evidence_summary=summary,
            classify_quiet=_noop_classify_quiet,
            activity_evidence_ttl_seconds=self._config.activity_evidence_ttl_seconds,
            silent_subagent_seconds=self._config.silent_subagent_seconds,
            corroboration=corroboration,
        )

    def _gate_fire(
        self,
        fire_reason: WatchdogFireReason,
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> WatchdogVerdict:
        """Smart-verdict gate: defer non-absolute fires the classifier names non-STUCK.

        The absolute ``SESSION_CEILING_EXCEEDED`` reason is the ONLY
        reason that bypasses the gate -- it is an operator-set
        hard cap (session wall-clock), not a stuck-detection signal.
        Every other reason -- including ``CHILDREN_PERSIST_TOO_LONG``,
        ``NO_OUTPUT_DEADLINE``, ``NO_OUTPUT_AT_START``,
        ``STALLED_AFTER_TOOL_RESULT``, ``REPEATED_ERROR_LOOP``,
        ``NO_PROGRESS_QUIET``, and the post-exit reasons -- is gated:
        the watchdog consults ``classify_stuck`` and returns CONTINUE
        (with a debug log naming the kind) for any non-STUCK kind.

        When the caller supplies a live ``corroboration`` snapshot, it
        is threaded into the classifier as the canonical "live child"
        input (the analysis-feedback contract for
        ``CHILDREN_PERSIST_TOO_LONG`` and ``NO_OUTPUT_AT_START``).
        Without this parameter the classifier would only see the
        process-monitor subagent_liveness channel -- a corroborator-only
        live signal would be invisible to the gate. The classifier's
        CURRENT verdict policy does NOT change based on the
        corroboration alone; the watchdog's own evaluators own the
        ``alive_by``-driven deferrals. The corroboration parameter is
        exposed so future classifier extensions can use it without
        changing the call site.

        The helper returns the final verdict the caller should use:
        FIRE for an allowed fire, CONTINUE for a deferred fire. The
        helper is the single boundary between the fire-decision helpers
        and the verdict-returning logic; the helpers that compute a
        candidate fire (e.g. _handle_waiting_branch,
        _post_tool_result_stalled, _evaluate_no_progress_quiet,
        _evaluate_no_output_at_start) call this helper to decide
        whether the fire is actually allowed.
        """
        if fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
            return WatchdogVerdict.FIRE
        kind = self._classify_stuck_now(
            now=now, idle_elapsed=idle_elapsed, corroboration=corroboration
        )
        if kind == StuckKind.STUCK:
            return WatchdogVerdict.FIRE
        # Diagnostic-only kind (SILENT_SUBAGENT) gets its OWN
        # ``_last_fire_reason`` label so operators can see WHY a
        # would-be fire was deferred ("a subagent dispatched then went
        # silent for >180s").  Without this branch, every non-STUCK
        # deferral collapses to ``DEFERRED_BY_STUCK_CLASSIFIER`` and
        # the SILENT_SUBAGENT diagnostic is invisible at the
        # ``last_fire_reason`` surface.  See AC-05 + analysis
        # feedback for the runtime contract.
        if kind == StuckKind.SILENT_SUBAGENT:
            self._last_fire_reason = WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER
            self._last_deferred_kind = kind
            if self._maybe_log_deferred(fire_reason, kind, idle_elapsed, now):
                self._log.debug(
                    "idle watchdog: silent subagent (deferred) reason={} idle_elapsed={}s",
                    fire_reason,
                    round(idle_elapsed, 1),
                )
            return WatchdogVerdict.CONTINUE
        self._last_fire_reason = WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER
        self._last_deferred_kind = kind
        if self._maybe_log_deferred(fire_reason, kind, idle_elapsed, now):
            self._log.debug(
                "idle watchdog: deferred fire reason={} kind={} idle_elapsed={}s",
                fire_reason,
                kind,
                round(idle_elapsed, 1),
            )
        return WatchdogVerdict.CONTINUE

    def _maybe_log_deferred(
        self,
        fire_reason: WatchdogFireReason,
        deferred_kind: StuckKind,
        idle_elapsed: float,
        now: float,
    ) -> bool:
        """Return True (and stamp the throttle map) when a deferred DEBUG
        emission is allowed for this ``(fire_reason, deferred_kind)`` key.

        The PROMPT log showed ~10 DEBUG records/sec at ``_gate_fire:949``
        while a fire was deferred; per-tick DEBUG emission is log spam.
        This helper consults ``self._last_deferred_log_at`` and the
        configured ``watchdog_log_throttle_seconds`` to keep emissions
        to at most one per ``(fire_reason, deferred_kind)`` key per
        throttle window.

        Returns True when:
          - the key has never been logged (initial transition), OR
          - ``now - last_logged_at >= watchdog_log_throttle_seconds``
            (the throttle window has elapsed since the prior emission).

        Returns False when ``now - last_logged_at < watchdog_log_throttle_seconds``
        (the emission would be a duplicate).

        The map is updated on every call that returns True so a
        subsequent call within the throttle window returns False.
        """
        key = (fire_reason.value, deferred_kind.value)
        last = self._last_deferred_log_at.get(key)
        throttle = self._config.watchdog_log_throttle_seconds
        if last is None or (now - last) >= throttle:
            self._last_deferred_log_at[key] = now
            return True
        return False

    @property
    def invocation_elapsed_seconds(self) -> float:
        """Return the seconds elapsed since the start of the invocation."""
        if self._invocation_started_at is None:
            return 0.0
        return self._clock.monotonic() - self._invocation_started_at

    def _is_no_progress_quiet(self, now: float, corroboration: CorroborationSnapshot) -> bool:
        """Return True when all no-progress quiet conditions are met.

        The dumb-kill floor (no_progress_quiet_minimum_invocation_seconds)
        is consulted FIRST so a recently-launched agent that is doing real
        thinking work (planning, exploration, dispatching subagents) but
        has not yet produced first-party activity evidence is not killed.
        When the floor field is None, the floor is disabled (not
        recommended). The SESSION_CEILING_EXCEEDED and the post-tool-result
        STALLED_AFTER_TOOL_RESULT paths are not affected by this floor.
        """
        if self._config.no_progress_quiet_seconds is None:
            return False
        if self.invocation_elapsed_seconds < self._config.no_progress_quiet_seconds:
            return False
        # Dumb-kill floor: defer the fire while the agent has been alive
        # for less than the configured floor. The floor must be checked
        # BEFORE the channel-evidence check so a recently-launched agent
        # with all channels stale still gets the floor protection.
        if (
            self._config.no_progress_quiet_minimum_invocation_seconds is not None
            and self.invocation_elapsed_seconds
            < self._config.no_progress_quiet_minimum_invocation_seconds
        ):
            return False
        # Defer the fire when the corroborator confirms ANY alive_by signal —
        # the child is alive (per AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        # CPU_IDLE_WHILE_ALIVE, LOG_STALE_WHILE_ALIVE, FRESH_HEARTBEAT_ONLY, or
        # STALE_LABEL_ONLY) so the cumulative CHILDREN_PERSIST_TOO_LONG ceiling
        # (default 600s) is the correct upper bound for live-child stalls, not
        # the 120s NO_PROGRESS_QUIET fire. NO_PROGRESS_QUIET now fires ONLY
        # when the corroborator returns no alive_by signal at all
        # (corroboration.alive_by is None — no live signal from the
        # corroborator) AND no fresh channel evidence is present (the agent is
        # silent and the channels are stale). When the corroborator returns
        # alive_by is None, the conservative policy preserves the old fire
        # path so legacy construction sites that do not set the signal
        # continue to behave identically.
        if corroboration.alive_by is not None:
            return False
        return not self._channel_evidence_active(now)

    def _evaluate_no_progress_quiet(
        self, now: float, idle_elapsed: float
    ) -> WatchdogVerdict | None:
        """Evaluate if the watchdog should fire due to lack of progress."""
        if self._config.no_progress_quiet_seconds is None:
            return None
        if self.invocation_elapsed_seconds < self._config.no_progress_quiet_seconds:
            return None
        if idle_elapsed < self._config.no_progress_quiet_seconds:
            return None

        corroboration = self._safe_corroborate()
        if not self._is_no_progress_quiet(now, corroboration):
            return None

        gate_verdict = self._gate_fire(
            WatchdogFireReason.NO_PROGRESS_QUIET, now=now, idle_elapsed=idle_elapsed
        )
        if gate_verdict == WatchdogVerdict.CONTINUE:
            return WatchdogVerdict.CONTINUE

        self._last_fire_reason = WatchdogFireReason.NO_PROGRESS_QUIET
        # Capture the corroborator's alive_by signal at the moment of
        # the fire. NO_PROGRESS_QUIET is the only fire path where
        # live-child vs dead-child differentiation matters; other
        # fire helpers (SESSION_CEILING_EXCEEDED, CHILDREN_PERSIST_TOO_LONG,
        # NO_OUTPUT_AT_START, etc.) do not need to capture alive_by.
        # The signal is consumed by IdleWatchdogKilledError.child_alive
        # so the failure classifier can read the live-child signal
        # end-to-end via the typed exception's __cause__ chain. When
        # corroboration.alive_by is None, child_alive will be False
        # (truly dead child -> Rule 2: exponential backoff).
        self._last_alive_by = corroboration.alive_by
        diag: dict[str, object] = {
            "cumulative": round(self._cumulative_waiting_on_child_seconds, 1),
            "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
            "idle_elapsed": round(idle_elapsed, 1),
            "ceiling": self._config.no_progress_quiet_seconds,
            "effective_ceiling": "no_progress_quiet",
        }
        corr_diag = self._build_corroboration_diag(corroboration)
        for key, val in corr_diag.items():
            if key not in diag:
                diag[key] = val
        evidence_block, _ = self._build_evidence_summary_diag(now)
        for ev_key, ev_val in evidence_block.items():
            if ev_key not in diag:
                diag[ev_key] = ev_val

        self._emit(
            WaitingStatusKind.HARD_STOP,
            current_run_seconds=round(self.invocation_elapsed_seconds, 1),
            idle_elapsed=idle_elapsed,
            ceiling_seconds=self._config.no_progress_quiet_seconds,
            diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
        )
        self._log.warning(
            "idle watchdog: FIRE reason={} idle_elapsed={}s invocation_elapsed={}s",
            WatchdogFireReason.NO_PROGRESS_QUIET,
            round(idle_elapsed, 1),
            round(self.invocation_elapsed_seconds, 1),
        )
        return WatchdogVerdict.FIRE

    def _evaluate_strictly_stuck(  # noqa: PLR0911 - early-exit guards per branch of the strictly-stuck state machine
        self,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot,
    ) -> WatchdogVerdict | None:
        """Evaluate the STRICTLY_STUCK orthogonal ceiling for stuck-but-alive jobs.

        Fires ``WatchdogFireReason.STRICTLY_STUCK`` when ALL of the following
        are true:

        1. ``self._config.no_progress_quiet_strictly_stuck_seconds`` is not
           None (the ceiling is enabled).
        2. The corroborator reports ``alive_by`` in the strictly-stuck
           set ``{OS_DESCENDANT_ONLY_STALE_PROGRESS, CPU_IDLE_WHILE_ALIVE,
           LOG_STALE_WHILE_ALIVE}``.
        3. The agent has been in this strictly-stuck alive_by state for at
           least ``no_progress_quiet_strictly_stuck_seconds`` (tracked
           via the ``_strictly_stuck_run_started_at`` field).
        4. NO first-party channel is fresh (a productive agent in this
           state would be emitting stdout / tool calls / workspace
           events; the lack of any fresh channel means the agent is
           genuinely silent).

        The ceiling is ORTHOGONAL to ``NO_PROGRESS_QUIET`` (which
        requires ``alive_by is None``) and to ``CHILDREN_PERSIST_TOO_LONG``
        (which fires on the cumulative wall-clock). The new ceiling is
        tuned for the stuck-but-alive case which is too lenient to be
        caught by the standard 600s ``CHILDREN_PERSIST_TOO_LONG`` ceiling
        but too noisy to be caught by ``NO_PROGRESS_QUIET`` (the agent
        IS technically alive).

        Returns ``WatchdogVerdict.FIRE`` when the conditions are met AND
        the smart-verdict gate allows the fire (a fresh subagent
        liveness signal in the corroborator at this very tick will
        defer). Returns ``WatchdogVerdict.CONTINUE`` when the gate
        defers. Returns ``None`` when the ceiling is not engaged.
        """
        if self._config.no_progress_quiet_strictly_stuck_seconds is None:
            # Reset the run counter so a future enable starts fresh.
            self._strictly_stuck_run_started_at = None
            return None
        _strictly_stuck_alive_by = (
            AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            AliveBy.CPU_IDLE_WHILE_ALIVE,
            AliveBy.LOG_STALE_WHILE_ALIVE,
        )
        if corroboration.alive_by not in _strictly_stuck_alive_by:
            # Transition OUT of the strictly-stuck alive_by set: reset
            # the run counter so a brief liveness gap does not accumulate
            # across runs.
            self._strictly_stuck_run_started_at = None
            return None
        if self._strictly_stuck_run_started_at is None:
            self._strictly_stuck_run_started_at = now
            return None
        strictly_stuck_run_seconds = now - self._strictly_stuck_run_started_at
        if strictly_stuck_run_seconds < self._config.no_progress_quiet_strictly_stuck_seconds:
            return None
        if self._channel_evidence_active(now):
            # A first-party channel is fresh (mcp_tool, subagent_output,
            # workspace) -- the agent is making forward progress on a
            # non-stdout channel; defer.
            return None
        # Route through the gate so a fresh subagent_liveness signal
        # in the corroborator can defer (defense-in-depth, mirrors the
        # NO_OUTPUT_AT_START gate pattern at line 1252-1256).
        gate_verdict = self._gate_fire(
            WatchdogFireReason.STRICTLY_STUCK,
            now=now,
            idle_elapsed=idle_elapsed,
            corroboration=corroboration,
        )
        if gate_verdict == WatchdogVerdict.CONTINUE:
            return WatchdogVerdict.CONTINUE
        self._last_fire_reason = WatchdogFireReason.STRICTLY_STUCK
        diag: dict[str, object] = {
            "alive_by": corroboration.alive_by.value
            if corroboration.alive_by is not None
            else None,
            "strictly_stuck_run_seconds": round(strictly_stuck_run_seconds, 1),
            "strictly_stuck_ceiling_seconds": (
                self._config.no_progress_quiet_strictly_stuck_seconds
            ),
            "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
            "idle_elapsed": round(idle_elapsed, 1),
        }
        evidence_block, _ = self._build_evidence_summary_diag(now)
        for ev_key, ev_val in evidence_block.items():
            if ev_key not in diag:
                diag[ev_key] = ev_val
        self._emit(
            WaitingStatusKind.HARD_STOP,
            current_run_seconds=round(strictly_stuck_run_seconds, 1),
            idle_elapsed=idle_elapsed,
            ceiling_seconds=self._config.no_progress_quiet_strictly_stuck_seconds,
            diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
        )
        self._log.warning(
            "idle watchdog: FIRE reason={} idle_elapsed={}s"
            " strictly_stuck_run_seconds={}s alive_by={}",
            WatchdogFireReason.STRICTLY_STUCK,
            round(idle_elapsed, 1),
            round(strictly_stuck_run_seconds, 1),
            corroboration.alive_by,
        )
        return WatchdogVerdict.FIRE

    def _evaluate_no_output_at_start(  # noqa: PLR0911 - 3 early-exit guards + 2 deferral gates + final verdict path; each is a distinct condition
        self,
        now: float,
        idle_elapsed: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict | None:
        """Evaluate if the watchdog should fire due to no output at start.

        Fires when the agent has been alive for no_output_at_start_seconds with
        ZERO recorded activity (no stdout, no tool call, no file change, no
        subagent output). This is different from NO_PROGRESS_QUIET which fires
        inside WAITING_ON_CHILD deferral when the agent HAS produced output at
        some point but is now stuck with stale-progress evidence.

        Defers (returns ``None``) before the gate when ANY of the following
        live signals is present:

        - ``classify_quiet()`` returns ``AgentExecutionState.WAITING_ON_CHILD``
          -- the execution strategy has already classified the run as
          waiting on a live child.  This early-exit prevents the prompt
          false-positive where a subagent dispatched at invocation start
          caused ``NO_OUTPUT_AT_START`` to fire at 30s before the
          WAITING_ON_CHILD deferral path (``_handle_waiting_branch``)
          could consult its 600s cumulative ceiling.  The cumulative
          ``CHILDREN_PERSIST_TOO_LONG`` ceiling remains the correct upper
          bound for live-child stalls.
        - ``self._safe_corroborate()`` returns a ``CorroborationSnapshot``
          whose ``alive_by`` is a FRESH corroboration state -- the
          corroborator (process tree / OS descendant scan / heartbeat)
          confirms a live child agent with a recent progress or
          heartbeat signal. The helper ``_alive_by_is_fresh(...)``
          (driven by ``_FRESH_ALIVE_BY_STATES`` = ``{FRESH_PROGRESS,
          FRESH_HEARTBEAT_ONLY}``) returns True ONLY for those two
          states. Stale ``AliveBy`` values (``OS_DESCENDANT_ONLY_STALE_PROGRESS``,
          ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
          ``STALE_LABEL_ONLY``) and ``None`` DO NOT defer: they describe
          a child that has stopped producing fresh evidence and the
          short ``NO_OUTPUT_AT_START`` kill MUST still apply. The
          ``self._last_alive_by`` field is intentionally NOT consulted
          here: it is only populated post-fire by ``NO_PROGRESS_QUIET``
          at line 620 and is never set for ``NO_OUTPUT_AT_START``.
          Reading the stale field would never trigger (when
          ``NO_PROGRESS_QUIET`` has never fired) or forever suppress
          ``NO_OUTPUT_AT_START`` after a prior ``NO_PROGRESS_QUIET``
          fire.
        - ``self._cumulative_waiting_on_child_seconds > 0`` -- the agent
          has already survived a full ``WAITING_ON_CHILD`` entry/exit
          cycle this invocation, which demonstrates it is alive enough
          that ``NO_OUTPUT_AT_START`` no longer applies.
        """
        if (
            self._config.no_output_at_start_seconds is None
            or self._has_meaningful_output
            or self._last_meaningful_output_at is None
            or (now - self._last_meaningful_output_at) < self._config.no_output_at_start_seconds
        ):
            return None
        # NOTE: NO_OUTPUT_AT_START has NO dumb-kill floor. The dumb-kill
        # floor (``no_progress_quiet_minimum_invocation_seconds``) is
        # ONLY consulted inside ``_is_no_progress_quiet`` for
        # ``NO_PROGRESS_QUIET``. The 30s/60s ``NO_OUTPUT_AT_START``
        # short ceiling fires on a truly silent ACTIVE run regardless
        # of how recently the agent was launched -- a freshly-launched
        # agent that never produces any channel evidence (stdout, MCP
        # tool call, file change, subagent progress) inside the
        # ``no_output_at_start_seconds`` window is a stuck process and
        # the short kill MUST fire. The 120s default dumb-kill floor
        # is intentionally NOT consulted here so the operator's
        # documented ``no_output_at_start_seconds`` threshold is the
        # single source of truth for ``NO_OUTPUT_AT_START`` lifetime.
        # ``classify_quiet()`` (waiting / subagent deferral) and the
        # corroborator's alive_by (FRESH subagent deferral) are still
        # consulted below as the canonical subagent deferral paths.
        try:
            quiet_state = classify_quiet()
        except Exception:
            quiet_state = AgentExecutionState.ACTIVE
        if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
            return None
        if quiet_state not in {
            AgentExecutionState.ACTIVE,
            AgentExecutionState.WAITING_ON_CHILD,
        }:
            return None
        if self._channel_evidence_active(now):
            return None
        # Defer when the LIVE corroborator reports a FRESH live-child
        # signal. We MUST call ``_safe_corroborate()`` here, NOT read
        # ``self._last_alive_by``: that field is only populated post-fire
        # by ``NO_PROGRESS_QUIET`` (line 620) so it carries stale state
        # from a prior fire and is never useful as a pre-fire deferral
        # signal for ``NO_OUTPUT_AT_START``. The LIVE call returns the
        # fresh snapshot from the corroborator at the moment of this
        # evaluation.
        #
        # Stale alive_by values (``OS_DESCENDANT_ONLY_STALE_PROGRESS``,
        # ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
        # ``STALE_LABEL_ONLY``) DO NOT defer: they describe a child
        # that has stopped producing fresh evidence (process tree
        # presence only, no progress / no heartbeat, or log-truncated
        # state) and the NO_OUTPUT_AT_START short kill MUST still
        # apply. The earlier ``is not None`` check was a false-positive
        # deferral gate: a wedged startup where the corroborator
        # reports an OS-descendant-only stale progress state would
        # suppress the short kill and never reach ``_gate_fire`` /
        # StuckClassifier. The fix is to gate the deferral on the
        # fresh-evidence subset of ``AliveBy`` and let stale values
        # fall through to ``_gate_fire`` so the StuckClassifier sees
        # the live snapshot. See
        # ``TestNoOutputAtStartStaleAliveByDoesNotDefer`` in
        # ``tests/agents/test_idle_watchdog_no_output_at_start_lifecycle.py``
        # for the regression test that pins this behavior.
        corroboration = self._safe_corroborate()
        if _alive_by_is_fresh(corroboration.alive_by):
            return None
        # Defer when we have already accumulated ``WAITING_ON_CHILD`` time
        # this run; an agent that survived a full waiting run has
        # demonstrated it is alive enough that ``NO_OUTPUT_AT_START`` no
        # longer applies. The cumulative ceiling
        # (``max_waiting_on_child_seconds``) is the bounded upper bound
        # for live-child stalls; this gate is a NO_OUTPUT_AT_START-specific
        # early-out that prevents a false-positive kill when the
        # corroborator is not injected.
        if self._cumulative_waiting_on_child_seconds > 0.0:
            return None

        gate_verdict = self._gate_fire(
            WatchdogFireReason.NO_OUTPUT_AT_START,
            now=now,
            idle_elapsed=idle_elapsed,
            corroboration=corroboration,
        )
        if gate_verdict == WatchdogVerdict.CONTINUE:
            return WatchdogVerdict.CONTINUE

        self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_AT_START
        diag: dict[str, object] = {
            "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
            "no_output_at_start_seconds": self._config.no_output_at_start_seconds,
            "last_activity_equals_started_at": True,
        }
        evidence_block, _ = self._build_evidence_summary_diag(now)
        for ev_key, ev_val in evidence_block.items():
            if ev_key not in diag:
                diag[ev_key] = ev_val

        self._emit(
            WaitingStatusKind.HARD_STOP,
            current_run_seconds=round(self.invocation_elapsed_seconds, 1),
            idle_elapsed=idle_elapsed,
            ceiling_seconds=self._config.no_output_at_start_seconds,
            diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
        )
        self._emit_fire_log(
            WatchdogFireReason.NO_OUTPUT_AT_START,
            now=now,
            idle_elapsed=idle_elapsed,
            message_suffix=(
                f" no_output_at_start_seconds={self._config.no_output_at_start_seconds}s"
            ),
        )
        return WatchdogVerdict.FIRE

    def idle_elapsed_seconds(self, now: float) -> float:
        """Seconds since the last recorded activity (the idle duration).

        Public accessor so callers (e.g. the process-reader fire log) can report
        a meaningful idle-elapsed value instead of the raw monotonic clock.
        """
        return now - self._last_activity

    def record_activity(self) -> None:
        """Record that the agent produced output; resets idle/drain/child state.

        Does NOT reset _session_started_at — the session ceiling is absolute and
        cannot be defeated by heartbeat activity.

        Does NOT reset _cumulative_waiting_on_child_seconds. Cumulative is a true
        absolute ceiling (parallel to the session ceiling) and never decays during
        the session.

        Clears the post-tool-result awaiting flag so a follow-up
        OUTPUT_LINE/STREAM_DELTA does not appear to be the post-tool-result
        progression activity (the flag is set by
        ``record_tool_result_activity()`` only).

        Counts as genuine forward progress for the repeated-error circuit
        breaker: it resets the repetition streak so an error loop only fires
        when the agent is NOT making real progress.
        """
        self._reset_idle_baseline()
        self._repetition_tracker.note_progress()
        self._last_meaningful_output_at = self._clock.monotonic()
        self._has_meaningful_output = True

    def record_lifecycle_activity(self) -> None:
        """Record cosmetic, non-meaningful activity (e.g. lifecycle frames).

        Resets the idle baseline exactly like ``record_activity()`` so the
        agent is not declared idle, but does NOT reset the repeated-error
        circuit breaker: cosmetic output interleaved between identical errors
        must not mask a wedged retry loop. LIFECYCLE frames are deliberately
        excluded from the NO_OUTPUT_AT_START baseline.
        """
        self._reset_idle_baseline()

    def record_tool_call_activity(
        self,
        tool_name: str,
        tool_args: object,
    ) -> None:
        """Record a tool-call observation for the tool-call circuit breaker.

        New seam added to feed :meth:`RepetitionTracker.mark_tool_call`
        from real production call sites so an agent wedged in an
        identical-tool-call retry loop (the same ``Bash`` command with
        the same arguments re-issued N times without producing forward
        progress) trips
        :data:`WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL`.

        Deliberately does NOT reset the idle baseline: identical
        tool-call wedges must still let the idle deadline advance
        (so a silent-after-wedge agent is also caught) while the
        tool-call rule catches the fast retry storm well before
        the idle timeout.  The cumulative WAITING_ON_CHILD run is
        still flushed for bookkeeping parity.

        Args:
            tool_name: The tool name (e.g. ``"Bash"``).  Empty / None
                tool names are coerced to ``"unknown"`` inside the
                tracker so the fingerprint is always well-formed.
            tool_args: The tool arguments (any JSON-serializable
                structure).  ``None`` is treated as an empty dict
                inside the tracker.  ``sort_keys=True`` ensures
                dict-key ordering does not affect the fingerprint.
        """
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._repetition_tracker.mark_tool_call(tool_name, tool_args)

    def record_error_activity(self, message: str) -> None:
        """Record an error/repeat line for the repeated-error circuit breaker.

        Deliberately does NOT reset the idle baseline: a stream of identical
        errors must still let the idle deadline advance (so a silent-after-errors
        agent is also caught), while the repeated-error rule catches a fast retry
        storm well before the idle timeout. The cumulative WAITING_ON_CHILD run is
        still flushed for bookkeeping parity.
        """
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._repetition_tracker.note_error(message)

    def record_progress_report(self, message: str) -> None:
        """Record an explicit ``report_progress`` heartbeat from the agent.

        A report that REPEATS the previous status (same fingerprint) is a cosmetic
        heartbeat: it feeds the repeated-error circuit breaker and does NOT reset
        the idle baseline, so an agent narrating "still stuck" forever can no
        longer keep itself alive. A report whose status CHANGES is treated as
        genuine forward progress (resets the idle baseline and the streak).
        """
        fingerprint = RepetitionTracker.fingerprint(message)
        if fingerprint == self._last_progress_fingerprint:
            now = self._clock.monotonic()
            self._accumulate_waiting_run(now)
            self._repetition_tracker.note_error(message)
            return
        self._last_progress_fingerprint = fingerprint
        self.record_activity()

    def _reset_idle_baseline(self) -> None:
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._last_activity = now
        self._in_drain_window = False
        self._drain_started_at = None
        self._awaiting_post_tool_result_progression = False

    def record_tool_result_activity(self) -> None:
        """Record that a TOOL_RESULT activity was observed.

        Sets the awaiting flag and records the timestamp. The next
        ``evaluate()`` call checks whether a follow-up activity
        (OUTPUT_LINE/STREAM_DELTA/TOOL_USE/LIFECYCLE) arrives within
        the configured ``post_tool_result_progression_seconds`` budget.
        If not, the watchdog fires STALLED_AFTER_TOOL_RESULT.

        This is a NEW BEHAVIOR for direct wedge detection. The
        existing ``pty_line_reader._handle_queued_line`` calls this
        method AFTER ``record_activity()`` on the TOOL_RESULT branch
        so the wedge is detected in ~120s by default (the
        post-tool-result budget) rather than waiting for the full
        300s idle timeout.

        Does NOT reset _session_started_at (the session ceiling
        remains absolute).
        """
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._last_activity = now
        self._in_drain_window = False
        self._drain_started_at = None
        self._last_tool_result_at = now
        self._awaiting_post_tool_result_progression = True
        self._repetition_tracker.note_progress()

    def record_mcp_tool_call(self, now: float | None = None) -> None:
        """Record an MCP tool-call activity signal (new channel).

        Increments the mcp_tool channel counter and updates the per-channel
        ``_last_at`` timestamp. Does NOT touch ``_last_activity`` (the stdout
        baseline) — the existing 'stdout only resets idle baseline' invariant
        is preserved. The verdict hook in ``evaluate()`` consults the per-channel
        ``_last_at`` via ``_channel_evidence_active`` and defers a
        NO_OUTPUT_DEADLINE fire while the channel is fresher than the configured
        ``activity_evidence_ttl_seconds``.

        Args:
            now: Optional monotonic timestamp override; tests use this to
                drive FakeClock without time travel. Defaults to the
                watchdog's injected clock.
        """
        timestamp = now if now is not None else self._clock.monotonic()
        self._mcp_tool_call_count += 1
        self._last_mcp_tool_call_at = timestamp

    def record_subagent_work(
        self,
        now: float | None = None,
        *,
        description: str | None = None,
    ) -> None:
        """Record a subagent work activity signal (subagent_output channel).

        Increments the subagent_output first-party channel counter and updates
        the per-channel ``_last_at`` timestamp. Does NOT touch
        ``_last_activity`` (the stdout baseline). The verdict hook in
        ``evaluate()`` defers a NO_OUTPUT_DEADLINE fire while this channel is
        fresher than the configured ``activity_evidence_ttl_seconds``.

        A subagent that exists but has produced no tool calls, no progress
        signals, and no file changes for the full TTL is NOT evidence of
        progress — its channel becomes stale and the watchdog returns to
        the normal idle path.

        Args:
            now: Optional monotonic timestamp override; tests use this to
                drive FakeClock without time travel. Defaults to the
                watchdog's injected clock.
            description: Optional short string describing the subagent
                activity being recorded (e.g. the raw line that triggered
                the activity sink). Truncated to 200 chars and surfaced
                via the ``subagent_activity`` field on subsequent
                ``WaitingStatusEvent`` instances so operators can see the
                most recent subagent signal at the moment of any event.
        """
        timestamp = now if now is not None else self._clock.monotonic()
        self._subagent_progress_count += 1
        self._last_subagent_progress_at = timestamp
        if description is not None:
            sanitized = _sanitize_subagent_description(description)
            self._last_subagent_progress_description = sanitized

    def record_subagent_output(self, line_count: int = 1, now: float | None = None) -> None:
        """Record fresh subagent output as first-party evidence.

        This is the channel that captures a subagent's own output/log stream
        when it is observable. Each new line read from the subagent's output
        advances the ``subagent_output`` first-party channel timestamp.

        Args:
            line_count: Number of new lines observed; defaults to 1.
            now: Optional monotonic timestamp override.
        """
        timestamp = now if now is not None else self._clock.monotonic()
        self._subagent_output_count += line_count
        self._last_subagent_output_at = timestamp

    def poll_subagent_output(self, now: float | None = None) -> int:
        """Poll observable subagent output streams and record new lines.

        Uses the injected ``ProcessMonitor`` to discover subagent log files and
        reads only new lines since the last poll. Each new line advances the
        ``subagent_output`` first-party channel.

        Args:
            now: Optional monotonic timestamp override.

        Returns:
            Number of new lines observed across all workers.
        """
        if self._process_monitor is None:
            return 0
        timestamp = now if now is not None else self._clock.monotonic()
        try:
            captures = self._process_monitor.discover_subagent_outputs()
        except Exception:
            self._log.debug(
                "idle watchdog: process_monitor.discover_subagent_outputs raised (suppressed)"
            )
            return 0
        total = 0
        for worker_id, capture in captures.items():
            existing = self._subagent_output_captures.get(worker_id)
            if existing is not None:
                resolved = existing
            else:
                resolved = capture
                self._subagent_output_captures[worker_id] = resolved
            try:
                lines = resolved.read_lines(worker_id)
            except Exception:
                self._log.debug("idle watchdog: subagent output capture raised (suppressed)")
                continue
            if lines:
                total += len(lines)
        if total:
            self.record_subagent_output(total, now=timestamp)
        return total

    def record_workspace_event(
        self,
        now: float | None = None,
        *,
        kind: WorkspaceChangeKind = WorkspaceChangeKind.OTHER,
        weight: float = 1.0,
    ) -> None:
        """Record a workspace file-change activity signal (new channel).

        Increments the workspace channel counter and updates the per-channel
        ``_last_at`` timestamp. Does NOT touch ``_last_activity`` (the stdout
        baseline). The verdict hook in ``evaluate()`` defers a
        NO_OUTPUT_DEADLINE fire while this channel is fresher than the
        configured ``activity_evidence_ttl_seconds``.

        When ``weight == 0.0`` the event is short-circuited (defense in
        depth: the WorkspaceMonitor already drops weight-0 events before
        invoking this recorder, but the watchdog enforces the contract
        too so a misconfigured binding cannot accidentally record a
        dropped event). When ``weight == 1.0`` the per-kind counter
        ``_workspace_kind_counts[kind.value]`` is advanced so the
        post-mortem diagnostic can show which kinds were most active.

        Args:
            now: Optional monotonic timestamp override; tests use this
                to drive FakeClock without time travel. Defaults to the
                watchdog's injected clock.
            kind: The ``WorkspaceChangeKind`` of the recorded event.
                Used to advance the per-kind counter so the post-mortem
                diagnostic can show ``{source: 10, log: 0, ...}`` at
                the moment of a fire. Defaults to
                ``WorkspaceChangeKind.OTHER`` (the legacy 0-arg binding
                from the pre-fix production code).
            weight: The binary weight of the recorded event. ``0.0``
                means the change is dropped (no counter / no timestamp
                update); ``1.0`` means the change counts as full
                activity. Defaults to ``1.0`` for the legacy 0-arg
                binding.
        """
        timestamp = now if now is not None else self._clock.monotonic()
        if weight == 0.0:
            return
        self._workspace_event_count_internal += 1
        self._last_workspace_event_at = timestamp
        self._last_workspace_event_weight = weight
        self._workspace_kind_counts[kind.value] = self._workspace_kind_counts.get(kind.value, 0) + 1

    @property
    def workspace_kind_counts(self) -> dict[str, int]:
        """Defensive copy of the per-kind workspace event counter.

        Returns a fresh dict on every access so callers (the
        post-mortem diagnostic, the operator UX) can mutate the
        result without affecting the watchdog's internal state. The
        keys are the five ``WorkspaceChangeKind`` string values
        (``source``, ``log``, ``cache``, ``artifact``, ``other``);
        kinds that have never been observed are absent from the
        returned dict.
        """
        return dict(self._workspace_kind_counts)

    def last_evidence_summary(self, now: float | None = None) -> EvidenceSummary:
        """Return a tier-aware per-channel evidence summary at the given time.

        Returns an ``EvidenceSummary`` containing five channels in fixed order:
        stdout (first-party), mcp_tool (first-party), subagent_output
        (first-party), subagent_liveness (side-channel), workspace
        (side-channel). Each ``ChannelEvidenceSummary`` carries the channel
        name, tier label, last observed monotonic timestamp, age in seconds,
        counter, and deferral permission.

        The summary is consumed by the watchdog's own verdict hook
        (via ``_channel_evidence_active``) and by the post-mortem
        diagnostic threading in the readers.

        Args:
            now: Optional monotonic timestamp override; tests use this to
                drive FakeClock without time travel. Defaults to the
                watchdog's injected clock.
        """
        timestamp = now if now is not None else self._clock.monotonic()
        return EvidenceSummary(
            channels=(
                self._channel_summary(
                    ChannelName.STDOUT,
                    self._last_activity,
                    None,
                    timestamp,
                    None,
                    alive_by=None,
                ),
                self._channel_summary(
                    ChannelName.MCP_TOOL,
                    self._last_mcp_tool_call_at,
                    self._mcp_tool_call_count,
                    timestamp,
                    None,
                    alive_by=None,
                ),
                self._subagent_output_summary(timestamp),
                self._subagent_liveness_summary(timestamp),
                self._workspace_summary(timestamp),
            )
        )

    def _workspace_kind_breakdown_for_summary(self) -> dict[str, int] | None:
        """Return the per-kind workspace counter snapshot for the summary.

        Returns ``None`` when no workspace activity has been observed
        yet (so the resulting ``ChannelEvidenceSummary.kind_breakdown``
        is ``None`` and is omitted from ``to_dict()`` for
        backward-compat with consumers that assert on the dict shape).
        Returns a fresh defensive copy when at least one kind has
        been observed (so the frozen dataclass invariant is preserved
        and the watchdog's internal state is not exposed).
        """
        if not self._workspace_kind_counts:
            return None
        return dict(self._workspace_kind_counts)

    def _subagent_output_summary(self, now: float) -> ChannelEvidenceSummary:
        """Build the first-party subagent_output summary.

        Combines explicit subagent progress signals (``record_subagent_work``)
        and captured subagent log-stream output (``record_subagent_output``).
        Either source is first-party evidence of subagent work and can defer
        the NO_OUTPUT_DEADLINE verdict while fresh.
        """
        candidates = [
            self._last_subagent_progress_at,
            self._last_subagent_output_at,
        ]
        last_at = max((t for t in candidates if t is not None), default=None)
        counter = self._subagent_progress_count + self._subagent_output_count
        return self._channel_summary(
            ChannelName.SUBAGENT_OUTPUT,
            last_at,
            counter,
            now,
            None,
            alive_by=None,
        )

    def _workspace_summary(self, now: float) -> ChannelEvidenceSummary:
        """Build the side-channel workspace summary with quality filtering.

        A workspace event only defers the verdict when its weight is greater
        than zero. The weight of the most recently recorded event determines
        the ``can_defer`` flag for the channel summary.
        """
        can_defer = self._last_workspace_event_weight > 0.0
        return self._channel_summary(
            ChannelName.WORKSPACE,
            self._last_workspace_event_at,
            self._workspace_event_count_internal,
            now,
            self._workspace_kind_breakdown_for_summary(),
            alive_by=None,
            can_defer_override=can_defer,
        )

    def _subagent_liveness_summary(self, now: float) -> ChannelEvidenceSummary:
        """Build the side-channel subagent_liveness summary.

        Uses the last subagent progress signal as a proxy for liveness when no
        process monitor is injected. When a process monitor is available, the
        watchdog consults it for live spawned subagents and records the liveness
        timestamp. The channel is side-channel and is quality-filtered: bare
        PID existence (alive_by in non-progress states) does NOT defer the
        verdict.

        ``can_defer`` is set to True ONLY when a process monitor has
        confirmed at least one live subagent (i.e. a real liveness
        signal from a real source). The classifier uses ``can_defer``
        to distinguish "live child from process monitor" (defers
        the gate so the dumb-kill protection applies) from "stale
        child from corroborator" (does NOT defer, so the
        no_progress / os_descendant_only ceilings can fire). The
        watchdog's own ``_channel_evidence_active`` continues to
        ignore this channel because the corroborator-only path has
        ``can_defer=False``.
        """
        last_at = self._last_subagent_progress_at
        counter = self._subagent_progress_count
        alive_by: AliveBy | None = None
        can_defer = False
        if self._process_monitor is not None:
            live = self._process_monitor.live_subagent_count()
            if live > 0:
                counter = max(counter, live)
                if last_at is None:
                    last_at = now
                alive_by = AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
                can_defer = True
        age: float | None = None if last_at is None else max(0.0, now - last_at)
        observed_counter: int | None = counter if counter > 0 else None
        return ChannelEvidenceSummary(
            channel_name=ChannelName.SUBAGENT_LIVENESS,
            tier=EvidenceTier.SIDE_CHANNEL,
            last_at=last_at,
            age_seconds=age,
            counter=observed_counter,
            alive_by=alive_by,
            can_defer=can_defer,
        )

    @staticmethod
    def _channel_summary(
        channel_name: ChannelName,
        last_at: float | None,
        counter: int | None,
        now: float,
        kind_breakdown: dict[str, int] | None,
        alive_by: AliveBy | None = None,
        can_defer_override: bool | None = None,
    ) -> ChannelEvidenceSummary:
        """Build a ChannelEvidenceSummary for a single channel."""
        age: float | None = None if last_at is None else max(0.0, now - last_at)
        observed_counter: int | None = counter if counter is not None and counter > 0 else None
        can_defer = (
            can_defer_override
            if can_defer_override is not None
            else CHANNEL_DEFERS_BY_DEFAULT[channel_name]
        )
        return ChannelEvidenceSummary(
            channel_name=channel_name,
            tier=CHANNEL_TIERS[channel_name],
            last_at=last_at,
            age_seconds=age,
            counter=observed_counter,
            kind_breakdown=kind_breakdown,
            alive_by=alive_by,
            can_defer=can_defer,
        )

    def _channel_evidence_active(self, now: float) -> bool:
        """Return True when any quality-filtered channel is fresher than the TTL.

        Consults the full tier-aware evidence summary. First-party channels
        (mcp_tool, subagent_output) always defer when fresh. Side-channel
        channels (workspace, subagent_liveness) only defer when explicitly
        marked ``can_defer=True`` by quality filtering.

        The stdout channel is intentionally excluded: a quiet stdout is the
        NORMAL state we are trying to detect, so it cannot itself defer the
        verdict.
        """
        summary = self.last_evidence_summary(now)
        ttl = self._config.activity_evidence_ttl_seconds
        fresh = summary.first_party_fresh(ttl)
        if fresh is not None:
            return True
        fresh = summary.side_channel_fresh(ttl)
        return fresh is not None

    def _accumulate_waiting_run(self, now: float) -> None:
        """Add elapsed time from the current WAITING run to the cumulative total.

        Called on every transition OUT of the WAITING_ON_CHILD state so the
        cumulative total is preserved across WAITING<->ACTIVE oscillation.
        Double-counting is prevented by only calling this on transitions (not on
        consecutive WAITING evaluations).

        Emits a EXITED event if we were actually in a WAITING run.
        """
        if self._waiting_on_child_started_at is not None:
            elapsed = now - self._waiting_on_child_started_at
            current_run_elapsed = max(0.0, elapsed)
            idle_elapsed = now - self._last_activity
            self._cumulative_waiting_on_child_seconds += current_run_elapsed
            self._emit(
                WaitingStatusKind.EXITED,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
            )
            self._waiting_on_child_started_at = None
            self._last_waiting_status_at = None
            self._suspicion_announced_for_run = False
            self._entry_corroboration = None

    def _safe_corroborate(self) -> CorroborationSnapshot:
        """Call the corroborator safely, returning an empty snapshot on None or error.

        Fail-closed invariant: when the corroborator returns ``None``
        (or any non-``CorroborationSnapshot`` value), normalize to an
        empty ``CorroborationSnapshot`` so callers can safely read
        ``corroboration.alive_by`` without a ``NoneType`` crash. An
        empty snapshot is equivalent to "no live evidence", which is
        the conservative no-defer signal. Callers such as
        ``_evaluate_no_output_at_start`` read ``corroboration.alive_by``
        directly, so a ``None`` return would otherwise raise
        ``AttributeError`` mid-evaluation and break the watchdog
        decision path instead of failing closed.

        Tick-scoped cache: when ``evaluate()`` is running, the
        snapshot captured at the top of the tick (``self._tick_corroboration``)
        is returned on every subsequent ``_safe_corroborate()`` call
        so all sub-evaluators (NO_OUTPUT_AT_START, STRICTLY_STUCK,
        NO_PROGRESS_QUIET, WAITING_ON_CHILD) see the SAME alive_by
        signal on a single tick. The cache is LAZILY populated on the
        first ``_safe_corroborate()`` call inside ``evaluate()`` so the
        strategy's first ``classify_quiet()`` read of shared state
        (e.g. ``ChildLivenessRegistry.prune_stale`` inside the
        corroborator) is NOT pre-empted. Outside of ``evaluate()``
        (external probing, helper access from test code that bypasses
        ``evaluate()``) the cache is ``None`` and the corroborator is
        invoked directly.
        """
        if self._tick_corroboration is not None:
            return self._tick_corroboration
        snapshot = self._call_corroborator_raw()
        self._tick_corroboration = snapshot
        return snapshot

    def _call_corroborator_raw(self) -> CorroborationSnapshot:
        """Invoke the underlying corroborator and normalize the result.

        No caching: this is the bypass path used by ``evaluate()`` to
        populate the tick-scoped cache (``self._tick_corroboration``)
        at the start of every tick. Returns an empty
        ``CorroborationSnapshot`` on ``None``, exceptions, or
        non-``CorroborationSnapshot`` returns (fail-closed).
        """
        if self._corroborator is None:
            return CorroborationSnapshot()
        try:
            # Cast to ``object`` so mypy doesn't narrow ``snapshot`` to
            # ``CorroborationSnapshot`` and reject the defensive
            # ``isinstance`` check below as unreachable. At runtime the
            # corroborator IS typed as ``Callable[[], CorroborationSnapshot]``
            # but the fail-closed invariant requires the isinstance
            # check to remain reachable so a misbehaving corroborator
            # (e.g. one that returns ``None``) is normalized to an empty
            # snapshot instead of crashing downstream callers.
            snapshot = cast("object", self._corroborator())
        except Exception:
            self._log.debug("idle watchdog: corroborator raised (suppressed)")
            return CorroborationSnapshot()
        if not isinstance(snapshot, CorroborationSnapshot):
            self._log.debug(
                "idle watchdog: corroborator returned non-CorroborationSnapshot"
                " (suppressed; treating as empty snapshot)"
            )
            return CorroborationSnapshot()
        return snapshot

    def _build_corroboration_diag(
        self,
        current: CorroborationSnapshot,
    ) -> dict[str, str | int | float | bool]:
        """Build a diagnostic dict comparing current corroboration snapshot to entry baseline."""
        diag: dict[str, str | int | float | bool] = {}
        entry = self._entry_corroboration
        if (
            current.workspace_event_count is not None
            and entry is not None
            and entry.workspace_event_count is not None
        ):
            diag["workspace_event_delta"] = (
                current.workspace_event_count - entry.workspace_event_count
            )
        if current.oldest_child_seconds is not None:
            diag["oldest_child_seconds"] = current.oldest_child_seconds
        if current.scoped_child_active is not None:
            diag["scoped_child_active"] = current.scoped_child_active
        if current.scoped_child_count is not None:
            diag["scoped_child_count"] = current.scoped_child_count
        if (
            current.terminal_child_events_total is not None
            and entry is not None
            and entry.terminal_child_events_total is not None
        ):
            diag["terminal_child_events_since_entry"] = (
                current.terminal_child_events_total - entry.terminal_child_events_total
            )
        if current.last_activity_was_meaningful is False:
            diag["lifecycle_only_activity"] = True
        if current.alive_by is not None:
            diag["alive_by"] = current.alive_by
        return diag

    def _build_evidence_string(
        self,
        diag: dict[str, str | int | float | bool],
    ) -> str:
        """Compose a human-readable evidence label for a SUSPECTED_FROZEN event."""
        suspect = self._config.suspect_waiting_on_child_seconds
        tokens: list[str] = []
        ws_delta = diag.get("workspace_event_delta")
        oldest = diag.get("oldest_child_seconds")
        if (
            isinstance(ws_delta, int | float)
            and ws_delta == 0
            and isinstance(oldest, int | float)
            and suspect is not None
            and oldest >= suspect
        ):
            tokens.append("time_and_workspace_quiet")
        if diag.get("scoped_child_active") is True:
            tokens.append("time_and_scoped_child_active")
        if diag.get("lifecycle_only_activity") is True:
            tokens.append("time_and_lifecycle_only")
        return "+".join(tokens) if tokens else "time_only"

    def _build_evidence_summary_diag(
        self,
        now: float,
    ) -> tuple[dict[str, object], float | None]:
        """Build the per-channel evidence_summary diagnostic block.

        Returns a 2-tuple ``(diag, freshest_age)`` where ``diag`` embeds
        the per-channel ChannelEvidenceSummary dicts under the
        ``evidence_summary`` key, plus a flat ``active_channel`` label
        (the name of the freshest non-stdout channel, or "none" when no
        channel is currently active) and the configured
        ``activity_evidence_ttl_seconds``. ``freshest_age`` is the age
        in seconds of the freshest non-stdout channel currently below
        the TTL (i.e. the channel that is doing the deferral), or
        ``None`` when no non-stdout channel is currently fresh.

        Used by both the verdict hook (for the deferred CONTINUE path)
        and the HARD_STOP diagnostic (for the CHILDREN_PERSIST_TOO_LONG
        path). The freshest_age is surfaced separately so the
        ``_handle_evidence_deferral`` debug log can name the actual
        channel age (not the stdout idle elapsed) as the reason for
        the deferral.
        """
        summary = self.last_evidence_summary(now)
        ttl = self._config.activity_evidence_ttl_seconds
        active_channel = "none"
        freshest_age: float | None = None
        flat: list[dict[str, object]] = []
        for entry in summary.channels:
            flat.append(entry.to_dict())
            if entry.channel_name == ChannelName.STDOUT:
                continue
            if not entry.can_defer:
                continue
            if (
                entry.age_seconds is not None
                and ttl is not None
                and ttl > 0.0
                and entry.age_seconds < ttl
                and (freshest_age is None or entry.age_seconds < freshest_age)
            ):
                freshest_age = entry.age_seconds
                active_channel = entry.channel_name.value
        diag: dict[str, object] = {
            "evidence_summary": cast("list[object]", list(flat)),
            "active_channel": active_channel,
            "activity_evidence_ttl_seconds": ttl,
        }
        return (diag, freshest_age)

    def _emit_fire_log(
        self,
        reason: WatchdogFireReason,
        *,
        now: float,
        idle_elapsed: float,
        message_suffix: str = "",
        **extra_fields: object,
    ) -> None:
        """Emit a fire log with per-channel evidence_summary in loguru extra."""
        evidence_block, _freshest_age = self._build_evidence_summary_diag(now)
        extra_payload: dict[str, object] = {
            "evidence_summary": evidence_block["evidence_summary"],
            "active_channel": evidence_block.get("active_channel", "none"),
            "fire_reason": reason.value,
        }
        extra_payload.update(extra_fields)
        self._log.warning(
            "idle watchdog: FIRE reason={}{} idle_elapsed={}s cumulative_waiting={}s",
            reason,
            message_suffix,
            round(idle_elapsed, 1),
            round(self._cumulative_waiting_on_child_seconds, 1),
            extra=extra_payload,
        )

    def _emit(
        self,
        kind: WaitingStatusKind,
        current_run_seconds: float,
        idle_elapsed: float,
        *,
        ceiling_seconds: float | None = None,
        suspect_threshold_seconds: float | None = None,
        diagnostic: dict[str, str | int | float | bool | list[object]] | None = None,
    ) -> None:
        """Build and dispatch a WaitingStatusEvent to listeners.

        The configured ``WaitingStatusListener`` always receives the event.
        Additionally, any ``subagent_activity`` payload is forwarded to the
        default subagent-activity listener so callers can observe real-time
        subagent progress without implementing a full status listener.

        Never propagates listener exceptions; logs at DEBUG if one is raised.
        """
        main_listener = self._listener
        subagent_listener = self._default_subagent_activity_listener
        if main_listener is None and subagent_listener is None:
            return
        candidate_total = self._cumulative_waiting_on_child_seconds + current_run_seconds
        _suspect = (
            suspect_threshold_seconds
            if suspect_threshold_seconds is not None
            else self._config.suspect_waiting_on_child_seconds
        )
        event = WaitingStatusEvent(
            kind=kind,
            cumulative_seconds=candidate_total,
            current_run_seconds=current_run_seconds,
            idle_elapsed_seconds=idle_elapsed,
            ceiling_seconds=(
                self._config.max_waiting_on_child_seconds
                if ceiling_seconds is None
                else ceiling_seconds
            ),
            suspect_threshold_seconds=_suspect,
            diagnostic=dict(diagnostic) if diagnostic else {},
            subagent_activity=self._last_subagent_progress_description,
        )
        if main_listener is not None:
            try:
                main_listener(event)
            except Exception:
                self._log.debug("idle watchdog: listener raised (suppressed)")
        if event.subagent_activity is not None:
            if subagent_listener is not None:
                try:
                    subagent_listener(event)
                except Exception:
                    self._log.debug(
                        "idle watchdog: default subagent activity listener raised (suppressed)"
                    )
            else:
                self._log.info(
                    "idle watchdog: subagent activity: {}",
                    event.subagent_activity,
                )

    def evaluate(
        self,
        *,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        """Evaluate whether the watchdog should fire, wait, or continue.

        The session ceiling is checked first (before idle deadline) because it
        is absolute and activity cannot reset it.

        Args:
            classify_quiet: Called only when the idle deadline has elapsed; returns
                the current AgentExecutionState to distinguish child-wait from stall.
                Also called on every drain-window tick to detect newly appearing
                children (which abort the drain and resume deferral).

        Returns:
            CONTINUE: keep running normally.
            WAITING_ON_CHILD: idle deadline elapsed; children still active; last_activity not reset.
            FIRE: idle deadline elapsed with no valid deferral; caller must terminate.
        """
        now = self._clock.monotonic()
        # Store the most recent classify_quiet callable so the gate
        # (``_gate_fire`` -> ``_classify_stuck_now``) can consult the
        # classifier's ``WAITING_ON_CHILD`` / ``RESUMABLE_CONTINUE``
        # branches with the same live signal the rest of ``evaluate()``
        # is using. A noop stub would force those branches to never
        # fire, which is the bug the analysis feedback called out.
        self._classify_quiet_provider = classify_quiet

        # Arm the tick-scoped corroboration cache sentinel ``None`` for
        # this ``evaluate()`` call. The cache is lazily populated on the
        # FIRST ``_safe_corroborate()`` call inside ``_evaluate_inner``
        # so the corroborator's side-effects (e.g. registry
        # ``prune_stale``) do NOT pre-empt the strategy's first
        # ``classify_quiet()`` read of the same registry. This preserves
        # the historical "strategy first, corroborator second" call
        # order that ``test_stale_scoped_child_evidence_fires_no_output_deadline``
        # pins while still reusing one snapshot across the WAITING_ON_CHILD
        # path's entry/ceiling/diagnostic reads inside a single tick
        # (``test_single_tick_corroboration_snapshot_reused_for_all_decisions_and_diagnostics``).
        self._tick_corroboration = None
        try:
            return self._evaluate_inner(
                now=now,
                classify_quiet=classify_quiet,
            )
        finally:
            self._tick_corroboration = None
    def _evaluate_inner(  # noqa: PLR0911 - gate + 5 sub-evaluators; each is a distinct verdict path
        self,
        *,
        now: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        """Inner ``evaluate()`` body. Runs inside the tick-scoped cache lifetime.

        ``evaluate()`` arms ``self._tick_corroboration = None`` for this
        call. ``_safe_corroborate()`` lazily populates the cache on the
        first read, then returns the cached snapshot for every
        subsequent read so all sub-evaluators (NO_OUTPUT_AT_START,
        STRICTLY_STUCK, NO_PROGRESS_QUIET, WAITING_ON_CHILD) and
        diagnostic surfaces on this tick see the SAME alive_by signal.
        Outside of ``evaluate()`` (external probing, helper access from
        test code that bypasses ``evaluate()``) the cache is ``None``
        and the corroborator is invoked directly.
        """
        # Poll observable subagent output streams before any verdict so fresh
        # subagent output is treated as first-party activity on this tick.
        self.poll_subagent_output(now=now)

        fire_reason: WatchdogFireReason | None = None
        # The session ceiling is the highest-priority fire reason
        # (operator-set hard cap, absolute). It MUST be checked
        # first so a session-ceiling fire always wins over a
        # concurrent repeated-error-loop fire. Both checks below
        # are independent: REPEATED_ERROR_LOOP is a wedged
        # retry-loop signal that is gated by the smart-verdict
        # gate, while SESSION_CEILING_EXCEEDED bypasses the gate
        # (see ``_gate_fire``). Prior versions used an ``elif``
        # here, which made the repeated-error breaker unreachable
        # whenever ``max_session_seconds`` was configured (the
        # default production configuration sets the session
        # ceiling via ``GeneralConfig.agent_max_session_seconds``,
        # so the breaker was silently disabled in normal runs).
        if self._config.max_session_seconds is not None:
            session_elapsed = now - self._session_started_at
            if session_elapsed >= self._config.max_session_seconds:
                fire_reason = WatchdogFireReason.SESSION_CEILING_EXCEEDED
        if fire_reason is None and self._repetition_tracker.tripped():
            # Two independent repetition dimensions share the same
            # consecutive + window thresholds.  When BOTH dimensions
            # are tripped the error dimension wins (the canonical
            # ``REPEATED_ERROR_LOOP`` reason).  When ONLY the
            # tool-call dimension is tripped the watchdog fires
            # ``REPEATED_IDENTICAL_TOOL_CALL`` so the failure
            # classifier sees a precise cause.
            if self._repetition_tracker.tripped_tool_dimension():
                fire_reason = WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL
            else:
                fire_reason = WatchdogFireReason.REPEATED_ERROR_LOOP
        if fire_reason is not None:
            idle_elapsed = now - self._last_activity
            # Smart-verdict gate: SESSION_CEILING_EXCEEDED is the only
            # absolute reason and bypasses the gate. REPEATED_ERROR_LOOP
            # is gated because a wedged retry loop is a stuck-detection
            # signal, not an operator-set hard cap.
            gate_verdict = self._gate_fire(fire_reason, now=now, idle_elapsed=idle_elapsed)
            if gate_verdict == WatchdogVerdict.FIRE:
                self._emit_fire_log(
                    fire_reason,
                    now=now,
                    idle_elapsed=idle_elapsed,
                    message_suffix=(
                        f" session_elapsed={round(now - self._session_started_at, 1)}s"
                        if fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
                        else ""
                    ),
                )
                self._last_fire_reason = fire_reason
                return WatchdogVerdict.FIRE
            return WatchdogVerdict.CONTINUE

        idle_elapsed = now - self._last_activity
        quiet_state = classify_quiet()
        no_output_at_start_verdict = self._evaluate_no_output_at_start(
            now, idle_elapsed, classify_quiet
        )
        if no_output_at_start_verdict is not None:
            return no_output_at_start_verdict

        if self._config.idle_timeout_seconds is None:
            return WatchdogVerdict.CONTINUE

        # STRICTLY_STUCK orthogonal ceiling. Engages BEFORE the
        # idle_timeout_seconds check so a stuck-but-alive job whose
        # idle_timeout has already elapsed is caught by the strictly-
        # stuck ceiling (which is tuned for that exact case) rather
        # than by the generic NO_OUTPUT_DEADLINE path. The corroborator
        # is consumed via the safe-normalize seam so a missing or
        # misbehaving corroborator falls through to an empty snapshot
        # (no alive_by) and the ceiling does not engage.
        strictly_stuck_verdict = self._evaluate_strictly_stuck(
            now,
            idle_elapsed,
            corroboration=self._safe_corroborate(),
        )
        if strictly_stuck_verdict is not None:
            return strictly_stuck_verdict

        if (
            quiet_state == AgentExecutionState.WAITING_ON_CHILD
            and self._config.no_progress_quiet_seconds is not None
        ):
            no_progress_verdict = self._evaluate_no_progress_quiet(now, idle_elapsed)
            if no_progress_verdict is not None:
                return no_progress_verdict

        if idle_elapsed < self._config.idle_timeout_seconds:
            self._accumulate_waiting_run(now)
            return WatchdogVerdict.CONTINUE

        verdict = self._evaluate_final_verdict(now, idle_elapsed, classify_quiet)
        return verdict

    def _handle_evidence_deferral(
        self,
        now: float,
        idle_elapsed: float,
    ) -> WatchdogVerdict:
        """Defer a NO_OUTPUT_DEADLINE fire while a non-stdout channel is fresh.

        Called from ``evaluate()`` when the idle deadline has elapsed and the
        post-tool-result wedge has NOT fired, but at least one non-stdout
        channel (mcp_tool, subagent, workspace) is fresher than
        ``activity_evidence_ttl_seconds``. The watchdog returns CONTINUE
        with a debug log naming the active channel, and the cumulative
        WAITING_ON_CHILD ceiling is NOT advanced (deferral is independent
        of waiting-on-child state — a productive session that emits no
        stdout but is busy on a non-stdout channel is not a 'child wait').

        This is the activity-aware verdict path. The SESSION_CEILING and
        CHILDREN_PERSIST_TOO_LONG ceilings are checked BEFORE this hook
        in ``evaluate()`` and remain absolute.
        """
        summary, freshest_age = self._build_evidence_summary_diag(now)
        active_channel_value = summary.get("active_channel", "none")
        channel_label = active_channel_value if isinstance(active_channel_value, str) else "none"
        # The 'age=' field is the age of the FRESHEST non-stdout channel
        # (i.e. the channel that is doing the deferral). When no channel
        # is fresh we fall back to idle_elapsed so the log always shows
        # a finite number; in that case the channel label is 'none' and
        # the log line still tells the operator why the verdict was
        # deferred (or, for 'none', that the deferral was driven by
        # some channel the helper did not enumerate).
        age_for_log = round(freshest_age, 1) if freshest_age is not None else round(idle_elapsed, 1)
        self._log.debug(
            "idle watchdog: deferred via activity evidence channel={} age={}s idle_elapsed={}s",
            channel_label,
            age_for_log,
            round(idle_elapsed, 1),
        )
        return WatchdogVerdict.CONTINUE

    def _post_tool_result_stalled(self, now: float, idle_elapsed: float) -> WatchdogVerdict | None:
        """Return the verdict when post-tool-result progression has stalled long enough.

        Returns ``None`` when the post-tool-result stall check is not
        applicable (no tool result, not awaiting progression, or the
        stall window has not yet elapsed). Returns ``WatchdogVerdict.FIRE``
        when the stall has been confirmed and the gate allowed the fire.
        Returns ``WatchdogVerdict.CONTINUE`` when the stall has been
        confirmed but the StuckClassifier gate deferred the fire (e.g.
        the agent is in a waiting state, the network is offline, or a
        first-party channel is fresh).

        The gate is consulted BEFORE the fire reason is set and BEFORE
        the log is emitted so a deferred fire leaves no diagnostic trace
        that suggests an actual fire.
        """
        if (
            self._config.post_tool_result_progression_seconds is None
            or not self._awaiting_post_tool_result_progression
            or self._last_tool_result_at is None
        ):
            return None
        since_tool_result = now - self._last_tool_result_at
        if since_tool_result < self._config.post_tool_result_progression_seconds:
            return None
        gate_verdict = self._gate_fire(
            WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
            now=now,
            idle_elapsed=idle_elapsed,
        )
        if gate_verdict == WatchdogVerdict.CONTINUE:
            return WatchdogVerdict.CONTINUE
        self._last_fire_reason = WatchdogFireReason.STALLED_AFTER_TOOL_RESULT
        self._emit_fire_log(
            WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
            now=now,
            idle_elapsed=idle_elapsed,
            message_suffix=f" since_tool_result={round(since_tool_result, 1)}s",
        )
        return WatchdogVerdict.FIRE

    def _handle_drain_window(
        self,
        now: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        """Handle evaluation while in the drain window.

        Re-consults classify_quiet on every tick. If children appear during the
        drain window, the drain is abandoned and we fall back to WAITING_ON_CHILD
        deferral to prevent false-positive fires while children are alive.
        """
        assert self._drain_started_at is not None

        quiet_state = classify_quiet()
        if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
            self._in_drain_window = False
            self._drain_started_at = None
            self._log.info(
                "idle watchdog: drain window abandoned"
                " (children reappeared), switching to WAITING_ON_CHILD"
            )
            return self._handle_waiting_branch(now, classify_quiet)

        drain_elapsed = now - self._drain_started_at
        if drain_elapsed < self._config.drain_window_seconds:
            self._log.debug(
                "idle watchdog: drain window active drain_elapsed={}s window={}s",
                round(drain_elapsed, 3),
                self._config.drain_window_seconds,
            )
            return WatchdogVerdict.CONTINUE

        idle_elapsed = now - self._last_activity
        gate_verdict = self._gate_fire(
            WatchdogFireReason.NO_OUTPUT_DEADLINE, now=now, idle_elapsed=idle_elapsed
        )
        if gate_verdict == WatchdogVerdict.CONTINUE:
            return WatchdogVerdict.CONTINUE
        self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_DEADLINE
        self._emit_fire_log(
            WatchdogFireReason.NO_OUTPUT_DEADLINE,
            now=now,
            idle_elapsed=idle_elapsed,
        )
        return WatchdogVerdict.FIRE

    _NON_PROGRESS_ALIVE_BY_VALUES = frozenset(
        [
            AliveBy.FRESH_HEARTBEAT_ONLY,
            AliveBy.STALE_LABEL_ONLY,
            AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            AliveBy.CPU_IDLE_WHILE_ALIVE,
            AliveBy.LOG_STALE_WHILE_ALIVE,
        ]
    )

    def _effective_waiting_ceiling(
        self,
        corroboration: CorroborationSnapshot,
    ) -> float:
        """Compute the effective waiting ceiling based on corroboration.

        Returns the shorter no-progress ceiling when the child is alive but not
        making forward progress (heartbeat-only, stale-label, or OS-descendant-only).
        Returns the standard full ceiling when the child is making progress or when
        the no-progress ceiling is disabled (None).
        """
        alive_by = corroboration.alive_by
        _effective = self._config.max_waiting_on_child_seconds
        if alive_by is None:
            return _effective
        if alive_by == AliveBy.FRESH_PROGRESS:
            return _effective
        _os_desc_only = alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
        if _os_desc_only and self._config.os_descendant_only_ceiling_seconds is not None:
            if self._config.max_waiting_on_child_no_progress_seconds is not None:
                _effective = min(
                    self._config.os_descendant_only_ceiling_seconds,
                    self._config.max_waiting_on_child_no_progress_seconds,
                )
            else:
                _effective = self._config.os_descendant_only_ceiling_seconds
        elif (
            self._config.max_waiting_on_child_no_progress_seconds is not None
            and alive_by in self._NON_PROGRESS_ALIVE_BY_VALUES
        ):
            _effective = self._config.max_waiting_on_child_no_progress_seconds
        return _effective

    def _effective_ceiling_label(
        self,
        corroboration: CorroborationSnapshot,
        effective_ceiling: float,
    ) -> str:
        alive_by = corroboration.alive_by
        if alive_by is None:
            return "standard"
        if alive_by == AliveBy.FRESH_PROGRESS:
            return "standard"
        if (
            alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
            and self._config.os_descendant_only_ceiling_seconds is not None
            and effective_ceiling == self._config.os_descendant_only_ceiling_seconds
        ):
            return "os_descendant_only"
        if effective_ceiling < self._config.max_waiting_on_child_seconds:
            return "no_progress"
        return "standard"

    def _compute_effective_suspect(
        self,
        alive_by: AliveBy | None,
        candidate_total: float,
    ) -> tuple[float | None, str]:
        if self._config.suspect_waiting_on_child_seconds is None:
            return None, "standard"
        _os_desc_only = alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
        if _os_desc_only and self._config.os_descendant_only_suspect_seconds is not None:
            eff = min(
                self._config.suspect_waiting_on_child_seconds,
                self._config.os_descendant_only_suspect_seconds,
            )
            return eff, "os_descendant_only"
        return self._config.suspect_waiting_on_child_seconds, "standard"

    def _handle_waiting_branch(  # noqa: PLR0912, PLR0915 - 5 orchestrated reasons + gate path
        self,
        now: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        """Handle the WAITING_ON_CHILD deferral branch.

        Accumulates time within the current run WITHOUT mutating the cumulative
        total (which is only updated on transition out of WAITING). The ceiling
        check uses cumulative + current-run total to avoid double-counting.

        Emits structured status events (ENTERED, PROGRESS, SUSPECTED_FROZEN,
        HARD_STOP) rather than per-tick debug spam. Status emission cadence is
        governed by waiting_status_interval_seconds and does NOT affect ceiling math.

        When max_waiting_on_child_no_progress_seconds is set and corroboration shows
        non-progress evidence (heartbeat-only, stale-label, or OS-descendant-only),
        the shorter no-progress ceiling is used instead of the full ceiling.

        The execution strategy is re-consulted on every tick so that a run
        that entered WAITING_ON_CHILD while a child was demonstrably active
        transitions back to the normal idle path as soon as the child evidence
        goes stale, rather than lingering until the larger cumulative ceiling.
        """
        idle_elapsed = now - self._last_activity
        if self._waiting_on_child_started_at is None:
            self._entry_corroboration = self._safe_corroborate()
            self._waiting_on_child_started_at = now
            self._last_waiting_status_at = now
            self._suspicion_announced_for_run = False
            self._log.info(
                "idle watchdog: entering WAITING_ON_CHILD deferral idle_elapsed={}s cumulative={}s",
                round(idle_elapsed, 1),
                round(self._cumulative_waiting_on_child_seconds, 1),
            )
            entry_ceiling = self._effective_waiting_ceiling(self._entry_corroboration)
            self._emit(
                WaitingStatusKind.ENTERED,
                current_run_seconds=0.0,
                idle_elapsed=idle_elapsed,
                ceiling_seconds=entry_ceiling,
            )

        current_run_elapsed = now - self._waiting_on_child_started_at
        candidate_total = self._cumulative_waiting_on_child_seconds + current_run_elapsed

        # Re-consult the execution strategy: if the child evidence is no
        # longer fresh, transition out of WAITING_ON_CHILD and let the
        # normal idle path (or activity-channel deferral) decide. This
        # prevents a stale/dead child from stretching the wait until the
        # cumulative ceiling.
        try:
            current_quiet_state = classify_quiet()
        except Exception:
            current_quiet_state = AgentExecutionState.WAITING_ON_CHILD
        if current_quiet_state != AgentExecutionState.WAITING_ON_CHILD:
            self._accumulate_waiting_run(now)
            if self._channel_evidence_active(now):
                return self._handle_evidence_deferral(now, idle_elapsed)
            return self._handle_active_branch(now)

        current_corr = self._safe_corroborate()
        effective_ceiling = self._effective_waiting_ceiling(current_corr)

        alive_by = current_corr.alive_by
        _os_desc_only = alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
        _os_desc_only_suspect = (
            self._config.os_descendant_only_suspect_seconds is not None if _os_desc_only else False
        )
        effective_suspect, suspect_reason = self._compute_effective_suspect(
            alive_by, candidate_total
        )

        if candidate_total >= effective_ceiling:
            gate_verdict = self._gate_fire(
                WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
                now=now,
                idle_elapsed=idle_elapsed,
                corroboration=current_corr,
            )
            if gate_verdict == WatchdogVerdict.CONTINUE:
                return WatchdogVerdict.CONTINUE
            self._last_fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
            corr_diag_hs = self._build_corroboration_diag(current_corr)
            corr_diag_hs["evidence"] = self._build_evidence_string(corr_diag_hs)
            _ceiling_lbl = self._effective_ceiling_label(current_corr, effective_ceiling)
            diag: dict[str, object] = {
                "cumulative": round(candidate_total, 1),
                "run_elapsed": round(current_run_elapsed, 1),
                "idle_elapsed": round(idle_elapsed, 1),
                "effective_ceiling": effective_ceiling,
                "effective_ceiling_label": _ceiling_lbl,
            }
            if effective_suspect is not None:
                diag["suspect_threshold"] = effective_suspect
            for key, value in corr_diag_hs.items():
                if key not in diag:
                    diag[key] = value
            evidence_block, _freshest_age = self._build_evidence_summary_diag(now)
            for ev_key, ev_value in evidence_block.items():
                if ev_key not in diag:
                    diag[ev_key] = ev_value
            self._emit(
                WaitingStatusKind.HARD_STOP,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
                ceiling_seconds=effective_ceiling,
                suspect_threshold_seconds=effective_suspect,
                diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
            )
            self._log.warning(
                "idle watchdog: FIRE reason={} idle_elapsed={}s cumulative_waiting={}s",
                WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
                round(idle_elapsed, 1),
                round(candidate_total, 1),
            )
            return WatchdogVerdict.FIRE

        if (
            effective_suspect is not None
            and not self._suspicion_announced_for_run
            and candidate_total >= effective_suspect
        ):
            self._suspicion_announced_for_run = True
            corr_diag_sf = self._build_corroboration_diag(current_corr)
            corr_diag_sf["evidence"] = self._build_evidence_string(corr_diag_sf)
            corr_diag_sf["suspect_reason"] = suspect_reason
            corr_diag_sf["suspect_threshold"] = effective_suspect
            _ceiling_lbl = self._effective_ceiling_label(current_corr, effective_ceiling)
            corr_diag_sf["effective_ceiling_label"] = _ceiling_lbl
            self._log.warning(
                "idle watchdog: SUSPECTED_FROZEN candidate_total={}s suspect={}s ceiling={}s",
                round(candidate_total, 1),
                effective_suspect,
                effective_ceiling,
            )
            self._emit(
                WaitingStatusKind.SUSPECTED_FROZEN,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
                ceiling_seconds=effective_ceiling,
                suspect_threshold_seconds=effective_suspect,
                diagnostic=cast("dict[str, str | int | float | bool | list[object]]", corr_diag_sf),
            )

        assert self._last_waiting_status_at is not None
        if now - self._last_waiting_status_at >= self._config.waiting_status_interval_seconds:
            self._last_waiting_status_at = now
            corr_diag_pr = self._build_corroboration_diag(current_corr)
            _ceiling_lbl = self._effective_ceiling_label(current_corr, effective_ceiling)
            corr_diag_pr["effective_ceiling"] = effective_ceiling
            corr_diag_pr["effective_ceiling_label"] = _ceiling_lbl
            self._log.info(
                "idle watchdog: WAITING_ON_CHILD progress cumulative={}s ceiling={}s",
                round(candidate_total, 1),
                round(effective_ceiling, 1),
            )
            self._emit(
                WaitingStatusKind.PROGRESS,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
                ceiling_seconds=effective_ceiling,
                diagnostic=cast("dict[str, str | int | float | bool | list[object]]", corr_diag_pr),
            )

        # SUBAGENT_PROGRESS waiting-status event. Surfaces the
        # most-recent subagent activity description AND the live
        # subagent count from the process monitor in the waiting-
        # status stream so operators see what the dispatched
        # subagent is doing in real time. This REUSES the existing
        # parser-layer ``ActivityEventKind.SUBAGENT_PROGRESS``
        # surface (``self._last_subagent_progress_description``
        # updated via ``record_subagent_work`` and the
        # ``_process_monitor.live_subagent_count()`` process-tree
        # signal) -- it does NOT introduce a new per-worker log
        # poll. The emit is rate-limited by
        # ``watchdog_subagent_progress_interval_seconds`` (30 s
        # default, matching the existing PROGRESS cadence) so
        # the new event does NOT introduce additional churn.
        # The predicate is: emit only when EITHER a subagent
        # observation has been recorded OR the process monitor
        # reports a live subagent count > 0 -- both surfaces
        # are agent-agnostic (no per-worker log discovery).
        live_subagent_count = (
            self._process_monitor.live_subagent_count()
            if self._process_monitor is not None
            else 0
        )
        if (
            self._last_subagent_progress_description is not None
            or live_subagent_count > 0
        ) and (
            self._last_subagent_progress_emit_at is None
            or (now - self._last_subagent_progress_emit_at)
            >= self._config.watchdog_subagent_progress_interval_seconds
        ):
            self._last_subagent_progress_emit_at = now
            subagent_diag: dict[str, object] = {
                "live_subagent_count": live_subagent_count,
                "subagent_progress_count": self._subagent_progress_count,
                "last_subagent_progress_at": self._last_subagent_progress_at,
            }
            if self._last_subagent_progress_description is not None:
                subagent_diag["subagent_activity"] = _sanitize_subagent_description(
                    self._last_subagent_progress_description
                )[:200]
            self._emit(
                WaitingStatusKind.SUBAGENT_PROGRESS,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
                ceiling_seconds=effective_ceiling,
                diagnostic=cast(
                    "dict[str, str | int | float | bool | list[object]]",
                    subagent_diag,
                ),
            )

        return WatchdogVerdict.WAITING_ON_CHILD

    def _handle_active_branch(self, now: float) -> WatchdogVerdict:
        """Handle the case where the agent appears active (no children visible).

        Accumulates any elapsed WAITING run time before entering the drain window.
        When drain_window_seconds=0, fires immediately without a drain window.
        """
        idle_elapsed = now - self._last_activity
        self._accumulate_waiting_run(now)
        if self._config.drain_window_seconds == 0.0:
            gate_verdict = self._gate_fire(
                WatchdogFireReason.NO_OUTPUT_DEADLINE,
                now=now,
                idle_elapsed=idle_elapsed,
            )
            if gate_verdict == WatchdogVerdict.CONTINUE:
                return WatchdogVerdict.CONTINUE
            self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_DEADLINE
            self._emit_fire_log(
                WatchdogFireReason.NO_OUTPUT_DEADLINE,
                now=now,
                idle_elapsed=idle_elapsed,
            )
            return WatchdogVerdict.FIRE
        self._in_drain_window = True
        self._drain_started_at = now
        self._log.info(
            "idle watchdog: entering drain window idle_elapsed={}s cumulative_waiting={}s",
            round(idle_elapsed, 1),
            round(self._cumulative_waiting_on_child_seconds, 1),
        )
        return WatchdogVerdict.CONTINUE

    def _evaluate_final_verdict(
        self,
        now: float,
        idle_elapsed: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        """Compute the final verdict after idle timeout.

        Called from evaluate() when the idle deadline has elapsed. Handles
        drain_window, post-tool stall, waiting branch, evidence deferral,
        and active branch cases.
        """
        if self._in_drain_window:
            return self._handle_drain_window(now, classify_quiet)
        post_tool_verdict = self._post_tool_result_stalled(now, idle_elapsed)
        if post_tool_verdict is not None:
            return post_tool_verdict
        quiet_state = classify_quiet()
        if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
            return self._handle_waiting_branch(now, classify_quiet)
        if self._channel_evidence_active(now):
            return self._handle_evidence_deferral(now, idle_elapsed)
        return self._handle_active_branch(now)
