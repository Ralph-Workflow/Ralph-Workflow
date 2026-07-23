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

from ralph.display.line_sanitizer import strip_terminal_control

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
    "thinking": "thinking",
    "tool_use": "tool",
    "tool_result": "tool-result",
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
    "result": "META",
    "pr": "META",
    "failure": "META",
    "artifact": "META",
    "progress": "META",
    "run-start": "META",
    "run-end": "META",
    "waiting": "META",
    "content": "CONT",
    "thinking": "CONT",
    "tool": "CONT",
    "tool-result": "CONT",
    "error": "CONT",
    "status-content": "CONT",
    "content-start": "CONT",
    "content-continue": "CONT",
    "content-end": "CONT",
    "content-checkpoint": "CONT",
    "thinking-start": "CONT",
    "thinking-continue": "CONT",
    "thinking-end": "CONT",
    "thinking-checkpoint": "CONT",
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
    "CONT": "theme.cat.cont",
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
    "CONT": "C",
}

_STREAMING_KINDS: Final[frozenset[str]] = frozenset({"text", "thinking"})

_STREAMING_BLOCK_TAGS: Final[dict[str, tuple[str, str, str]]] = {
    "content": ("content-start", "content-continue", "content-end"),
    "thinking": ("thinking-start", "thinking-continue", "thinking-end"),
}

_EMPTY_PLAN_SIGNATURE: tuple[None, tuple[str, ...], int] = (None, (), 0)


def _sanitize(text: str) -> str:
    """Strip terminal control sequences for safe terminal / transcript output.

    Literal bracket markup (``[bold]x[/bold]``) is preserved verbatim so
    copy-pasted output remains self-describing and matches what an
    operator sees in the live display. Terminal CSI / OSC / C0
    sequences are still removed so the result is safe to print on a
    real TTY without leaking alternate-screen swaps, erase-display
    repaints, or cursor moves.

    The historical behavior of also feeding the input through Rich's
    text-from-markup helper is intentionally dropped: every meaningful
    call site (the ``ParallelDisplay`` emit paths and the
    ``render_event_line`` activity router) already prints with
    ``markup=False, highlight=False`` and never relied on Rich
    markup stripping to render. Removing the markup step keeps the
    round-trip from operator → display → transcript faithful.
    """
    return strip_terminal_control(text)
