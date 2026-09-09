from __future__ import annotations

from typing import Final

_RETIRED_AGENT_KEY: Final = "can_commit"
_AGENT_TABLE_PARTS: Final = 2
_QUOTED_KEY_DELIMITERS: Final = 2


def remove_retired_agent_can_commit_assignments(source: str) -> str:
    in_agent_table = False
    multiline_quote: str | None = None
    retained_lines: list[str] = []
    for line in source.splitlines(keepends=True):
        if multiline_quote is not None:
            retained_lines.append(line)
            multiline_quote = _multiline_quote_after(line, multiline_quote)
            continue

        table_path = _table_path(line)
        if table_path is not None:
            in_agent_table = (
                len(table_path) == _AGENT_TABLE_PARTS and _unquote_key(table_path[0]) == "agents"
            )
        if not (in_agent_table and _is_retired_assignment(line)):
            retained_lines.append(line)
        multiline_quote = _multiline_quote_after(line, None)
    return "".join(retained_lines)


def _table_path(line: str) -> tuple[str, ...] | None:
    stripped = _without_comment(line).strip()
    if not stripped.startswith("[") or stripped.startswith("[[") or not stripped.endswith("]"):
        return None
    return _dotted_key_parts(stripped[1:-1])


def _without_comment(line: str) -> str:
    quote: str | None = None
    index = 0
    while index < len(line):
        character = line[index]
        if quote is not None:
            if character == "\\" and quote == '"':
                index += 2
                continue
            if character == quote:
                quote = None
        elif character in ('"', "'"):
            quote = character
        elif character == "#":
            return line[:index]
        index += 1
    return line


def _dotted_key_parts(text: str) -> tuple[str, ...] | None:
    parts: list[str] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        start = index
        if index < len(text) and text[index] in ('"', "'"):
            quote = text[index]
            index += 1
            while index < len(text):
                if text[index] == "\\" and quote == '"':
                    index += 2
                    continue
                if index < len(text) and text[index] == quote:
                    index += 1
                    break
                index += 1
        else:
            while index < len(text) and text[index] not in ". \t":
                index += 1
        if start == index:
            return None
        parts.append(text[start:index])
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            return tuple(parts)
        if text[index] != ".":
            return None
        index += 1
    return None


def _unquote_key(key: str) -> str:
    if len(key) >= _QUOTED_KEY_DELIMITERS and key[0] == key[-1] and key[0] in ('"', "'"):
        return key[1:-1]
    return key


def _is_retired_assignment(line: str) -> bool:
    index = 0
    while index < len(line) and line[index].isspace():
        index += 1
    if index == len(line) or line[index] == "#":
        return False
    key_start = index
    if line[index] in ('"', "'"):
        quote = line[index]
        index += 1
        while index < len(line) and line[index] != quote:
            if line[index] == "\\" and quote == '"':
                index += 2
                continue
            index += 1
        if index == len(line):
            return False
        index += 1
    else:
        while index < len(line) and (line[index].isalnum() or line[index] in "_-"):
            index += 1
    if _unquote_key(line[key_start:index]) != _RETIRED_AGENT_KEY:
        return False
    while index < len(line) and line[index].isspace():
        index += 1
    return index < len(line) and line[index] == "="


def _multiline_quote_after(line: str, active_quote: str | None) -> str | None:
    index = 0
    quote = active_quote
    while index < len(line):
        if quote is not None:
            delimiter = quote * 3
            end = line.find(delimiter, index)
            if end == -1:
                return quote
            index = end + 3
            quote = None
            continue
        if line[index] == "#":
            return None
        if line.startswith('"""', index):
            quote = '"'
            index += 3
            continue
        if line.startswith("'''", index):
            quote = "'"
            index += 3
            continue
        if line[index] == '"':
            index += 1
            while index < len(line):
                if line[index] == "\\":
                    index += 2
                    continue
                if index < len(line) and line[index] == '"':
                    index += 1
                    break
                index += 1
            continue
        if line[index] == "'":
            end = line.find("'", index + 1)
            if end == -1:
                return None
            index = end + 1
            continue
        index += 1
    return quote
