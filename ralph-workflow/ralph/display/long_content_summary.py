"""Default-on summary layer for oversized agent content blocks.

When content exceeds 4000 display cells a deterministic headline summary is
extracted from the already-AI-produced text. This is additive: head+tail
condensation remains the trusted default; the summary is an extra layer for
quick context.

Set RALPH_LONG_CONTENT_SUMMARY=0 (or 'false'/'no'/'off') to disable.
When unset or empty the summary is enabled for content above the threshold.
The summary is derived from the first sentence of the content (markdown
headings stripped), capped at 120 characters. No external AI call is made.

Note: the sentence splitter is intentionally simple — it may truncate on
abbreviations ('e.g.') or URLs. The summary is additive and labelled
'↳ summary:' so a wrong headline never obscures the raw content.

Optional AI-generated summary layer (default-OFF):
Set RALPH_LONG_CONTENT_AI_SUMMARY=1 AND register a hook via
set_ai_summary_hook() to enable. The hook receives the raw text and must
return a string or None. Exceptions are swallowed. Output is capped at 400
characters. Labelled '↳ ai-summary:' to distinguish from the deterministic
headline.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.cells import cell_len

if TYPE_CHECKING:
    from collections.abc import Mapping

_SUMMARY_THRESHOLD = 4000
_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})
_ENABLED_VALUES: frozenset[str] = frozenset({"1", "true", "yes"})

_PLACEHOLDER_HEADLINE = "(no headline available)"
_AI_SUMMARY_MAX_CHARS = 400

_SENTENCE_END = re.compile(r"[.!?\n]")

AiSummaryHook = Callable[[str], "str | None"]

@dataclass
class _AiHookState:
    hook: AiSummaryHook | None = None


_AI_HOOK_STATE = _AiHookState()
_ai_hook_lock = threading.Lock()


def set_ai_summary_hook(hook: AiSummaryHook | None) -> None:
    """Register (or clear) the AI summary hook. Thread-safe."""
    with _ai_hook_lock:
        _AI_HOOK_STATE.hook = hook


def get_ai_summary_hook() -> AiSummaryHook | None:
    """Return the current AI summary hook. Thread-safe atomic read."""
    with _ai_hook_lock:
        return _AI_HOOK_STATE.hook


def should_summarize(text: str, env: Mapping[str, str]) -> bool:
    """Return True when text exceeds the threshold and the kill-switch is not set."""
    flag = env.get("RALPH_LONG_CONTENT_SUMMARY", "").lower().strip()
    if flag in _DISABLED_VALUES:
        return False
    try:
        return cell_len(text) > _SUMMARY_THRESHOLD
    except Exception:
        return False


def build_content_summary(text: str, max_chars: int = 200) -> str:
    """Extract the first sentence, strip markdown prefixes, truncate to max_chars.

    Falls back to the first non-empty line when no sentence terminator is found.
    Returns '' when no non-empty line exists.
    """
    for line in text.splitlines():
        stripped = line.lstrip("#> ").strip()
        if not stripped:
            continue
        m = _SENTENCE_END.search(stripped)
        if m:
            candidate = stripped[: m.end()].strip()
            if candidate:
                if len(candidate) <= max_chars:
                    return candidate
                return candidate[:max_chars] + "…"
        if len(stripped) <= max_chars:
            return stripped
        return stripped[:max_chars] + "…"
    return ""


def build_headline_summary(text: str, max_chars: int = 120) -> str:
    """Extract a short headline from text, capped at max_chars."""
    return build_content_summary(text, max_chars=max_chars)


def build_headline_or_placeholder(text: str, max_chars: int = 120) -> str:
    """Extract headline; return placeholder when no headline can be extracted."""
    result = build_content_summary(text, max_chars=max_chars)
    return result if result else _PLACEHOLDER_HEADLINE


def build_ai_summary(text: str, env: Mapping[str, str]) -> str | None:
    """Return an AI-generated summary string, or None when disabled/unavailable.

    Requires RALPH_LONG_CONTENT_AI_SUMMARY=1 in env AND a registered hook AND
    text above the threshold. Hook exceptions are swallowed. Output is capped
    at 400 chars with an ellipsis suffix.
    """
    flag = env.get("RALPH_LONG_CONTENT_AI_SUMMARY", "").lower().strip()
    if flag not in _ENABLED_VALUES:
        return None
    hook = get_ai_summary_hook()
    if hook is None:
        return None
    if not should_summarize(text, env):
        return None
    try:
        result = hook(text)
    except Exception:
        return None
    if result is None:
        return None
    return result[:_AI_SUMMARY_MAX_CHARS] + "…" if len(result) > _AI_SUMMARY_MAX_CHARS else result


__all__ = [
    "_PLACEHOLDER_HEADLINE",
    "_SUMMARY_THRESHOLD",
    "AiSummaryHook",
    "build_ai_summary",
    "build_content_summary",
    "build_headline_or_placeholder",
    "build_headline_summary",
    "get_ai_summary_hook",
    "set_ai_summary_hook",
    "should_summarize",
]
