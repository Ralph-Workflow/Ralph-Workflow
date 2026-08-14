"""Tests for commit prompt generation."""

from pathlib import Path

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

    # The prompt names the artifact and its submission path in the opening
    # line (tightened 2026-08-12 from the older "spec-compliant mcp
    # commit_message artifact" phrasing; the contract is unchanged).
    assert "produce and submit one valid `commit_message` artifact" in prompt.lower()
    assert diff in prompt
    assert "<ralph-commit>" not in prompt
    assert "<ralph-subject>" not in prompt
    assert "## HOW TO WRITE THE SUBJECT" in prompt
    assert "## HOW TO WRITE THE BODY" in prompt
    # Anti-churn: a weak model told only to "write a good subject" enumerates
    # candidates and re-picks. The prompt must give a terminating procedure.
    assert "first valid lowercase imperative subject" in prompt.lower()
    assert "do not generate alternatives" in prompt.lower()
    # No unenforced constraint an agent could keep failing (e.g. a length cap
    # the canonical validator never checks).
    assert "there is no length limit" in prompt.lower()
    assert "commit document" in prompt.lower()
    assert "skip document" in prompt.lower()
    # Architectural fix (2026-06-14): the template MUST NOT carry a
    # "REQUIRED PROCEDURE" that duplicates the shared artifact submission
    # macro. A duplicate procedure (e.g. "output only the commit subject
    # line") used to mislead a small model into stopping without calling
    # ``declare_complete``, leaving the gate to retry forever. The macro
    # is the single source of truth for the completion contract.
    assert "output only the commit subject line" not in prompt.lower()
    assert "declare_complete" in prompt.lower()
    # The artifact is a markdown document: the decision lives in frontmatter.
    assert "type: commit" in prompt
    assert "subject: fix(auth): prevent token expiry race" in prompt
    assert "type: skip" in prompt
    assert "## Files" in prompt
    assert "## Excluded Files" in prompt
    assert "internal_ignore, not_task_related, sensitive, deferred" in prompt
    # Tightened opening (2026-08-12) replaced the old "Task:" prefix; the
    # instruction line now opens the prompt directly.
    assert prompt.startswith("Produce and submit one valid `commit_message` artifact")
    assert "tool named" in prompt.lower()
    assert "do not call bash" in prompt.lower()
    # The write-file fallback promotes a validated markdown document, not JSON.
    assert ".agent/tmp/commit_message.md" in prompt
    assert "raw markdown" in prompt.lower()
    assert "edit the json file on disk" not in prompt.lower()
    # The grammar the canonical validator enforces is stated in full, so a
    # rejection is a bug in the prompt rather than something to iterate on.
    assert "changes only repo maintenance, tooling, config, or dependencies | chore" in prompt
    assert "otherwise omit the scope and the parentheses entirely" in prompt.lower()
    assert "no dots, no uppercase" in prompt.lower()
    assert "quotes become part of the subject" in prompt.lower()
    assert "starts with a lowercase letter or digit" in prompt.lower()
    # The doc-shape bullet names both document choices (tightened
    # 2026-08-12: "for pending work" replaced the older "changes not yet
    # committed" framing).
    assert "pending work" in prompt.lower()
    assert "when no commit is needed" in prompt.lower()
    # The tightening dropped the "current worktree vs the last commit"
    # framing; "for the pending diff" in the opening line carries the
    # same scope statement.


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
            "mcp__ralph__ralph_submit_md_artifact",
            "ralph_submit_md_artifact",
        ),
    )

    assert "ralph_submit_md_artifact" in prompt
    assert "mcp__ralph__ralph_submit_md_artifact" in prompt
    assert "mcp__ralph__declare_complete" in prompt
    assert "mcp__ralph__write_file" in prompt
    assert '`mcp__ralph__declare_complete(summary="commit_message")`' in prompt


def test_opencode_commit_prompt_uses_direct_tool_call_language() -> None:
    prompt = prompt_commit_message_for_opencode(
        "diff --git a/app.py b/app.py\n+hello",
        submit_artifact_tool_name="ralph_ralph_submit_md_artifact",
    )

    # Tightened opening (2026-08-12): names the artifact and the pending
    # diff directly, replacing the older "current pending work" framing.
    assert "Produce one valid `commit_message` artifact for the pending diff" in prompt
    # The 2026-08-12 tightening dropped the "current worktree vs the last
    # commit" framing; "for the pending diff" carries the same scope
    # statement in the simplified template.
    # Silent single-pass reading replaced the older "Do not analyze
    # anything" instruction.
    assert "Read the diff silently" in prompt
    assert "`ralph_ralph_submit_md_artifact`" in prompt
    # The artifact is a markdown document: the decision lives in frontmatter.
    assert "type: commit" in prompt
    assert "subject: fix(auth): prevent token expiry race" in prompt
    assert "type: skip" in prompt
    assert "path | reason" in prompt
    # The tightened skip-reason enumeration wraps across lines with
    # backticked tokens; pin each token rather than one long phrase.
    for token in ("internal_ignore", "not_task_related", "sensitive", "deferred"):
        assert token in prompt
    assert "state-changing tools allowed are" in prompt
    assert "ralph_declare_complete" in prompt
    assert "ralph_write_file" in prompt
    assert '`ralph_declare_complete(summary="commit_message")`' in prompt
    assert "Do not call bash" in prompt
    # The unavailable-tool fallback lives in the SHARED artifact submission
    # macro, not in a duplicate procedure section, and promotes a validated
    # markdown document — never JSON.
    assert ".agent/tmp/commit_message.md" in prompt
    assert "raw markdown" in prompt.lower()
    # Subject grammar (tightened 2026-08-12): the kind table moved into one
    # inline sentence; the ``chore`` restriction and the scope alphabet are
    # the load-bearing constraints.
    assert "use `chore` only for" in prompt
    assert "maintenance, tooling, config, or dependencies" in prompt
    assert "Scope is optional" in prompt
    assert "lowercase letters, digits, `/`, `_`, or `-`" in prompt
    assert "lowercase imperative description" in prompt
    # Body policy: omit only for trivial one-line changes.
    assert "Omit the body only for a one-line typo, formatting, or comment change" in prompt
    # Anti-churn: the simplified subject instruction terminates selection
    # with a single "once" pass (the full template's "do not generate
    # alternatives" phrasing is deliberately absent here).
    assert "Write `<kind>(<scope>)?!?: <description>` once" in prompt
    # Tightened opening (2026-08-12) replaced the old "Task:" prefix; the
    # instruction line now opens the prompt directly.
    assert prompt.startswith("Produce one valid")


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
    assert "MANDATORY FINAL ACTION" in prompt


def test_commit_prompt_explicitly_forbids_confirmation_questions() -> None:
    """The commit prompt must forbid confirmation questions outright.

    The prohibition lives in the shared ``_unattended_mode`` partial, which
    every commit prompt includes. The 2026-08-12 tightening dropped the
    "would you like me to" example phrase; the behavioral prohibition it
    illustrated is still pinned here by its normative sentence.
    """
    prompt = prompt_commit_message("diff --git a/app.py b/app.py\n+hello")

    assert "do not ask the user for confirmation" in prompt.lower()


def test_commit_prompt_uses_file_reference_for_large_diff(tmp_path: Path) -> None:
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


def test_opencode_commit_prompt_uses_file_reference_for_large_diff(tmp_path: Path) -> None:
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
