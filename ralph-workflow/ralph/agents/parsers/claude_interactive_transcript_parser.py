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
_PROVIDER_FAILURE_PATTERN = re.compile(
    r"^(?:"
    r"(?:api|authentication|authorization|credential|oauth|billing|quota|"
    r"rate[\s_-]*limit|network|connection|provider|request)\s+"
    r"(?:error|failure|failed|unavailable)\b"
    r"|service\s+unavailable\b"
    r"|(?:4(?:00|01|03|08|09|13|22|23|24|29)|5\d\d)\s+"
    r"(?:bad\s+request|unauthorized|forbidden|request\s+timeout|conflict|"
    r"payload\s+too\s+large|unprocessable\s+(?:content|entity)|locked|"
    r"failed\s+dependency|too\s+many\s+requests|internal\s+server\s+error|"
    r"bad\s+gateway|service\s+unavailable|gateway\s+timeout)\b"
    r")",
    re.IGNORECASE,
)
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
    r"[\s]*[✽✶✢●✳✻]"
    r"|[\s]*·\s*thinking\s*\)"
    r"|[\s]*\(\d+s\s*·"
    r"|[\s]*↓\s*\d+\.?\d*[km]?\s*tokens?"
    r"|[\s]*✻\s*↓\s*\d+\.?\d*[km]?\s*tokens?\s*·\s*thinking\s*\)"
    r"|[\s]*\d+thinking[\s)]*$"
    r"|[\s]*\d+\.?\d*[km]?\s*tokens?\s*\)?"
    r"|[\s]*\d+\s*·\s*thinking\s*\)"
    r"|[\s]*·\s*\d+s\s*·\s*(?:↓\s*\d+\.?\d*[km]?\s*tokens?\s*·\s*)?thinking\s*\)"
    r"|[\s]*\d+s\s*·\s*(?:↓\s*\d+\.?\d*[km]?\s*tokens?\s*·\s*)?thinking\s*\)"
)

# Short fragments (< 20 chars) ending in "thinking" are thinking status
# remnants from character-by-character PTY reads.  Legitimate prose containing
# "thinking" is always longer or doesn't end with "thinking".
_LENIENT_THINKING_MAX_LEN = 30
_MIN_OUTPUT_LEN = 6
_MAX_THINKING_PREFIX_ALPHA = 2
_TUI_GLYPH_CHARS: frozenset[str] = frozenset("↑↓·✽✶✢●✳✻→←↳…▌█")
_TUI_GLYPH_STRONG_LEN = 40


def _contains_thinking_keyword(text: str) -> bool:
    lower = text.lower()
    return "ought for" in lower or "inking)" in lower or bool(re.search(r"\bthinking\b", lower))


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
    # Exit banner (Claude Code >= 2.1.x prints the resume hint on two lines;
    # the id-carrying second line is matched by _SESSION_ID_PATTERNS first,
    # so this only ever drops the bare banner line).
    re.compile(r"^\s*Resume this session\b", re.IGNORECASE),
    # PTY echo of a typed slash command (e.g. the auto-exit "/exit").
    re.compile(r"^\s*/[A-Za-z][A-Za-z0-9_-]{0,30}\s*$"),
)

_BOX_DRAWING_STRUCTURAL_RATIO = 0.6
_BOX_DRAWING_FRAME_RATIO = 0.10
_ALPHANUMERIC_FRAME_MIN_RATIO = 0.25
_MIN_STRIPPED_WORDS = 3
_MIN_BOX_COUNT_FOR_STRIPPED_CHECK = 2


def _count_box_drawing(text: str) -> int:
    """Count Unicode box-drawing / block-element characters in *text*."""
    return sum(
        1
        for ch in text
        if ch in _BOX_DRAWING_CHARS
        or (unicodedata.category(ch) == "So" and "\u2500" <= ch <= "\u259f")
    )


def _is_tui_chrome(text: str) -> bool:
    """Return True when *text* is terminal-render surface noise.

    Detects box-drawing borders, splash screens, spinners, status bars, and
    other TUI artifacts that should never be classified as agent output.
    Platform-agnostic: operates on Unicode character properties, not
    terminal-specific escape sequences (those are handled by the VT normalizer).
    """
    if not text or any(pattern.search(text) for pattern in _TUI_CHROME_PATTERNS):
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
        ch
        for ch in text
        if ch not in _BOX_DRAWING_CHARS and unicodedata.category(ch) not in ("So", "Sk", "Cf", "Cc")
    )
    meaningful_words = [w for w in stripped.split() if any(c.isalnum() for c in w)]
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
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        error = value.get("message")
        if isinstance(error, str) and error.strip():
            return error.strip()
        error_type = value.get("type")
        if isinstance(error_type, str) and error_type.strip():
            return error_type.strip()
    return ""


