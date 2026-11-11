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

    assert "produce and submit one valid `commit_message` artifact" in prompt.lower()
    assert diff in prompt
    assert "<ralph-commit>" not in prompt
    assert "<ralph-subject>" not in prompt
    assert "## HOW TO WRITE THE SUBJECT" in prompt
    assert "## HOW TO WRITE THE BODY" in prompt
    assert "commit document" in prompt.lower()
    assert "skip document" in prompt.lower()
    assert "output only the commit subject line" not in prompt.lower()
    assert "declare_complete" in prompt.lower()
    assert "type: commit" in prompt
    assert "subject: fix(auth): prevent token expiry race" in prompt
    assert "type: skip" in prompt
    assert prompt.startswith("Produce and submit one valid `commit_message` artifact")
    assert "tool named" in prompt.lower()
    assert "do not call bash" in prompt.lower()
    assert ".agent/tmp/commit_message.md" in prompt
    assert "raw markdown" in prompt.lower()
    assert "edit the json file on disk" not in prompt.lower()
    assert "pending work" in prompt.lower()
    assert "when no commit is needed" in prompt.lower()


@pytest.mark.parametrize("opencode", [False, True])
def test_commit_prompts_lead_with_a_persisted_validation_hint_without_consuming_it(
    tmp_path: Path,
    opencode: bool,
) -> None:
    hint_path = tmp_path / ".agent" / "tmp" / "last_retry_error_commit.txt"
    hint_path.parent.mkdir(parents=True)
    hint = "VALIDATION FAILURE\nCOMMIT001: invalid subject"
    hint_path.write_text(hint, encoding="utf-8")
    diff = "diff --git a/app.py b/app.py\n+hello"

    if opencode:
        prompt = prompt_commit_message_for_opencode(
            diff,
            submit_artifact_tool_name="ralph_submit_md_artifact",
            workspace_root=tmp_path,
        )
    else:
        prompt = prompt_commit_message(diff, workspace_root=tmp_path)

    assert prompt.startswith(hint)
    assert hint_path.read_text(encoding="utf-8") == hint


def test_commit_prompt_rejects_empty_diff() -> None:
    with pytest.raises(ValueError):
        prompt_commit_message("   \n \t ")


def test_standalone_commit_prompt_omits_pipeline_context_links() -> None:
    prompt = prompt_commit_message("diff --git a/app.py b/app.py\n+hello")

    assert ".agent/artifacts/development_result.md" not in prompt
    assert ".agent/artifacts/plan.md" not in prompt
    assert ".agent/PRODUCT_CRITERIA.md" not in prompt


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

    assert "Produce one valid `commit_message` artifact for the pending diff" in prompt
    assert "Read the diff silently" in prompt
    assert "`ralph_ralph_submit_md_artifact`" in prompt
    assert "type: commit" in prompt
    assert "subject: fix(auth): prevent token expiry race" in prompt
    assert "type: skip" in prompt
    assert "state-changing tools allowed are" in prompt
    assert "ralph_declare_complete" in prompt
    assert "ralph_write_file" in prompt
    assert '`ralph_declare_complete(summary="commit_message")`' in prompt
    assert "Do not call bash" in prompt
    assert ".agent/tmp/commit_message.md" in prompt
    assert "raw markdown" in prompt.lower()
    assert prompt.startswith("Produce one valid")


def test_opencode_commit_prompt_skip_output_instruction_is_unambiguous() -> None:
    prompt = prompt_commit_message_for_opencode(
        "diff --git a/app.py b/app.py\n+hello",
        submit_artifact_tool_name="ralph_submit_md_artifact",
    )

    assert "<subject>" not in prompt
    assert "output only the commit subject line" not in prompt.lower()
    assert "MANDATORY FINAL ACTION" in prompt


def test_commit_prompt_explicitly_forbids_confirmation_questions() -> None:
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
