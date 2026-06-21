"""Template-method base class for agent output parsers.

Provides the shared ``parse`` template, ``parse_json_line`` for standard
JSON-vs-raw dispatch, ``is_stop_event`` for stop-type checking, and
``flush_accumulators`` for draining pending text accumulators.

Subclasses override ``classify_line`` for parser-specific line dispatch
and optionally ``flush_accumulators`` when the accumulator layout differs
from the standard dict-based pattern.

The shared ``emit_subagent_activity(line, sink)`` hook feeds per-parser
subagent progress into the idle watchdog's subagent sink via the
``invoke_subagent_sink`` contextvar.  See
``_sanitize_parser_subagent_summary`` for the sanitization rules.
"""

from __future__ import annotations

import json
import re
from json import JSONDecodeError
from typing import TYPE_CHECKING, ClassVar, cast

from .agent_output_line import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from .text_accumulator import TextAccumulator

__all__ = ["ParserTemplateBase"]

# Lines whose type is "demonstrable work" (tool call, tool result, model
# text, model thinking).  These are the ONLY line kinds the parser hook
# forwards to the subagent sink so a hard-failing child (error), a
# lifecycle event (message_start), or a raw fallback (raw) does not keep
# the watchdog deferring a kill.
_EMITTABLE_TYPES: frozenset[str] = frozenset({"tool_use", "tool_result", "text", "thinking"})

# Hard cap on the emitted summary length so a chatty model cannot push
# a 4 KB payload into the operator-visible subagent_activity field.
# Mirrors the watchdog's ``_SUBAGENT_DESCRIPTION_MAX = 200`` constant.
_MAX_SUMMARY_LENGTH: int = 200
_MAX_PREFIX_LENGTH: int = 80


# Sanitization patterns.  These mirror the watchdog's
# ``_sanitize_subagent_description`` rules so the parser layer and the
# watchdog layer never disagree about what an operator-visible summary
# may contain.  See ``idle_watchdog.py:_sanitize_subagent_description``
# for the full rationale.

# Control characters (C0 + DEL) and ANSI/CSI/OSC sequences.
_CONTROL_CHARS_RE: re.Pattern[str] = re.compile(
    r"[\x00-\x1f\x7f]|\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07"
)

# JSON keys whose value is sensitive (mirrors
# ``_SENSITIVE_JSON_KEYS`` in idle_watchdog.py).  Lookup is
# case-insensitive so mixed-case provider keys such as ``Prompt`` /
# ``Arguments`` / ``Input`` / ``Content`` are redacted like their
# lowercase variants.
_SENSITIVE_JSON_KEYS: frozenset[str] = frozenset(
    {"arguments", "args", "file_path", "input", "prompt", "content"}
)

# Bare-path prefixes that are sensitive.
_SENSITIVE_PATH_TOKEN_RE: re.Pattern[str] = re.compile(
    r"""
    (?:/etc/|/proc/|/sys/|/root/|~/\.ssh/)[^\s\x1b\n]*
    |
    (?i:authorization)\s*:\s*(?i:bearer)[^\n]*
    |
    -----BEGIN\s+[A-Z ]*PRIVATE\s+KEY-----[^\n]*
    """,
    re.VERBOSE,
)

# Fallback pattern for malformed JSON values (mirrors
# ``_SENSITIVE_MARKER_FALLBACK_RE``).  ``(?i)`` makes the key names
# case-insensitive so mixed-case provider keys are redacted like
# their lowercase variants.
_SENSITIVE_MARKER_FALLBACK_RE: re.Pattern[str] = re.compile(
    r"""
    "(?i:arguments|args|file_path|input|prompt|content)"\s*:\s*"
    .*?
    (?=[,\}\]\n]|$)
    """,
    re.VERBOSE | re.DOTALL,
)


