from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_SUBAGENT_DESCRIPTION_MAX = 200
_RAW_DECODE_PAIR_LEN = 2


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]|\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


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


_SENSITIVE_MARKER_FALLBACK_RE = re.compile(
    r"""
    "(?i:arguments|args|file_path|input|prompt|content)"\s*:\s*"
    .*?
    (?=[,\}\]\n]|$)
    """,
    re.VERBOSE | re.DOTALL,
)


_SENSITIVE_JSON_KEYS: frozenset[str] = frozenset(
    {"arguments", "args", "file_path", "input", "prompt", "content"}
)


def _redact_json_values(obj: object) -> object:
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
    try:
        decoded: object = _JSON_DECODER.raw_decode(text, pos)
    except (json.JSONDecodeError, ValueError):
        return None, -1
    if not isinstance(decoded, tuple):
        return None, -1
    if len(decoded) != _RAW_DECODE_PAIR_LEN:
        return None, -1
    value: object = decoded[0]
    end: object = decoded[1]
    if not isinstance(end, int):
        return None, -1
    return value, end


def _redact_json_fragments(text: str) -> str:
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


StatusDiagnostic = dict[str, str | int | float | bool | list[object]]


def as_status_diagnostic(values: Mapping[str, object]) -> StatusDiagnostic:
    result: StatusDiagnostic = {}
    for key, value in values.items():
        typed = _status_diagnostic_value(value)
        if typed is not None:
            result[key] = typed
    return result


def _status_diagnostic_value(
    value: object,
) -> str | int | float | bool | list[object] | None:
    match value:
        case bool() as flag:
            return flag
        case str() as text:
            return text
        case int() as number:
            return number
        case float() as number:
            return number
        case list() as items:
            return list(items)
        case _:
            return None
