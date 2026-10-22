"""Decode an exec argv that a caller serialised into a string.

Some MCP clients and models send an argv array as its text form
(``'["cat", "a.txt"]'`` or ``"['cat', 'a.txt']"``). Read as shell words that
becomes the command ``[cat,`` and a spawn failure, so the string is turned
back into the list it was before the exec parameters are validated.
"""

from __future__ import annotations

import ast
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_ARGV_KEYS = ("command", "argv")


def _json_value(value: str) -> object:
    """Parse JSON at one typed boundary."""
    parsed: object = json.loads(value)
    return parsed


def _python_literal(value: str) -> object:
    """Parse a Python-literal list (single-quoted argv) at one typed boundary."""
    parsed: object = ast.literal_eval(value)
    return parsed


def _stringified_argv(value: str) -> list[str] | None:
    """Return the argv serialised in ``value``, else ``None``.

    Only a bracket glued to a quote is accepted: ``[`` followed by whitespace
    is the shell ``test`` builtin and must stay a command.
    """
    stripped = value.strip()
    if not (stripped.startswith(('["', "['")) and stripped.endswith("]")):
        return None
    for decode in (_json_value, _python_literal):
        try:
            decoded = decode(stripped)
        except (ValueError, SyntaxError, RecursionError, MemoryError):
            continue
        if not isinstance(decoded, list) or not decoded:
            continue
        tokens = [token for token in decoded if isinstance(token, str)]
        if len(tokens) == len(decoded):
            return tokens
    return None


def decode_stringified_argv(params: Mapping[str, object]) -> Mapping[str, object]:
    """Return ``params`` with a stringified ``command`` / ``argv`` turned back into a list.

    Decoding happens before the NUL guard, which therefore also sees a NUL
    that a ``\\u0000`` escape only produces after decoding.
    """
    decoded: dict[str, object] | None = None
    for key in _ARGV_KEYS:
        value = params.get(key)
        tokens = _stringified_argv(value) if isinstance(value, str) else None
        if tokens is not None:
            decoded = dict(params) if decoded is None else decoded
            decoded[key] = tokens
    return params if decoded is None else decoded
