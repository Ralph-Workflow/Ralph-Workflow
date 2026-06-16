"""VT/PTY helper functions for interactive Claude mode."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from ralph.agents.invoke._types import _ChoiceMenuOption, _ChoiceMenuState
from ralph.display.vt_normalizer import normalize_vt_text

if TYPE_CHECKING:
    import threading

_PERMISSION_PROMPT_PATTERNS = (
    re.compile(r"claude requested permissions?", re.IGNORECASE),
    re.compile(r"\bapprove\b", re.IGNORECASE),
    re.compile(r"\ballow\?", re.IGNORECASE),
    re.compile(r"enable auto mode\?", re.IGNORECASE),
    re.compile(r"enter to confirm", re.IGNORECASE),
)
_CHOICE_MENU_OPTION_RE = re.compile(
    "^(?P<prefix>[^\\d\\w\\s]\\s*)?(?P<index>\\d+)\\.\\s*(?P<label>.+)$"
)

_APPROVAL_KEYWORDS = frozenset({"allow", "approve", "yes", "grant", "authorize"})
_REJECTION_KEYWORDS = frozenset({"no", "cancel", "deny", "reject", "block", "skip", "exit"})
_MENU_SCORE_CONFIRM_FOOTER = 3
_MENU_SCORE_PERMISSION_KEYWORDS = 2
_MENU_SCORE_NUMBERED_OPTIONS = 2
_MENU_SCORE_PERMISSION_PATTERN = 2
_MENU_SCORE_THRESHOLD = 4
_MIN_LINE_LEN_FOR_NUMBERED_CHECK = 3
_MIN_PREFIX_LEN = 2
_MENU_APPROVAL_INDICATORS = frozenset(
    {
        "allow",
        "approve",
        "yes",
        "grant",
        "authorize",
        "ok",
        "okay",
        "accept",
        "confirm",
    }
)
_MENU_REJECTION_INDICATORS = frozenset(
    {
        "no",
        "cancel",
        "deny",
        "reject",
        "block",
        "skip",
        "exit",
        "quit",
        "refuse",
        "decline",
    }
)
_MENU_APPROVAL_COUNT_THRESHOLD = 2
_MIN_PREFIX_CHAR_LEN = 4
_MENU_QUIESCENCE_SECONDS = 0.75
_RECENT_CHOICE_LINES_MAX = 20


def _split_complete_vt_lines(text: str) -> tuple[list[str], str]:
    collapsed = text.replace("\r\r\n", "\n").replace("\r\n", "\n")
    lines = collapsed.split("\n")
    if not lines:
        return [], ""
    if collapsed.endswith("\n"):
        lines.pop()
        return [f"{line}\n" for line in lines], ""
    pending = lines.pop()
    return [f"{line}\n" for line in lines], pending


def _pending_vt_snapshot_line(text: str) -> str | None:
    normalized = normalize_vt_text(text).rstrip()
    if not normalized:
        return None
    return normalized


def _visible_tui_text(text: str) -> str:
    return normalize_vt_text(text).strip()


def _fuzzy_contains_permission_prompt(text: str) -> bool:
    """Fuzzy detection of interactive permission/approval prompts.

    Permission prompts ask Claude to do something (allow tool, enable mode, etc).
    Action chooser menus (Retry/Skip/Exit) ask user to pick a task action.

    We detect permission prompts by looking for permission-related keywords
    in the text, which is robust against format variations.
    """
    visible = normalize_vt_text(text)
    lower = visible.lower()
    lines = [line.strip().lower() for line in visible.splitlines() if line.strip()]

    if "bypass permissions on" in lower:
        return False

    if any(pattern.search(visible) is not None for pattern in _PERMISSION_PROMPT_PATTERNS):
        return True

    approval_count = sum(1 for line in lines if any(kw in line for kw in _MENU_APPROVAL_INDICATORS))
    rejection_count = sum(
        1 for line in lines if any(kw in line for kw in _MENU_REJECTION_INDICATORS)
    )

    if approval_count >= 1 and rejection_count >= 1:
        return True

    if approval_count >= _MENU_APPROVAL_COUNT_THRESHOLD:
        return True

    permission_phrases = [
        "claude requested",
        "permission prompt",
        "permissions to",
        "authorize",
        "tool use",
        "requires confirmation",
        "approval",
        "auto mode",
        "trust prompt",
    ]
    return any(phrase in lower for phrase in permission_phrases)


def _option_index_score(pair: tuple[int, int]) -> int:
    return pair[1]


def _simple_auto_approve(text: str) -> str | None:
    """Auto-approve any permission prompt by scoring options for safety.

    Scores each option by approval/rejection keyword weight, picks the best.
    Falls back to plain Enter when scoring is ambiguous.
    """
    if not _fuzzy_contains_permission_prompt(text):
        return None

    visible = normalize_vt_text(text)
    lines = [line.strip() for line in visible.splitlines() if line.strip()]
    if not lines:
        return "\r"

    option_indices: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        lower = line.lower()
        has_digit = (
            len(line) > _MIN_PREFIX_LEN and line[0:2].isdigit() and "." in line[:4]
        ) or any(line.lstrip().startswith(f"{d}.") for d in range(1, 10))
        if has_digit:
            approval = sum(1 for kw in _MENU_APPROVAL_INDICATORS if kw in lower)
            rejection = sum(1 for kw in _MENU_REJECTION_INDICATORS if kw in lower)
            option_indices.append((i, 2 * approval - 2 * rejection))

    if not option_indices:
        return "\r"

    best_index, best_score = max(option_indices, key=_option_index_score)
    if best_score > 0:
        option_num = lines[best_index].lstrip()[:2].rstrip(".").strip()
        if option_num.isdigit():
            return f"{option_num}\r"

    return "\r"


def _extract_choice_menu_state(text: str) -> _ChoiceMenuState | None:
    visible = normalize_vt_text(text)
    lines = [line.strip() for line in visible.splitlines() if line.strip()]
    if not lines:
        return None
    options: list[_ChoiceMenuOption] = []
    prompt: str | None = None
    confirm_footer: str | None = None
    for line in lines:
        lower = line.lower()
        is_confirm_footer = ("enter" in lower and "confirm" in lower) or "enter to confirm" in lower
        if is_confirm_footer:
            confirm_footer = line
            continue
        match = _CHOICE_MENU_OPTION_RE.match(line)
        if match is not None:
            index = int(str(match.group("index")))
            label = str(match.group("label")).strip()
            selected = match.group("prefix") is not None
            options.append(_ChoiceMenuOption(index=index, label=label, selected=selected))
            continue
        if prompt is None:
            prompt = line
    if confirm_footer is None or not options:
        return None
    if prompt is None:
        selected_option = next((opt for opt in options if opt.selected), None)
        if selected_option is not None:
            prompt = selected_option.label
        else:
            return None
    selected_index = next((i for i, option in enumerate(options) if option.selected), None)
    return _ChoiceMenuState(
        prompt=prompt,
        options=tuple(options),
        selected_index=selected_index,
        confirm_footer=confirm_footer,
    )


def _menu_navigation_response(
    state: _ChoiceMenuState,
    preferred_index: int | None,
) -> str | None:
    if preferred_index is None:
        return None
    effective_selected = state.selected_index if state.selected_index is not None else 0
    delta = preferred_index - effective_selected
    if delta > 0:
        return ("\x1b[B" * delta) + "\r"
    if delta < 0:
        return ("\x1b[A" * abs(delta)) + "\r"
    return "\r"


def _plan_choice_menu_response(text: str) -> str | None:
    state = _extract_choice_menu_state(text)
    if state is None:
        return None
    preferred_index = state.selected_index
    for i, option in enumerate(state.options):
        label = option.label.lower()
        if label.startswith("yes") and "default" not in label:
            preferred_index = i
            break
    return _menu_navigation_response(state, preferred_index)


def _approval_option_score(label: str) -> int | None:
    lowered = label.lower()
    if any(
        token in lowered for token in ("no", "cancel", "deny", "reject", "block", "exit", "skip")
    ):
        return None
    score = 0
    if any(token in lowered for token in ("allow", "approve", "grant", "authorize", "yes")):
        score += 4
    if any(token in lowered for token in ("once", "this time", "now")):
        score += 2
    if any(token in lowered for token in ("always", "default", "session", "permanent")):
        score -= 3
    return score if score > 0 else None


def _best_permission_option(state: _ChoiceMenuState) -> tuple[int, str] | None:
    preferred_index: int | None = None
    preferred_score: int | None = None
    preferred_label: str | None = None
    for i, option in enumerate(state.options):
        score = _approval_option_score(option.label)
        if score is None:
            continue
        if preferred_score is None or score > preferred_score:
            preferred_index = i
            preferred_score = score
            preferred_label = option.label
    if preferred_index is None or preferred_label is None:
        return None
    return preferred_index, preferred_label


def _plan_fuzzy_permission_menu_response(text: str) -> str | None:
    state = _extract_choice_menu_state(text)
    if state is None:
        return None
    best = _best_permission_option(state)
    if best is None:
        return None
    preferred_index, _ = best
    return _menu_navigation_response(state, preferred_index)


def _permission_prompt_action_message(
    text: str,
    *,
    auto_mode_prompt_seen: bool,
) -> str | None:
    state = _extract_choice_menu_state(
        text
        if auto_mode_prompt_seen
        else f"Enable auto mode?\n{text}"
        if _is_auto_mode_menu_snapshot(text)
        else text
    )
    if state is None:
        return None
    selected_label: str | None = None
    if auto_mode_prompt_seen or _is_auto_mode_menu_snapshot(text):
        for option in state.options:
            lowered = option.label.lower()
            if lowered.startswith("yes") and "default" not in lowered:
                selected_label = option.label
                break
    else:
        best = _best_permission_option(state)
        if best is not None:
            _, selected_label = best
    if selected_label is None:
        return None
    prompt_summary = state.prompt
    return f"Ralph auto-answered permission prompt: {prompt_summary} → {selected_label}"


def _is_auto_mode_menu_snapshot(text: str) -> bool:
    visible = normalize_vt_text(text)
    lowered = [line.strip().lower() for line in visible.splitlines() if line.strip()]
    if not any("enter to confirm" in line for line in lowered):
        return False
    return any("yes, and make it my default mode" in line for line in lowered) and any(
        "yes, enable auto mode" in line for line in lowered
    )


def _write_pty_input(writer_fd: int, text: str, *, lock: threading.Lock | None = None) -> None:
    data = text.encode("utf-8")
    if lock is None:
        os.write(writer_fd, data)
    else:
        with lock:
            os.write(writer_fd, data)


def _is_permission_prompt_line(text: str) -> bool:
    stripped = _visible_tui_text(text)
    if _extract_choice_menu_state(text) is not None:
        return True
    if _fuzzy_contains_permission_prompt(text):
        return True
    return any(pattern.search(stripped) is not None for pattern in _PERMISSION_PROMPT_PATTERNS)


def _interactive_auto_response_for_prompt(
    text: str,
    *,
    auto_mode_prompt_seen: bool,
) -> str | None:
    if auto_mode_prompt_seen or _is_auto_mode_menu_snapshot(text):
        return _plan_choice_menu_response(
            text if auto_mode_prompt_seen else f"Enable auto mode?\n{text}"
        )
    precise_response = _plan_fuzzy_permission_menu_response(text)
    if precise_response is not None:
        return precise_response
    if _fuzzy_contains_permission_prompt(text):
        return _simple_auto_approve(text)
    return None
