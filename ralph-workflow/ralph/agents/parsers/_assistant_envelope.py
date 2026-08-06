"""Shared assistant-envelope classifier for Claude (RC2 of wt-04-claude-parsing).

Claude Code emits bookkeeping turns as real ``type: "assistant"`` records
with two distinct envelopes that MUST be classified at the envelope
level (not the text level):

  - **API error envelope**: the wire carries ``isApiErrorMessage: True``
    in EITHER the top-level record (current Claude Code 2.1.x placement,
    verified against the captured session) OR nested under
    ``message.isApiErrorMessage`` (older wire variants, accepted for
    forward compatibility). The envelope routes the record to the
    error path; no ``text`` / ``output`` event is emitted; the idle
    baseline is NOT reset (this is an error envelope, not work); the
    record is NOT forwarded to the subagent sink so R2/R3 attribution
    does not see it.

  - **Synthetic turn envelope**: the wire carries
    ``message.model == "<synthetic>"`` (the canonical marker, verified
    against the captured session) OR the weaker heuristic
    ``message.usage.output_tokens == 0 AND message.stop_reason ==
    "stop_sequence"``. The envelope routes the record to the
    lifecycle path; the existing ``is_lifecycle_kind`` predicate in
    ``claude_interactive.parse()`` drops the lifecycle event from the
    agent-text stream; the watchdog's ``_record_event`` path cannot
    reach it; the retry-context excerpt builder cannot reach it
    (text is not in any sink); the idle baseline is NOT reset.

  - **Normal envelope**: neither 1 nor 2 holds. The parser dispatches
    the existing per-item content extraction.

The shared classifier is the ONLY place this rule lives. Both
``claude_interactive_transcript_parser.py`` and ``claude.py`` (headless
NDJSON) call into it; a future wire change touches one file, not two.

Why the envelope, not the text:

  - The literal ``"No response requested."`` is the bookkeeping text
    Claude Code 2.1.223 currently puts inside a synthetic envelope, but
    it is NOT the marker. Future Claude Code builds may carry a
    different bookkeeping payload in the same envelope; filtering the
    literal would miss the next build's text. Keying off the envelope
    (model name + usage + stop_reason) is future-proof.
  - The classifier's R4 contract is guaranteed by the
    ``ENVELOPE_API_ERROR`` / ``ENVELOPE_SYNTHETIC`` paths producing no
    ``output``/``text`` event and falling through the existing
    ``is_lifecycle_kind`` predicate respectively. Operators never see
    the synthetic or error text on the operator channel, and the
    retry-context excerpt builder never sees it either.

Pure-function property: ``classify_assistant_record`` returns the same
``AssistantEnvelopeDecision`` for the same input on every call, with
no I/O, no clock reads, and no module-level mutable state. Tests assert
this directly via deterministic fixture-driven inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Public envelope-classification constants
# ---------------------------------------------------------------------------

ENVELOPE_NORMAL: str = "normal"
ENVELOPE_SYNTHETIC: str = "synthetic"
ENVELOPE_API_ERROR: str = "api_error"


# Top-level record keys that signal the API-error envelope on the
# current Claude Code 2.1.x wire. The nested ``message.isApiErrorMessage``
# placement is the legacy wire shape; the top-level placement is the
# current one (verified against the captured fixture). Both are
# accepted so a future wire change that drops one placement does not
# silently route the record to the normal path.
_TOP_LEVEL_API_ERROR_KEYS: frozenset[str] = frozenset({"isApiErrorMessage"})


@dataclass(frozen=True)
class AssistantEnvelopeDecision:
    """The classification result for one assistant record.

    Attributes:
        envelope: One of ``ENVELOPE_NORMAL``, ``ENVELOPE_SYNTHETIC``,
            or ``ENVELOPE_API_ERROR``. The parser dispatches on this
            string rather than a bool so the test suite can assert
            the exact branch.
        error_text: For ``ENVELOPE_API_ERROR``, the text payload
            extracted from the record (the wire-shape ``error_text``
            field when present, otherwise the message content). Empty
            for non-error envelopes.
        synthetic_text: For ``ENVELOPE_SYNTHETIC``, the text payload
            from the message content (the bookkeeping line). Empty
            for non-synthetic envelopes.
    """

    envelope: str
    error_text: str = ""
    synthetic_text: str = ""


def _extract_message_text(value: object) -> str:
    """Best-effort text extraction from a message ``content`` field.

    Mirrors ``claude_interactive_transcript_parser._extract_message_text``
    so the classifier is consistent with the parser's text shape.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _extract_top_level_error_text(obj: dict[str, object]) -> str:
    """Pull the API-error text from a top-level record when present."""
    error = obj.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        err_type = error.get("type")
        if isinstance(err_type, str) and err_type.strip():
            return err_type.strip()
    return ""


