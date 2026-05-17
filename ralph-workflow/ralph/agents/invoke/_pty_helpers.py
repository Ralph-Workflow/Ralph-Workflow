"""VT/PTY helper functions for interactive Claude mode."""

from __future__ import annotations

import re
from typing import IO, TYPE_CHECKING

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
_CHOICE_MENU_OPTION_RE = re.compile("^(?P<prefix>\u276f\\s*)?(?P<index>\\d+)\\.\\s+(?P<label>.+)$")
_MENU_QUIESCENCE_SECONDS = 0.75


def _split_complete_vt_lines(text: str) -> tuple[list[str], str]:
    lines = text.splitlines(keepends=True)
    pending = lines.pop() if lines and not lines[-1].endswith(("\n", "\r")) else ""
    return lines, pending


def _pending_vt_snapshot_line(text: str) -> str | None:
    normalized = normalize_vt_text(text).strip()
    if not normalized:
        return None
    return f"{normalized}\n"


def _visible_tui_text(text: str) -> str:
    return normalize_vt_text(text).strip()


def _extract_choice_menu_state(text: str) -> _ChoiceMenuState | None:
    visible = normalize_vt_text(text)
    lines = [line.strip() for line in visible.splitlines() if line.strip()]
    if not lines:
        return None
    options: list[_ChoiceMenuOption] = []
    prompt: str | None = None
    confirm_footer: str | None = None
    for line in lines:
        if "enter to confirm" in line.lower():
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
    if prompt is None or confirm_footer is None or not options:
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
    if preferred_index is None or state.selected_index is None:
        return None
    delta = preferred_index - state.selected_index
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


def _write_pty_input(writer: IO[bytes], text: str, *, lock: threading.Lock | None = None) -> None:
    if lock is None:
        writer.write(text.encode("utf-8"))
        writer.flush()
        return
    with lock:
        writer.write(text.encode("utf-8"))
        writer.flush()


def _is_permission_prompt_line(text: str) -> bool:
    stripped = _visible_tui_text(text)
    if _extract_choice_menu_state(text) is not None:
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
    return _plan_fuzzy_permission_menu_response(text)
