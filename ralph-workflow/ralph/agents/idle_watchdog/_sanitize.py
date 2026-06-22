"""Sanitization helpers for idle-watchdog operator-visible subagent text."""

from __future__ import annotations

import json
import re
from typing import cast

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
