"""Detection for deterministic harness echoes of an input prompt."""

from __future__ import annotations


def is_prompt_echo_line(line: str, input_prompt: str | None) -> bool:
    """Return whether a nonblank line repeats the complete input prompt."""
    stripped_line = line.strip()
    stripped_prompt = input_prompt.strip() if input_prompt is not None else ""
    return bool(stripped_line and stripped_prompt) and (
        stripped_line == stripped_prompt or stripped_prompt in stripped_line
    )


__all__ = ["is_prompt_echo_line"]
