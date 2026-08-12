"""Tests for transport-neutral prompt echo detection."""

from __future__ import annotations

from ralph.agents.execution_state import is_prompt_echo_line


def test_prompt_echo_detector_matches_only_complete_prompt_echoes() -> None:
    prompt = "plan the implementation step by step"

    assert is_prompt_echo_line(prompt, prompt) is True
    assert is_prompt_echo_line(f"Input: {prompt}", prompt) is True
    assert is_prompt_echo_line(f"  {prompt}  ", f"  {prompt}  ") is True
    assert is_prompt_echo_line("thinking: planning next step", prompt) is False
    assert is_prompt_echo_line("plan", prompt) is False
    assert is_prompt_echo_line("", prompt) is False
    assert is_prompt_echo_line(prompt, "") is False
