"""Tests for commit prompt generation."""

import pytest

from ralph.prompts.commit import prompt_commit_message
from ralph.prompts.template_registry import TemplateRegistry


def test_commit_prompt_includes_diff_and_guidance() -> None:
    diff = "diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-foo\n+bar"
    prompt = prompt_commit_message(diff)

    assert "conventional" in prompt.lower()
    assert diff in prompt
    assert "<ralph-commit>" not in prompt
    assert "<ralph-subject>" not in prompt
    assert "plain-text" in prompt.lower()
    assert "## COMMIT MESSAGE FORMAT" in prompt


def test_commit_prompt_rejects_empty_diff() -> None:
    with pytest.raises(ValueError):
        prompt_commit_message("   \n \t ")


def test_commit_prompt_uses_registry_templates() -> None:
    registry = TemplateRegistry()
    registry.register_template("commit_message", "OVERRIDE {{ DIFF }}\n")

    result = prompt_commit_message("custom diff", template_registry=registry)

    assert result == "OVERRIDE custom diff\n"
