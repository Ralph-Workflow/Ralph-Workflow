
from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.cells import cell_len

if TYPE_CHECKING:
    from collections.abc import Mapping

SUMMARY_THRESHOLD = 4000
_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})
_ENABLED_VALUES: frozenset[str] = frozenset({"1", "true", "yes"})

_PLACEHOLDER_HEADLINE = "(no headline available)"
AI_SUMMARY_MAX_CHARS = 400

_SENTENCE_END = re.compile(r"[.!?\n]")

AiSummaryHook = Callable[[str], "str | None"]


@dataclass
class _AiHookState:
    hook: AiSummaryHook | None = None


_AI_HOOK_STATE = _AiHookState()
_ai_hook_lock = threading.Lock()


def set_ai_summary_hook(hook: AiSummaryHook | None) -> None:
    with _ai_hook_lock:
        _AI_HOOK_STATE.hook = hook


def get_ai_summary_hook() -> AiSummaryHook | None:
    with _ai_hook_lock:
        return _AI_HOOK_STATE.hook


def should_summarize(text: str, env: Mapping[str, str]) -> bool:
    flag = env.get("RALPH_LONG_CONTENT_SUMMARY", "").lower().strip()
    if flag in _DISABLED_VALUES:
        return False
    try:
        return cell_len(text) > SUMMARY_THRESHOLD
    except Exception:
        return False


def build_content_summary(text: str, max_chars: int = 200) -> str:
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
    return build_content_summary(text, max_chars=max_chars)


def build_headline_or_placeholder(text: str, max_chars: int = 120) -> str:
    result = build_content_summary(text, max_chars=max_chars)
    return result if result else _PLACEHOLDER_HEADLINE


def build_ai_summary(text: str, env: Mapping[str, str]) -> str | None:
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
    return result[:AI_SUMMARY_MAX_CHARS] + "…" if len(result) > AI_SUMMARY_MAX_CHARS else result


__all__ = [
    "SUMMARY_THRESHOLD",
    "_PLACEHOLDER_HEADLINE",
    "AiSummaryHook",
    "build_ai_summary",
    "build_content_summary",
    "build_headline_or_placeholder",
    "build_headline_summary",
    "get_ai_summary_hook",
    "set_ai_summary_hook",
    "should_summarize",
]
