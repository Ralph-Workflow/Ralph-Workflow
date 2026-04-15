"""Tests for commit prompt generation."""

import pytest

from ralph.prompts.commit import prompt_commit_message, prompt_commit_message_for_opencode
from ralph.prompts.template_registry import TemplateRegistry


def test_commit_prompt_includes_diff_and_guidance() -> None:
    diff = "diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-foo\n+bar"
    prompt = prompt_commit_message(diff)

    assert "spec-compliant mcp commit_message artifact" in prompt.lower()
    assert diff in prompt
    assert "<ralph-commit>" not in prompt
    assert "<ralph-subject>" not in prompt
    assert "plain text" in prompt.lower()
    assert "mcp artifact is the authoritative output" in prompt.lower()
    assert "## COMMIT MESSAGE FORMAT" in prompt
    assert "do not explain the diff" in prompt.lower()
    assert "call the tool before emitting any final text" in prompt.lower()
    assert "commit artifact" in prompt.lower()
    assert "skip artifact" in prompt.lower()
    assert "optionally echo only the commit subject line once" in prompt.lower()
    assert '"type": "commit"' in prompt
    assert '"subject": "type(scope): description"' in prompt
    assert '"type": "skip"' in prompt
    assert '"reason": "Reason why no commit is needed"' in prompt
    assert '"files": ["src/auth/token.rs", "tests/auth/token_expiry_test.rs"]' in prompt
    assert '"excluded_files": [{"path": "notes/todo.md", "reason": "not_task_related"}]' in prompt
    assert "internal_ignore, not_task_related, sensitive, deferred" in prompt
    assert prompt.startswith("Task:")
    assert "tool named" in prompt.lower()


def test_commit_prompt_rejects_empty_diff() -> None:
    with pytest.raises(ValueError):
        prompt_commit_message("   \n \t ")


def test_commit_prompt_uses_registry_templates() -> None:
    registry = TemplateRegistry()
    registry.register_template("commit_message", "OVERRIDE {{ DIFF }}\n")

    result = prompt_commit_message("custom diff", template_registry=registry)

    assert result == "OVERRIDE custom diff\n"


def test_commit_prompt_includes_prefixed_submit_artifact_aliases() -> None:
    prompt = prompt_commit_message(
        "diff --git a/app.py b/app.py\n+hello",
        submit_artifact_tool_names=("ralph_submit_artifact", "mcp__ralph__ralph_submit_artifact"),
    )

    assert "ralph_submit_artifact" in prompt
    assert "mcp__ralph__ralph_submit_artifact" in prompt


def test_opencode_commit_prompt_uses_direct_tool_call_language() -> None:
    prompt = prompt_commit_message_for_opencode(
        "diff --git a/app.py b/app.py\n+hello",
        submit_artifact_tool_name="ralph_submit_artifact",
    )

    assert prompt.startswith("Do not analyze anything.")
    assert "Immediately call `ralph_submit_artifact`" in prompt
    assert 'artifact_type="commit_message"' in prompt
    assert '{"type":"commit","subject":"type(scope): description"}' in prompt
    assert (
        '{"type":"commit","subject":"type(scope): description",'
        '"excluded_files":[{"path":"notes/todo.md","reason":"not_task_related"}]}' in prompt
    )
    assert "json string" in prompt.lower()
    assert '{"type":"skip","reason":"Reason why no commit is needed"}' in prompt
    assert "internal_ignore, not_task_related, sensitive, deferred" in prompt
    assert "The only tool you may call" in prompt
    assert "Do not call bash" in prompt
