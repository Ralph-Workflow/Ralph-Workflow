"""Detection for deterministic harness echoes of an input prompt."""

from __future__ import annotations

HARNESS_ECHO_MARKERS: tuple[str, ...] = ("<|user|>", "<|im_start|>", "[INST]")
_SHELL_PROMPT_PREFIXES: tuple[str, ...] = ("> ", "$ ", "% ", "# ")
_SENTENCE_TERMINATORS: tuple[str, ...] = (". ", "! ", "? ")
_MINIMUM_MULTI_SENTENCE_COUNT = 2


def _looks_like_chat_template_marker(line: str) -> bool:
    """Return whether a line contains deterministic chat-template framing."""
    return any(marker in line for marker in HARNESS_ECHO_MARKERS)


def _prompt_edge_sentences(prompt: str) -> tuple[str, ...]:
    """Return the first and last sentences of a multi-sentence prompt."""
    sentences: list[str] = []
    remaining = prompt.strip()
    while remaining:
        boundaries = [
            position
            for terminator in _SENTENCE_TERMINATORS
            if (position := remaining.find(terminator)) >= 0
        ]
        if not boundaries:
            sentences.append(remaining)
            break
        boundary = min(boundaries) + 1
        sentences.append(remaining[:boundary].strip())
        remaining = remaining[boundary + 1 :].lstrip()
    if len(sentences) < _MINIMUM_MULTI_SENTENCE_COUNT:
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
