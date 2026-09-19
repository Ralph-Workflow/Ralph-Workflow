"""Behavioral coverage for validation-focused agent retry prompts."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.phases.required_artifacts import retry_hint_path
from ralph.pipeline.effect_executor import write_agent_retry_prompt


@pytest.mark.parametrize("recovery_action", ["fresh", "resume", "new_session_with_id"])
def test_validation_retry_prompt_leads_and_ends_with_blocking_guidance(
    tmp_path: Path,
    recovery_action: str,
) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("Repair the submitted artifact.", encoding="utf-8")
    hint_path = tmp_path / retry_hint_path("development")
    hint_path.parent.mkdir(parents=True)
    hint_path.write_text("ATTEMPT 1\nSPEC008 missing evidence", encoding="utf-8")

    retry_prompt = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_path),
        reason="ArtifactValidation",
        context_lines=["The submitted draft failed validation."],
        recovery_action=recovery_action,
        drain="development",
    )

    content = Path(retry_prompt).read_text(encoding="utf-8")

    assert content.startswith("VALIDATION FAILURE")
    assert "SPEC008 missing evidence" in content
    assert "fix the underlying issue" in content.lower()
    assert "do not resubmit unchanged work" in content.lower()
    assert content.rstrip().endswith("Do not resubmit unchanged work.")


def test_validation_retry_prompt_regenerates_with_accumulated_context(tmp_path: Path) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("Repair the submitted artifact.", encoding="utf-8")
    hint_path = tmp_path / retry_hint_path("development")
    hint_path.parent.mkdir(parents=True)
    hint_path.write_text("ATTEMPT 1\nSPEC008 missing evidence", encoding="utf-8")

    first_path = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_path),
        reason="ArtifactValidation",
        context_lines=[],
        drain="development",
    )

    hint_path.write_text(
        "ATTEMPT 1\nSPEC008 missing evidence\n\nATTEMPT 2\nMD002 malformed frontmatter",
        encoding="utf-8",
    )
    second_path = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_path),
        reason="ArtifactValidation",
        context_lines=[],
        drain="development",
    )

    first_content = Path(first_path).read_text(encoding="utf-8")
    second_content = Path(second_path).read_text(encoding="utf-8")

    assert first_path != second_path
    assert "ATTEMPT 1" in first_content
    assert "ATTEMPT 1" in second_content
    assert "ATTEMPT 2" in second_content
    assert "MD002 malformed frontmatter" in second_content


def test_non_validation_retry_prompt_preserves_generic_recovery_heading(tmp_path: Path) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("Repair the submitted artifact.", encoding="utf-8")

    retry_prompt = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_path),
        reason="InactivityTimeout",
        context_lines=[],
        drain="development",
    )

    assert Path(retry_prompt).read_text(encoding="utf-8").startswith("ERROR RECOVERY REQUIRED")
