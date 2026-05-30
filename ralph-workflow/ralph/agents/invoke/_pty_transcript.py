"""Transcript parsing helpers for PTY-based agent sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable


def find_claude_transcript_path(session_id: str) -> Path | None:
    projects_root = Path.home() / ".claude" / "projects"
    if not projects_root.exists():
        return None
    target_name = f"{session_id}.jsonl"
    for candidate_root in projects_root.iterdir():
        candidate = candidate_root / target_name
        if candidate.is_file():
            return candidate
    return None


def _extract_message_text(value: object) -> str:
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


def _transcript_lines_from_assistant_content(content: list[object]) -> list[str]:
    lines: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if item_type == "tool_use":
            lines.append(f"claude tool: {item.get('name', 'tool')!s}\n")
        elif item_type == "tool_result":
            result_content = _extract_message_text(item.get("content"))
            if result_content:
                lines.append(f"claude tool result: {result_content}\n")
        elif item_type == "text":
            text = str(item.get("text", "")).strip()
            if text:
                lines.append(f"{text}\n")
    return lines


def _transcript_lines_from_user_content(content: list[object]) -> list[str]:
    lines: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_dict = cast("dict[str, object]", item)
        if item_dict.get("type") != "tool_result":
            continue
        result_content = _extract_message_text(item_dict.get("content"))
        if result_content:
            lines.append(f"claude tool result: {result_content}\n")
    return lines


def _transcript_lines_from_message(
    message: object, extractor: Callable[[list[object]], list[str]]
) -> list[str]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return extractor(content)


def transcript_lines_from_event(raw_line: str) -> list[str]:
    try:
        parsed = cast("object", json.loads(raw_line))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    obj = cast("dict[str, object]", parsed)
    event_type = str(obj.get("type", ""))
    if event_type in {"permission-mode", ""}:
        return []
    if event_type == "assistant":
        return _transcript_lines_from_message(
            obj.get("message"), _transcript_lines_from_assistant_content
        )
    if event_type == "user":
        return _transcript_lines_from_message(
            obj.get("message"), _transcript_lines_from_user_content
        )
    return []
