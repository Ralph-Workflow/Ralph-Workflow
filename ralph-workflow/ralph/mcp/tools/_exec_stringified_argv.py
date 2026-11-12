from __future__ import annotations

import ast
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_ARGV_KEYS = ("command", "argv")


def _json_value(value: str) -> object:
    parsed: object = json.loads(value)
    return parsed


def _python_literal(value: str) -> object:
    parsed: object = ast.literal_eval(value)
    return parsed


def _stringified_argv(value: str) -> list[str] | None:
    stripped = value.strip()
    if not (stripped.startswith(('["', "['")) and stripped.endswith("]")):
        return None
    for decode in (_json_value, _python_literal):
        try:
            decoded = decode(stripped)
        except (ValueError, SyntaxError, TypeError, RecursionError, MemoryError):
            continue
        if not isinstance(decoded, list) or not decoded:
            continue
        tokens = [token for token in decoded if isinstance(token, str)]
        if len(tokens) == len(decoded):
            return tokens
    return None


def decode_stringified_argv(params: Mapping[str, object]) -> Mapping[str, object]:
    decoded: dict[str, object] | None = None
    for key in _ARGV_KEYS:
        value = params.get(key)
        tokens = _stringified_argv(value) if isinstance(value, str) else None
        if tokens is not None:
            decoded = dict(params) if decoded is None else decoded
            decoded[key] = tokens
    return params if decoded is None else decoded