def _is_top_level_api_error(obj: dict[str, object]) -> bool:
    """Detect the API-error envelope at the TOP level.

    The current Claude Code 2.1.x wire carries ``isApiErrorMessage:
    True`` directly on the assistant record (not nested under
    ``message``). Verified against the captured fixture
    ``tests/agents/invoke/fixtures/claude_interactive_real_capture/...``.
    """
    return any(bool(obj.get(key)) for key in _TOP_LEVEL_API_ERROR_KEYS)


def _is_message_api_error(message: dict[str, object]) -> bool:
    """Detect the API-error envelope nested under ``message`` (legacy wire)."""
    return bool(message.get("isApiErrorMessage"))


def _is_synthetic_message(message: dict[str, object]) -> bool:
    """Detect the synthetic-turn envelope.

    Two acceptance criteria:

      - ``message.model == "<synthetic>"`` (canonical marker, verified
        verbatim against the captured fixture).
      - Weaker heuristic: ``message.usage.output_tokens == 0 AND
        message.stop_reason == "stop_sequence"`` -- catches the same
        bookkeeping envelope on a future build that drops the
        ``<synthetic>`` model name while keeping the zero-usage /
        stop_sequence stop pattern.
    """
    model = message.get("model")
    if isinstance(model, str) and model == "<synthetic>":
        return True
    usage = message.get("usage")
    stop_reason = message.get("stop_reason")
    if not isinstance(usage, dict) or not isinstance(stop_reason, str):
        return False
    output_tokens: object = usage.get("output_tokens")
    return output_tokens == 0 and stop_reason == "stop_sequence"


def classify_assistant_record(obj: object) -> AssistantEnvelopeDecision:
    """Classify one assistant record into ``NORMAL`` / ``SYNTHETIC`` / ``API_ERROR``.

    Pure function: no I/O, no clock reads, no module-level mutable
    state. Returns the same ``AssistantEnvelopeDecision`` for the same
    input on every call. The classifier handles BOTH the top-level
    (current Claude Code 2.1.x) and nested (legacy wire) placements
    of the ``isApiErrorMessage`` flag so a future wire change that
    drops one placement does not silently route the record to the
    normal path.

    Priority order:

      1. ``isApiErrorMessage`` (top-level OR nested) -> ``ENVELOPE_API_ERROR``
         with the error text carried as ``error_text``.
      2. ``message.model == "<synthetic>"`` OR zero-usage / stop_sequence
         heuristic -> ``ENVELOPE_SYNTHETIC`` with the bookkeeping text
         carried as ``synthetic_text``.
      3. else -> ``ENVELOPE_NORMAL``.

    The two non-normal paths are mutually exclusive because the
    captured fixture shows the synthetic envelope has
    ``isApiErrorMessage: False`` (the synthetic is not an API error);
    but for forward compatibility, when BOTH match, the API-error
    branch wins (an error envelope takes precedence over a synthetic
    marker so a build that emits ``isApiErrorMessage: True`` alongside
    ``model: <synthetic>`` still routes to the error path).
    """
    if not isinstance(obj, dict):
        return AssistantEnvelopeDecision(envelope=ENVELOPE_NORMAL)

    message = obj.get("message")
    message_dict: dict[str, object] = message if isinstance(message, dict) else {}

    if _is_top_level_api_error(obj) or _is_message_api_error(message_dict):
        error_text = _extract_top_level_error_text(obj)
        if not error_text:
            error_text = _extract_message_text(message_dict.get("content"))
        return AssistantEnvelopeDecision(
            envelope=ENVELOPE_API_ERROR,
            error_text=error_text,
        )

    if _is_synthetic_message(message_dict):
        synthetic_text = _extract_message_text(message_dict.get("content"))
        return AssistantEnvelopeDecision(
            envelope=ENVELOPE_SYNTHETIC,
            synthetic_text=synthetic_text,
        )

    return AssistantEnvelopeDecision(envelope=ENVELOPE_NORMAL)


__all__ = [
    "ENVELOPE_API_ERROR",
    "ENVELOPE_NORMAL",
    "ENVELOPE_SYNTHETIC",
    "AssistantEnvelopeDecision",
    "classify_assistant_record",
]
