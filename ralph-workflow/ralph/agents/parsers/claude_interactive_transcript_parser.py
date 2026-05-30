"""Semantic parser for VT-normalized Claude interactive transcripts."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import cast

from ralph.display.vt_normalizer import normalize_vt_text

from .interactive_transcript_event import InteractiveTranscriptEvent

_SESSION_ID_PATTERNS = (
    re.compile(r"session\s+id\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
    re.compile(r"--resume\s+([A-Za-z0-9._:-]+)"),
)
_TOOL_USE_PATTERN = re.compile(r"^claude tool:\s*\S", re.IGNORECASE)
_MIN_MEANINGFUL_LEN = 3
_PURE_COUNTER_RE = re.compile(r"^\s*\d+\s*$")
_TUI_STATUSBAR_RE = re.compile(
    r"bypass permissions (on|off).*shift\+tab|"
    r"← for agent|"
    r"↑ \d+\.?\d*k tokens|"
    r"Ctrl\+[CD]|"
    r"esc to interrupt|"
    r"thought for \d+s",
    re.IGNORECASE,
)
_THINKING_STATUS_RE = re.compile(
    r"^[\s]*[✶✢●✳]"
    r"|^[\s]*·\s*thinking\s*\)"
    r"|^[\s]*\(\d+s\s*·"
    r"|^[\s]*↓\s*\d+\.?\d*[km]?\s*tokens?"
    r"|^[\s]*\d+thinking\s*·"
    r"|^[\s]*·\s*\d+s\s*·\s*thinking\s*\)"
)

# Short fragments (< 20 chars) ending in "thinking" are thinking status
# remnants from character-by-character PTY reads.  Legitimate prose containing
# "thinking" is always longer or doesn't end with "thinking".
_LENIENT_THINKING_MAX_LEN = 20
_MIN_OUTPUT_LEN = 3

# Box-drawing (U+2500-U+257F) + block elements (U+2580-U+259F) + extras.
_BOX_DRAWING_CHARS: frozenset[str] = frozenset(
    "\u2500\u2502\u250c\u2510\u2514\u2518\u251c\u2524\u252c\u2534\u253c"
    "\u2550\u2551\u2554\u2557\u255a\u255d\u2560\u2563\u2566\u2569\u256c"
    "\u256d\u256e\u256f\u2570\u2571\u2572\u2573"
    "\u2580\u2584\u2588\u258c\u2590\u2591\u2592\u2593"
    "\u2594\u2595\u2596\u2597\u2598\u2599\u259a\u259b\u259c\u259d\u259e\u259f"
    "\u2574\u2575\u2576\u2577\u2578\u2579\u257a\u257b\u257c\u257d\u257e\u257f"
)

_TUI_CHROME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[╭╰][─━═]+ClaudeCode", re.UNICODE),
    re.compile(
        r"[✽✻⚙]\s*(\w*[Ss]pinn?ing|[Ss]ping|Actioning|Tinkering|Clauding|Hullaballooing|Quaing|\w*thinking)",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*\d+\.?\d*[km]?\s*tokens?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\.?\d*[km]\s*$", re.IGNORECASE),
    re.compile(r"^\d+\s*plugins?\s*failed\s*to\s*install", re.IGNORECASE),
    re.compile(r"^\s*(Haiku|Sonnet|Opus)\s*[\d.]+\s*·\s*Claude\s*(Max|Pro)\s*·", re.IGNORECASE),
    re.compile(r"^\s*⏵⏵"),
    re.compile(r"^\s*(shift\+tab|ctrl\+[cd]|esc)\s+to\s+(cycle|interrupt|cancel)", re.IGNORECASE),
    re.compile(r"^\s*[⬆↑]\s*[/\w-]+\s*│", re.UNICODE),
)

_BOX_DRAWING_STRUCTURAL_RATIO = 0.6
_BOX_DRAWING_FRAME_RATIO = 0.10
_ALPHANUMERIC_FRAME_MIN_RATIO = 0.25
_MIN_STRIPPED_WORDS = 3
_MIN_BOX_COUNT_FOR_STRIPPED_CHECK = 2


def _count_box_drawing(text: str) -> int:
    """Count Unicode box-drawing / block-element characters in *text*."""
    return sum(1 for ch in text if ch in _BOX_DRAWING_CHARS
               or (unicodedata.category(ch) == "So"
                   and "\u2500" <= ch <= "\u259f"))


def _is_tui_chrome(text: str) -> bool:  # noqa: PLR0911
    """Return True when *text* is terminal-render surface noise.

    Detects box-drawing borders, splash screens, spinners, status bars, and
    other TUI artifacts that should never be classified as agent output.
    Platform-agnostic: operates on Unicode character properties, not
    terminal-specific escape sequences (those are handled by the VT normalizer).
    """
    if not text:
        return True

    for pattern in _TUI_CHROME_PATTERNS:
        if pattern.search(text):
            return True

    if not any("\u2500" <= ch <= "\u259f" for ch in text):
        return False

    total_chars = len(text)
    box_count = _count_box_drawing(text)
    if box_count == 0:
        return False

    box_ratio = box_count / total_chars
    if box_ratio >= _BOX_DRAWING_STRUCTURAL_RATIO:
        return True

    alpha_count = sum(1 for ch in text if ch.isalnum())
    alpha_ratio = alpha_count / total_chars if total_chars else 0
    if box_ratio >= _BOX_DRAWING_FRAME_RATIO and alpha_ratio < _ALPHANUMERIC_FRAME_MIN_RATIO:
        return True

    stripped = "".join(
        ch for ch in text
        if ch not in _BOX_DRAWING_CHARS
        and unicodedata.category(ch) not in ("So", "Sk", "Cf", "Cc")
    )
    meaningful_words = [
        w for w in stripped.split()
        if any(c.isalnum() for c in w)
    ]
    return (
        len(meaningful_words) < _MIN_STRIPPED_WORDS
        and box_count >= _MIN_BOX_COUNT_FOR_STRIPPED_CHECK
    )


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


def _extract_error_text(value: object) -> str:
    if isinstance(value, dict):
        error = value.get("message")
        if isinstance(error, str) and error.strip():
            return error.strip()
        error_type = value.get("type")
        if isinstance(error_type, str) and error_type.strip():
            return error_type.strip()
    return ""


class ClaudeInteractiveTranscriptParser:
    """Extract semantic events from a normalized Claude interactive transcript."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self._last_emitted_signature: tuple[str, str] | None = None

    def feed(self, raw_text: str) -> list[InteractiveTranscriptEvent]:
        json_events = self._events_from_json(raw_text)
        if json_events is not None:
            return json_events
        normalized = normalize_vt_text(raw_text)
        events: list[InteractiveTranscriptEvent] = []
        for line in normalized.splitlines():
            text = line.strip()
            if not text:
                continue
            if not any(character.isalnum() for character in text):
                continue
            stripped = text.replace(" ", "").replace(".", "")
            if len(text) <= _MIN_MEANINGFUL_LEN and stripped.isdigit():
                continue
            event = self._event_for_text(text)
            if event is not None:
                self._append_if_new(events, event)
        return events

    def _append_if_new(
        self, events: list[InteractiveTranscriptEvent], event: InteractiveTranscriptEvent
    ) -> None:
        signature = (event.kind, event.text)
        if signature == self._last_emitted_signature:
            return
        events.append(event)
        self._last_emitted_signature = signature

    def _events_from_assistant_content_item(
        self, item: dict[str, object]
    ) -> list[InteractiveTranscriptEvent]:
        item_type = str(item.get("type", ""))
        if item_type == "tool_use":
            tool_name = str(item.get("name", "tool"))
            return [InteractiveTranscriptEvent(kind="tool_use", text=f"claude tool: {tool_name}")]
        if item_type == "text":
            text = str(item.get("text", "")).strip()
            if text:
                return [InteractiveTranscriptEvent(kind="output", text=text)]
        if item_type == "thinking":
            text = str(item.get("thinking", "")).strip()
            if text:
                return [InteractiveTranscriptEvent(kind="thinking", text=text)]
        return []

    def _events_from_assistant_message(self, message: object) -> list[InteractiveTranscriptEvent]:
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if not isinstance(content, list):
            return []
        events: list[InteractiveTranscriptEvent] = []
        for item in content:
            if isinstance(item, dict):
                events.extend(self._events_from_assistant_content_item(item))
        return events

    def _events_from_user_message(self, message: object) -> list[InteractiveTranscriptEvent]:
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if not isinstance(content, list):
            return []
        events: list[InteractiveTranscriptEvent] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_dict = cast("dict[str, object]", item)
            if item_dict.get("type") != "tool_result":
                continue
            text = _extract_message_text(item_dict.get("content")).strip()
            if text:
                events.append(
                    InteractiveTranscriptEvent(kind="tool_result", text=f"claude result: {text}")
                )
        return events

    def _events_from_json(self, raw_text: str) -> list[InteractiveTranscriptEvent] | None:
        try:
            parsed = cast("object", json.loads(raw_text))
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        obj = cast("dict[str, object]", parsed)
        event_type = str(obj.get("type", ""))
        events: list[InteractiveTranscriptEvent] = []
        session_id = obj.get("sessionId") or obj.get("session_id")
        if isinstance(session_id, str) and session_id:
            self.session_id = session_id
            self._append_if_new(events, InteractiveTranscriptEvent(kind="session", text=session_id))
        if event_type == "assistant":
            for event in self._events_from_assistant_message(obj.get("message")):
                self._append_if_new(events, event)
        elif event_type == "user":
            for event in self._events_from_user_message(obj.get("message")):
                self._append_if_new(events, event)
        elif event_type == "error":
            error_text = _extract_error_text(obj.get("error"))
            if error_text:
                self._append_if_new(
                    events,
                    InteractiveTranscriptEvent(kind="error", text=error_text),
                )
        return events

    def _event_for_text(self, text: str) -> InteractiveTranscriptEvent | None:  # noqa: PLR0911
        if _PURE_COUNTER_RE.match(text):
            return None
        if _TUI_STATUSBAR_RE.search(text):
            return None
        for pattern in _SESSION_ID_PATTERNS:
            match = pattern.search(text)
            if match is not None:
                self.session_id = match.group(1)
                return InteractiveTranscriptEvent(kind="session", text=text)
        if _TOOL_USE_PATTERN.match(text):
            return InteractiveTranscriptEvent(kind="tool_use", text=text)
        if text.startswith("claude result:"):
            return InteractiveTranscriptEvent(kind="tool_result", text=text)
        if text.startswith("[claude]:") or text.startswith("claude ") or text.startswith("claude/"):
            return InteractiveTranscriptEvent(kind="lifecycle", text=text)
        if _THINKING_STATUS_RE.search(text):
            return InteractiveTranscriptEvent(kind="thinking", text=text)
        if _is_tui_chrome(text):
            return None
        cleaned = text.rstrip().rstrip(")")
        if (
            len(text) < _LENIENT_THINKING_MAX_LEN
            and cleaned.endswith("thinking")
            and " " not in cleaned
        ):
            return InteractiveTranscriptEvent(kind="thinking", text=text)
        if len(text) < _MIN_OUTPUT_LEN:
            return None
        return InteractiveTranscriptEvent(kind="output", text=text)
