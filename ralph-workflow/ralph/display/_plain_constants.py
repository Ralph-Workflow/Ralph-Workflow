from __future__ import annotations

from typing import Final

from ralph.display.line_sanitizer import strip_markup_safe

LEVELS: Final[dict[str, str]] = {
    "execution": "MILESTONE",
    "review": "MILESTONE",
    "fix": "MILESTONE",
    "analysis": "INFO",
    "commit": "INFO",
    "verification": "INFO",
    "terminal": "SUCCESS",
    "fanout_join": "INFO",
}

TAGS: Final[tuple[str, ...]] = (
    "phase",
    "phase-close",
    "plan",
    "plan-scope",
    "plan-steps",
    "activity",
    "analysis",
    "worker",
    "result",
    "pr",
    "failure",
    "artifact",
    "content",
    "thinking",
    "tool",
    "error",
    "progress",
    "run-start",
    "run-end",
    "waiting",
    "status-content",
)

_KIND_TO_TAG: Final[dict[str, str]] = {
    "text": "content",
    "thinking": "think",
    "tool_use": "call",
    "tool_result": "result",
    "error": "error",
    "progress": "progress",
    "subagent_progress": "progress",
    "status": "status-content",
    "lifecycle": "status-content",
    "raw": "content",
}

_KIND_TO_LEVEL: Final[dict[str, str]] = {
    "error": "ERROR",
    "tool_result": "SUCCESS",
    "progress": "INFO",
    "subagent_progress": "INFO",
    "thinking": "INFO",
    "tool_use": "INFO",
    "lifecycle": "MILESTONE",
    "status": "INFO",
}

TAG_CATEGORY: Final[dict[str, str]] = {
    "phase": "META",
    "phase-close": "META",
    "plan": "META",
    "plan-scope": "META",
    "plan-steps": "META",
    "activity": "META",
    "worker": "META",
    "analysis": "META",
    "pr": "META",
    "failure": "META",
    "artifact": "META",
    "progress": "META",
    "run-start": "META",
    "run-end": "META",
    "waiting": "META",
    "content": "OUT",
    "think": "OUT",
    "call": "OUT",
    "result": "OUT",
    "error": "OUT",
    "status-content": "OUT",
}

_LEVEL_THEME_KEYS: Final[dict[str, str]] = {
    "INFO": "theme.level.info",
    "SUCCESS": "theme.level.success",
    "WARN": "theme.level.warn",
    "ERROR": "theme.level.error",
    "MILESTONE": "theme.level.milestone",
}

_CAT_THEME_KEYS: Final[dict[str, str]] = {
    "META": "theme.cat.meta",
    "OUT": "theme.cat.out",
}

_COMPACT_LEVEL_BADGES: Final[dict[str, str]] = {
    "INFO": "I",
    "SUCCESS": "S",
    "WARN": "W",
    "ERROR": "E",
    "MILESTONE": "M",
}

_COMPACT_CAT_BADGES: Final[dict[str, str]] = {
    "META": "M",
    "OUT": "O",
}

_STREAMING_KINDS: Final[frozenset[str]] = frozenset({"text", "thinking"})

_STREAMING_BLOCK_TAGS: Final[dict[str, tuple[str, str]]] = {
    "content": ("content-start", "content-continue"),
    "think": ("thinking-start", "thinking-continue"),
}

_EMPTY_PLAN_SIGNATURE: tuple[None, tuple[str, ...], int] = (None, (), 0)


def _sanitize(text: str) -> str:
    return strip_markup_safe(text)