def _tool_result_event(item: dict[str, object]) -> InteractiveTranscriptEvent | None:
    """Build a tool_result event from a content block, or None if empty.

    Honors the wire-format ``is_error`` flag (matching the headless Claude,
    Cursor, Pi, and Generic parsers) by carrying it in metadata so the
    downstream parser can surface a failed tool call as an error instead of
    a routine result.
    """
    text = _extract_message_text(item.get("content")).strip()
    if not text:
        return None
    metadata: dict[str, object] = {}
    tool_use_id = item.get("tool_use_id")
    if isinstance(tool_use_id, str) and tool_use_id:
        metadata["tool_use_id"] = tool_use_id
    if bool(item.get("is_error")):
        metadata["is_error"] = True
    return InteractiveTranscriptEvent(
        kind="tool_result",
        text=f"claude result: {text}",
        metadata=metadata,
    )


_IDLE_SINGLE_WORD_MAX_LEN = 15
_IDLE_ELLIPSIS_MAX_LEN = 40
_IDLE_TUI_GLYPH_MAX_LEN = 20


class ClaudeInteractiveTranscriptParser:
    """Extract semantic events from a normalized Claude interactive transcript.

    Architecture: the transcript file delivers structured JSON events that tell us
    which mode the session is in (thinking / output / tool_use).  Non-JSON text
    fragments that arrive between JSON events are classified by *mode*, not by
    per-line regex heuristics:

      - *thinking* mode → every non-JSON fragment is TUI status-bar noise → drop.
      - *tool_use*  mode → every non-JSON fragment is TUI rendering noise → drop.
      - *output*    mode → non-JSON fragments are genuine agent output → emit.
      - *idle*      (no mode yet) → conservative heuristics (drop short / TUI-ish).

    This eliminates the whack-a-mole regex approach where every new Claude Code
    thinking-status variant needed a dedicated pattern.
    """

    def __init__(self) -> None:
        self.session_id: str | None = None
        self._last_emitted_signature: tuple[str, str, object] | None = None
        self._buffer = ""
        self._current_content_mode: str | None = None

    def feed(self, raw_text: str) -> list[InteractiveTranscriptEvent]:
        json_events = self._events_from_json(raw_text)
        if json_events is not None:
            return json_events
        self._buffer += raw_text
        if "\n" not in self._buffer:
            return []
        normalized = normalize_vt_text(self._buffer)
        lines = normalized.split("\n")
        if not lines:
            return []
        if not normalized.endswith("\n"):
            self._buffer = lines.pop()
            if not lines:
                return []
        else:
            self._buffer = ""
        events: list[InteractiveTranscriptEvent] = []
        for line in lines:
            text = line.strip()
            if not text:
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
        # tool_use_id participates in the signature so parallel same-tool
        # calls (and identical result texts from distinct calls) all emit;
        # only a literal re-read of the same transcript line is suppressed.
        signature = (event.kind, event.text, event.metadata.get("tool_use_id"))
        if signature == self._last_emitted_signature:
            return
        events.append(event)
        self._last_emitted_signature = signature

    def _events_from_assistant_content_item(
        self, item: dict[str, object]
    ) -> list[InteractiveTranscriptEvent]:
        item_type = str(item.get("type", ""))
        result: list[InteractiveTranscriptEvent] = []
        if item_type == "tool_use":
            self._current_content_mode = "tool_use"
            raw_tool_name = item.get("name")
            tool_name = (
                raw_tool_name.strip()
                if isinstance(raw_tool_name, str) and raw_tool_name.strip()
                else "tool"
            )
            metadata: dict[str, object] = {"tool": tool_name}
            tool_use_id = item.get("id")
            if isinstance(tool_use_id, str) and tool_use_id:
                metadata["tool_use_id"] = tool_use_id
            tool_input = item.get("input")
            if isinstance(tool_input, dict):
                metadata["input"] = dict(cast("dict[str, object]", tool_input))
            result.append(
                InteractiveTranscriptEvent(
                    kind="tool_use",
                    text=f"claude tool: {tool_name}",
                    metadata=metadata,
                )
            )
        elif item_type == "thinking":
            self._current_content_mode = "thinking"
            text = str(item.get("thinking", "")).strip()
            if text and not self._is_tui_thinking_garbage(text):
                result.append(InteractiveTranscriptEvent(kind="thinking", text=text))
        elif item_type == "tool_result":
            event = _tool_result_event(item)
            if event is not None:
                result.append(event)
        elif item_type == "text":
            self._current_content_mode = "output"
            text = str(item.get("text", "")).strip()
            if text:
                event = self._event_for_text(text)
                if event is not None:
                    result.append(event)
        return result

    def _events_from_assistant_message(self, message: object) -> list[InteractiveTranscriptEvent]:
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if isinstance(content, str):
            # Defensive future-proofing: assistant text delivered as a plain
            # string instead of a content-block list is still agent output.
            text = content.strip()
            if not text:
                return []
            self._current_content_mode = "output"
            event = self._event_for_text(text)
            return [event] if event is not None else []
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
            event = _tool_result_event(item_dict)
            if event is not None:
                events.append(event)
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
        if isinstance(session_id, str) and session_id and session_id != self.session_id:
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

    def _match_known_pattern(self, text: str) -> InteractiveTranscriptEvent | None:
        """Match text against known regex/prefix patterns, returning event or None."""
        result = (
            InteractiveTranscriptEvent(kind="error", text=text)
            if _PROVIDER_FAILURE_PATTERN.match(text)
            else None
        )
        for pattern in _SESSION_ID_PATTERNS:
            if result is not None:
                break
            match = pattern.search(text)
            if match is not None:
                self.session_id = match.group(1)
                result = InteractiveTranscriptEvent(kind="session", text=text)
                break
        if result is None and _TOOL_USE_PATTERN.match(text):
            result = InteractiveTranscriptEvent(kind="tool_use", text=text)
        if result is None and text.startswith("claude result:"):
            result = InteractiveTranscriptEvent(kind="tool_result", text=text)
        if result is None and (
            text.startswith("[claude]:") or text.startswith("claude ") or text.startswith("claude/")
        ):
            result = InteractiveTranscriptEvent(kind="lifecycle", text=text)
        if result is None and _THINKING_STATUS_RE.search(text):
            result = None
        return result

    @staticmethod
    def _is_tui_thinking_garbage(text: str) -> bool:
        """Return True if *text* is TUI spinner/status garbage, not real content."""
        return bool(_THINKING_STATUS_RE.search(text)) or any(c in _TUI_GLYPH_CHARS for c in text)

    def _event_for_active_mode(self, text: str) -> InteractiveTranscriptEvent | None:
        """Classify text after a structured content block established its mode."""
        if self._current_content_mode == "thinking":
            if self._is_tui_thinking_garbage(text):
                return None
            return InteractiveTranscriptEvent(kind="thinking", text=text)
        if self._current_content_mode == "output":
            return InteractiveTranscriptEvent(kind="output", text=text)
        return None

    @staticmethod
    def _is_idle_tui_fragment(text: str) -> bool:
        """Return whether idle-mode text is terminal status chrome."""
        return (
            bool(_THINKING_STATUS_RE.search(text))
            or (len(text) < _IDLE_SINGLE_WORD_MAX_LEN and " " not in text)
            or ("…" in text and len(text) < _IDLE_ELLIPSIS_MAX_LEN)
            or (len(text) < _IDLE_TUI_GLYPH_MAX_LEN and any(c in _TUI_GLYPH_CHARS for c in text))
            or _contains_thinking_keyword(text)
        )

    def _event_for_text(self, text: str) -> InteractiveTranscriptEvent | None:
        if _PURE_COUNTER_RE.match(text) or _TUI_STATUSBAR_RE.search(text):
            return None
        known = self._match_known_pattern(text)
        if known is not None:
            return known
        if _is_tui_chrome(text):
            return None
        if self._current_content_mode is not None:
            return self._event_for_active_mode(text)
        if self._is_idle_tui_fragment(text) or len(text) <= _MIN_OUTPUT_LEN:
            return None
        return InteractiveTranscriptEvent(kind="output", text=text)
