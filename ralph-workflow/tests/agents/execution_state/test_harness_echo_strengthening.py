"""Regression coverage for strengthened deterministic harness echo detection (S-4)."""

from __future__ import annotations

from ralph.agents.execution_state import is_prompt_echo_line
from ralph.agents.execution_state._harness_echo import is_prompt_echo_line as implementation


def test_harness_echo_detector_rejects_harness_framing_and_prompt_fragments() -> None:
    """S-4: deterministic CLI framing and opening/closing prompt echoes are not LLM output."""
    prompt = "First sentence here. Second sentence there."

    assert is_prompt_echo_line("<|user|> hello", prompt) is True
    assert is_prompt_echo_line("<|im_start|>user", prompt) is True
    assert is_prompt_echo_line("[INST] hello", prompt) is True
    assert is_prompt_echo_line("> First sentence here.", prompt) is True
    assert is_prompt_echo_line("$ First sentence here.", prompt) is True
    assert is_prompt_echo_line("% First sentence here.", prompt) is True
    assert is_prompt_echo_line("# First sentence here.", prompt) is True
    assert is_prompt_echo_line("Hello, world.", "Hello, world.") is True
    assert is_prompt_echo_line("First sentence here.", prompt) is True
    assert is_prompt_echo_line("Second sentence there.", prompt) is True
    assert is_prompt_echo_line("total: 42", prompt) is False
    assert is_prompt_echo_line("OK", prompt) is False
    assert is_prompt_echo_line("Working on it...", prompt) is False
    assert is_prompt_echo_line is implementation