def _redact_json_values(obj: object) -> object:
    """Walk a parsed JSON structure and redact sensitive key values.

    Mirrors the watchdog's ``_redact_json_values`` helper: a sensitive
    key (``arguments``, ``args``, ``file_path``, ``input``, ``prompt``,
    ``content``) is fully replaced with the literal ``<redacted>`` so
    the surrounding JSON remains well-formed and non-sensitive sibling
    fields cannot leak.
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


_JSON_DECODER = json.JSONDecoder(strict=False)


def _decode_json_at(text: str, pos: int) -> tuple[object, int]:
    """Parse a JSON object/array starting at ``pos`` and return ``(value, end_offset)``.

    On parse failure, returns ``(None, -1)`` so the caller can fall
    through to character-level emission. Mirrors the watchdog's
    ``_decode_json_at`` helper so the parser-layer and watchdog-layer
    JSON fragment scanners behave identically.
    """
    try:
        decoded = cast("tuple[object, int]", _JSON_DECODER.raw_decode(text, pos))
    except (json.JSONDecodeError, ValueError):
        return None, -1
    value, end = decoded
    return value, end


def _redact_json_fragments(text: str) -> str:
    """Walk ``text`` and redact every JSON object/array it contains.

    Mirrors the watchdog's ``_redact_json_fragments`` helper: scan for
    ``{`` / ``[`` bytes, try to parse a JSON fragment at each position,
    and use ``_redact_json_values`` to replace sensitive key values with
    ``<redacted>``. This catches embedded JSON fragments after free-form
    text and nested objects/lists under mixed-case sensitive keys.
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


def _sanitize_parser_subagent_summary(line: str) -> str:
    """Sanitize a one-line subagent summary for operator-visible output.

    Mirrors the watchdog's ``_sanitize_subagent_description`` rules:
    strips control characters, redacts sensitive JSON fragments
    (well-formed, malformed, and embedded), redacts sensitive path
    roots, redacts bearer tokens, and caps the result at
    ``_MAX_SUMMARY_LENGTH`` so the operator-visible subagent_activity
    field never echoes raw tool arguments / file paths / bearer tokens.

    Returns the sanitized string (which may be empty when the input
    was empty or only whitespace).
    """
    if not line:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub("", line)
    cleaned = _redact_json_fragments(cleaned)
    cleaned = _SENSITIVE_MARKER_FALLBACK_RE.sub("<redacted>", cleaned)
    cleaned = _SENSITIVE_PATH_TOKEN_RE.sub("<redacted>", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > _MAX_SUMMARY_LENGTH:
        cleaned = cleaned[:_MAX_SUMMARY_LENGTH]
    return cleaned


def _tool_name_from_line(line: AgentOutputLine) -> str:
    """Return the tool name for a tool_use / tool_result line, or 'unknown'.

    Tries ``metadata.tool``, ``metadata.tool_name``, then ``metadata.name``
    (the field Claude's content blocks use).  Falls back to the literal
    ``"unknown"`` so the emitted summary is always well-formed and never
    echoes raw tool-result content when metadata is missing.
    """
    metadata = line.metadata or {}
    candidate = metadata.get("tool") or metadata.get("tool_name") or metadata.get("name")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return "unknown"


class ParserTemplateBase:
    """Template-method base for agent output parsers.

    Class attributes (override in subclasses):
        _STOP_EVENT_TYPES: Known stop-event type names.

    Instance attributes (used by the default implementation):
        _accumulators: dict mapping keys to TextAccumulator instances.
    """

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset()

    _accumulators: dict[str, TextAccumulator]

    def __init__(self) -> None:
        self._accumulators = {}

    def emit_subagent_activity(
        self,
        line: AgentOutputLine,
        sink: Callable[[str], None],
    ) -> None:
        """Forward a parsed line to the watchdog's subagent sink.

        Default hook for every NDJSON parser that inherits from this
        template.  Builds a one-line ``<type>:<summary>`` summary
        from the line's type and metadata, sanitizes it via
        :func:`_sanitize_parser_subagent_summary`, and invokes
        ``sink(summary)`` once per line.

        The hook fires ONLY for the four "demonstrable work" line
        kinds: ``tool_use``, ``tool_result``, ``text``, ``thinking``.
        Lifecycle events, error lines, raw fallbacks, stop markers,
        and unknown types do NOT invoke the sink (a hard-failing
        child or a lifecycle heartbeat does not count as forward
        progress and must not keep the watchdog deferring a kill).

        Sink exceptions are swallowed so a buggy sink cannot crash
        the activity stream; the watchdog already logs recorder
        errors at DEBUG and the contract is "fail soft".

        Subclasses may override this hook to add parser-specific
        metadata enrichment (e.g. ``metadata.tool`` resolution
        from a different field).  The default implementation is
        deliberately conservative: it surfaces ``tool_use`` /
        ``tool_result`` line types, ``text`` first-80-chars, and
        ``thinking`` first-80-chars with the same sanitization
        rules the watchdog uses internally so operator-visible
        summaries are consistent end-to-end.
        """
        if line.type not in _EMITTABLE_TYPES:
            return
        tool_name = _tool_name_from_line(line)
        if line.type in {"tool_use", "tool_result"}:
            summary = f"{line.type}:{tool_name}"
        else:
            content = line.content.strip()
            truncated = (
                content[:_MAX_PREFIX_LENGTH] if len(content) > _MAX_PREFIX_LENGTH else content
            )
            summary = f"{line.type}:{truncated}"
        summary = _sanitize_parser_subagent_summary(summary)
        if not summary:
            return
        try:
            sink(summary)
        except Exception:
            return

    def parse_json_line(self, line: str) -> AgentOutputLine | None:
        """Try to parse *line* as JSON and classify the result.

        Returns:
            * An ``AgentOutputLine(type='raw', ...)`` when the line is not valid
              JSON or is a JSON non-dict value (e.g. a bare string or number).
            * The return value of ``self._classify_json_object(...)`` when the
              line is a valid JSON dict.
            * ``None`` when ``_classify_json_object`` returns ``None``.
        """
        stripped = line.strip()
        try:
            parsed: object = json.loads(stripped, strict=False)
        except JSONDecodeError:
            return AgentOutputLine(type="raw", content=stripped, raw=line)
        if not isinstance(parsed, dict):
            return AgentOutputLine(type="raw", content=stripped, raw=line)
        return self._classify_json_object(cast("dict[str, object]", parsed), line)

    def _classify_json_object(
        self,
        parsed: dict[str, object],
        raw: str,
    ) -> AgentOutputLine | None:
        """Subclass hook: classify a successfully parsed JSON dict.

        The default implementation returns ``None``, signalling that the JSON
        object was not handled by the template's simple path.  Subclasses may
        return an ``AgentOutputLine`` or ``None``.
        """
        return None

    def is_stop_event(self, event_type: str) -> bool:
        """Return True if *event_type* is a known stop marker."""
        return event_type in self._STOP_EVENT_TYPES

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        """Flush all pending text accumulators and yield their content."""
        for key in list(self._accumulators.keys()):
            acc = self._accumulators.pop(key)
            yield from acc.flush(kind="text")

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        """Subclass hook: classify a single raw line and yield output lines.

        This is the primary extension point for parser-specific line dispatch.
        The default implementation calls ``parse_json_line`` and yields the
        result if non-``None``.
        """
        result = self.parse_json_line(line)
        if result is not None:
            yield result

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse all *lines* using the template method.

        Iterates each line through ``classify_line``, then drains any pending
        accumulators via ``flush_accumulators``.
        """
        for line in lines:
            yield from self.classify_line(line)
        yield from self.flush_accumulators()
