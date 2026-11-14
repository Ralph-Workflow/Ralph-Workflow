from __future__ import annotations

import re
from typing import cast

from ralph.agents.parsers._event_classification import is_user_prompt_event_line

HARNESS_ECHO_MARKERS: tuple[str, ...] = ("<|user|>", "<|im_start|>", "[INST]")
_SHELL_PROMPT_PREFIXES: tuple[str, ...] = ("> ", "$ ", "% ", "# ")
_SENTENCE_BOUNDARY: re.Pattern[str] = re.compile(r"(?<=[.!?])\s+")
_MIN_SENTENCE_PAIR = 2


def _looks_like_chat_template_marker(line: str) -> bool:
    return any(marker in line for marker in HARNESS_ECHO_MARKERS)


def _prompt_edge_sentences(prompt: str) -> tuple[str, ...]:
    parts = cast("list[str]", _SENTENCE_BOUNDARY.split(prompt))
    sentences = tuple(sentence.strip() for sentence in parts if sentence.strip())
    if len(sentences) < _MIN_SENTENCE_PAIR:
        return ()
    return sentences[0], sentences[-1]


def is_prompt_echo_line(line: str, input_prompt: str | None) -> bool:
    stripped_line = line.strip()
    stripped_prompt = input_prompt.strip() if input_prompt is not None else ""
    if not stripped_line:
        return False
    if _looks_like_chat_template_marker(stripped_line):
        return True
    if stripped_line.startswith(_SHELL_PROMPT_PREFIXES):
        return True
    if not stripped_prompt:
        return False
    return (
        stripped_line == stripped_prompt
        or stripped_prompt in stripped_line
        or stripped_line in _prompt_edge_sentences(stripped_prompt)
    )


__all__ = ["HARNESS_ECHO_MARKERS", "is_prompt_echo_line", "is_user_prompt_event_line"]
