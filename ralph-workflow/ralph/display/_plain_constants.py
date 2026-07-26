"""Constants, tag maps, and markup utilities for plain log rendering.

Internal leaf module (wt-007-consolidate-display). Re-exports the
constants and helpers previously defined in
``ralph.display.plain_renderer._constants``. ParallelDisplay imports
the names from here so it can construct log lines without taking a
dependency on a renderer module.

Note (PA-005): ``_KV_PATTERN`` is NOT re-exported from this module.
``_KV_PATTERN`` lives in ``ralph.display.completion_summary`` (where
the completion-summary code is the only consumer) and is intentionally
kept out of scope.
"""

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
    "tool-result",
    "error",
    "progress",
    "run-start",
    "run-end",
    "waiting",
    "status-content",
    "content-start",
    "content-continue",
    "content-end",
    "content-checkpoint",
    "thinking-start",
    "thinking-continue",
    "thinking-end",
    "thinking-checkpoint",
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
    # wt-028-display S-3 (DA-001): the public base tag for tool_result
    # is ``result`` (not the parser-kind identifier ``tool_result``);
    # the result tag is the human-facing category for tool outcomes and
    # inherits the OUT (content) category the retired ``tool-result``
    # tag carried -- the category badge on a successful tool result
    # still reads OUT, not META.
    "content": "OUT",
    "think": "OUT",
    "call": "OUT",
    "result": "OUT",
    "error": "OUT",
    "status-content": "OUT",
    "content-start": "OUT",
    "content-continue": "OUT",
    "content-end": "OUT",
    "content-checkpoint": "OUT",
    "thinking-start": "OUT",
    "thinking-continue": "OUT",
    "thinking-end": "OUT",
    "thinking-checkpoint": "OUT",
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

_STREAMING_BLOCK_TAGS: Final[dict[str, tuple[str, str, str]]] = {
    "content": ("content-start", "content-continue", "content-end"),
    # wt-028-display S-3 (DA-001): the public base tag is ``think`` (not
    # the parser-kind identifier ``thinking``); the streaming plumbing
    # must follow the same key so ``_route_streaming`` and
    # ``_close_block`` look up the (start, continue, end) triple
    # against the live base_tag, not the retired internal kind name.
    "think": ("thinking-start", "thinking-continue", "thinking-end"),
}

_EMPTY_PLAN_SIGNATURE: tuple[None, tuple[str, ...], int] = (None, (), 0)


def _sanitize(text: str) -> str:
    """Strip terminal controls while preserving literal bracketed agent output.

    Literal bracket markup stays copy-pasteable; Rich rendering is disabled at
    every transcript sink. Terminal CSI / OSC / C0 sequences are always removed,
    preventing terminal control in scrollback.

    Delegates to :func:`strip_markup_safe` -- the single choke point that owns
    the markup-parse guard, so malformed agent markup cannot raise here.
    """
    return strip_markup_safe(text)
