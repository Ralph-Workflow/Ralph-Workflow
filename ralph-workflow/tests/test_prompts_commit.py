"""Tests for commit prompt generation."""

import pytest

from ralph.prompts.commit import (
    CommitPromptPayloadConfig,
    prompt_commit_message,
    prompt_commit_message_for_opencode,
)
from ralph.prompts.template_registry import TemplateRegistry


def test_commit_prompt_includes_diff_and_guidance() -> None:
    diff = "diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-foo\n+bar"
    prompt = prompt_commit_message(diff)

    assert "spec-compliant mcp commit_message artifact" in prompt.lower()
    assert diff in prompt
    assert "<ralph-commit>" not in prompt
    assert "<ralph-subject>" not in prompt
    assert "## COMMIT MESSAGE FORMAT" in prompt
    assert "## WHEN TO USE A BODY" in prompt
    assert "most commits need a body" in prompt.lower()
    assert "commit document" in prompt.lower()
    assert "skip document" in prompt.lower()
    # Architectural fix (2026-06-14): the template MUST NOT carry a
    # "REQUIRED PROCEDURE" that duplicates the shared artifact submission
    # macro. A duplicate procedure (e.g. "output only the commit subject
    # line") used to mislead a small model into stopping without calling
    # ``declare_complete``, leaving the gate to retry forever. The macro
    # is the single source of truth for the completion contract.
    assert "output only the commit subject line" not in prompt.lower()
    assert "artifact submission procedure above is authoritative" in prompt.lower()
    assert "declare_complete" in prompt.lower()
    # The artifact is a markdown document: the decision lives in frontmatter.
    assert "type: commit" in prompt
    assert "subject: fix(auth): prevent token expiry race" in prompt
    assert "type: skip" in prompt
    assert "## Files" in prompt
    assert "## Excluded Files" in prompt
    assert "internal_ignore, not_task_related, sensitive, deferred" in prompt
    assert prompt.startswith("Task:")
    assert "tool named" in prompt.lower()
    assert "do not call bash" in prompt.lower()
    # The write-file fallback promotes a validated markdown document, not JSON.
    assert ".agent/tmp/commit_message.md" in prompt
    assert "raw markdown" in prompt.lower()
    assert "edit the json file on disk" not in prompt.lower()
    assert "use `chore` only for repo maintenance" in prompt.lower()
    assert "omit the scope when the change spans multiple subsystems" in prompt.lower()
    assert "one-liner subjects (no body) are only acceptable for" in prompt.lower()
    assert "common mistakes to avoid" in prompt.lower()
    assert "bad: chore: update files" in prompt.lower()
    assert "bad: fix: stuff" in prompt.lower()
    assert "changes not yet committed" in prompt.lower()
    assert "current worktree vs the last commit" in prompt.lower()


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
        submit_artifact_tool_names=(
            "ralph_submit_md_artifact",
            "mcp__ralph__ralph_submit_md_artifact",
        ),
    )

    assert "ralph_submit_md_artifact" in prompt
    assert "mcp__ralph__ralph_submit_md_artifact" in prompt


def test_opencode_commit_prompt_uses_direct_tool_call_language() -> None:
    prompt = prompt_commit_message_for_opencode(
        "diff --git a/app.py b/app.py\n+hello",
        submit_artifact_tool_name="ralph_submit_md_artifact",
    )

    assert "current pending work" in prompt
    assert "current worktree vs the last commit" in prompt
    assert "Do not analyze anything" in prompt
    assert "`ralph_submit_md_artifact`" in prompt
    # The artifact is a markdown document: the decision lives in frontmatter.
    assert "type: commit" in prompt
    assert "subject: fix(auth): prevent token expiry race" in prompt
    assert "type: skip" in prompt
    assert "path | reason" in prompt
    assert "internal_ignore, not_task_related, sensitive, deferred" in prompt
    assert "The only state-changing tools you may call" in prompt
    assert "declare_complete" in prompt
    assert "write_file" in prompt
    assert "Do not call bash" in prompt
    # The unavailable-tool fallback lives in the SHARED artifact submission
    # macro, not in a duplicate procedure section, and promotes a validated
    # markdown document — never JSON.
    assert ".agent/tmp/commit_message.md" in prompt
    assert "raw markdown" in prompt.lower()
    assert "Use `chore` only for repo maintenance" in prompt
    assert "Omit the scope when the change spans multiple subsystems" in prompt
    assert "Most commits need a body" in prompt
    assert "One-liner subjects (no body) are only" in prompt
    assert "Bad" in prompt
    assert "Good" in prompt


def test_opencode_commit_prompt_skip_output_instruction_is_unambiguous() -> None:
    prompt = prompt_commit_message_for_opencode(
        "diff --git a/app.py b/app.py\n+hello",
        submit_artifact_tool_name="ralph_submit_md_artifact",
    )

    # The old "<subject>" placeholder caused models to output "<skip>" for skip artifacts.
    # The instruction must now be explicit for both commit and skip cases.
    assert "<subject>" not in prompt
    # The skip-output instruction is not a duplicate procedure; the shared
    # artifact submission macro is the authoritative completion contract.
    assert "output only the commit subject line" not in prompt.lower()
    assert "follow the artifact submission procedure above" in prompt.lower()


def test_commit_prompt_explicitly_forbids_confirmation_questions() -> None:
    prompt = prompt_commit_message("diff --git a/app.py b/app.py\n+hello")

    assert "do not ask the user for confirmation" in prompt.lower()
    assert "would you like me to" in prompt.lower()


def test_commit_prompt_uses_file_reference_for_large_diff(tmp_path: object) -> None:
    diff = "x" * (100 * 1024 + 1)

    prompt = prompt_commit_message(
        diff,
        payload_config=CommitPromptPayloadConfig(
            output_dir=tmp_path,
            name_prefix="development_commit",
        ),
    )

    assert "read the complete diff from file at" in prompt.lower()
    assert diff not in prompt
    payload_file = tmp_path / "development_commit_diff.txt"
    assert payload_file.read_text(encoding="utf-8") == diff


def test_opencode_commit_prompt_uses_file_reference_for_large_diff(tmp_path: object) -> None:
    diff = "x" * (100 * 1024 + 1)

    prompt = prompt_commit_message_for_opencode(
        diff,
        submit_artifact_tool_name="ralph_submit_md_artifact",
        payload_config=CommitPromptPayloadConfig(
            output_dir=tmp_path,
            name_prefix="review_commit",
        ),
    )

    assert "read the complete diff from file at" in prompt.lower()
    assert diff not in prompt
    payload_file = tmp_path / "review_commit_diff.txt"
    assert payload_file.read_text(encoding="utf-8") == diff
