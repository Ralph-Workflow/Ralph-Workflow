"""Detection for deterministic harness echoes of an input prompt."""

from __future__ import annotations

import re

HARNESS_ECHO_MARKERS: tuple[str, ...] = ("<|user|>", "<|im_start|>", "[INST]")
_SHELL_PROMPT_PREFIXES: tuple[str, ...] = ("> ", "$ ", "% ", "# ")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _looks_like_chat_template_marker(line: str) -> bool:
    """Return whether a line contains deterministic chat-template framing."""
    return any(marker in line for marker in HARNESS_ECHO_MARKERS)


def _prompt_edge_sentences(prompt: str) -> tuple[str, ...]:
    """Return the first and last sentences of a multi-sentence prompt."""
    sentences = tuple(
        sentence.strip() for sentence in _SENTENCE_BOUNDARY.split(prompt) if sentence.strip()
    )
    if len(sentences) < 2:
        return ()
    return sentences[0], sentences[-1]


def is_prompt_echo_line(line: str, input_prompt: str | None) -> bool:
    """Return whether a nonblank line is deterministic harness output, not LLM work."""
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


__all__ = ["HARNESS_ECHO_MARKERS", "is_prompt_echo_line"]
