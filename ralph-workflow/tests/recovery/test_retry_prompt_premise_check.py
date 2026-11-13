from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.markdown import Diagnostic
from ralph.phases.required_artifacts import (
    build_validation_retry_hint,
    retry_fingerprint_path_for_hint,
    retry_hint_fingerprint,
    retry_hint_path,
)
from ralph.pipeline.effect_executor import write_agent_retry_prompt
from ralph.recovery.retry_prompt import PREMISE_CHECK_LINE, premise_check_line

RALPH_TEST_CHANGE_REASONS = {
    "test_premise_check_line_matches_only_an_identical_repeat_fingerprint": (
        "PRODUCT_CRITERIA unit 3 puts the premise instruction on the deterministic retry "
        "channels, so the helper matches only an identical repeat fingerprint"
    ),
    "test_validation_retry_hint_renders_the_premise_line_only_on_an_identical_repeat": (
        "PRODUCT_CRITERIA unit 3 puts the premise instruction on the deterministic retry "
        "channels, so the validation hint renders the line on an identical repeat and "
        "leaves it off a changed failure"
    ),
    "test_validation_retry_hint_keeps_the_premise_line_once_across_three_identical_attempts": (
        "PRODUCT_CRITERIA unit 3 puts the premise instruction on the deterministic retry "
        "channels, so a third identical attempt still carries the line exactly once"
    ),
    "test_generic_retry_prompt_renders_the_premise_line_only_on_an_identical_repeat": (
        "PRODUCT_CRITERIA unit 3 puts the premise instruction on the deterministic retry "
        "channels, so the generic agent-retry prompt renders the line on an identical "
        "repeat and leaves it off a changed failure"
    ),
    "test_generic_retry_prompt_persists_the_failure_fingerprint_on_the_first_failure": (
        "PRODUCT_CRITERIA unit 3 decides the premise instruction from the persisted "
        "failure fingerprint, so the first failure still persists it"
    ),
    "test_generic_retry_prompt_resume_tail_renders_the_premise_line_once": (
        "PRODUCT_CRITERIA unit 3 puts the premise instruction on the deterministic retry "
        "channels, so the resume tail carries the line exactly once"
    ),
    "test_generic_retry_prompt_new_session_tail_renders_the_premise_line_once": (
        "PRODUCT_CRITERIA unit 3 puts the premise instruction on the deterministic retry "
        "channels, so the new-session tail carries the line exactly once"
    ),
    "test_generic_retry_prompt_keeps_a_validation_hint_premise_line_undoubled": (
        "PRODUCT_CRITERIA unit 3 renders the premise instruction exactly once per prompt, "
        "so a validation hint that already carries it is not doubled"
    ),
    "test_generic_retry_prompt_omits_the_premise_line_when_only_the_reason_changes": (
        "PRODUCT_CRITERIA unit 3 emits the premise instruction only for an identical "
        "repeat failure, so a changed reason with identical detail leaves it off"
    ),
    "test_generic_retry_prompt_resume_tail_omits_the_premise_line_when_only_the_reason_changes": (
        "PRODUCT_CRITERIA unit 3 emits the premise instruction only for an identical "
        "repeat failure, so the resume tail leaves it off when only the reason changes"
    ),
    "test_generic_retry_prompt_new_session_tail_omits_the_premise_line_when_only_the_reason_changes": (
        "PRODUCT_CRITERIA unit 3 emits the premise instruction only for an identical "
        "repeat failure, so the new-session tail leaves it off when only the reason changes"
    ),
}


def _diagnostic(rule_id: str, message: str) -> Diagnostic:
    return Diagnostic(line=1, section="frontmatter", rule_id=rule_id, message=message)


def _write_prompt(tmp_path: Path) -> Path:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Repair the retained artifact.\n", encoding="utf-8")
    return prompt_file


def _render(
    tmp_path: Path,
    prompt_file: Path,
    detail: str,
    *,
    recovery_action: str | None = None,
    reason: str = "AgentTimeout",
) -> str:
    retry_prompt = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_file),
        reason=reason,
        detail=detail,
        context_lines=["the agent produced no output"],
        drain="development",
        recovery_action=recovery_action,
    )
    return Path(retry_prompt).read_text(encoding="utf-8")


def test_premise_check_line_matches_only_an_identical_repeat_fingerprint() -> None:
    assert PREMISE_CHECK_LINE == "test the premise shared by the failed fixes before another patch"
    assert premise_check_line("same", "same") == PREMISE_CHECK_LINE
    assert premise_check_line("same", "different") == ""
    assert premise_check_line("", "") == ""
    assert premise_check_line("same", "") == ""
    assert premise_check_line("", "same") == ""


def test_validation_retry_hint_renders_the_premise_line_only_on_an_identical_repeat() -> None:
    failure_a = [_diagnostic("MD001", "frontmatter is malformed")]
    failure_b = [_diagnostic("MD002", "body section is missing")]

    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint("development_result", failure_a, prior_hint=first)
    changed = build_validation_retry_hint("development_result", failure_b, prior_hint=repeat)

    assert first.count(PREMISE_CHECK_LINE) == 0
    assert repeat.count(PREMISE_CHECK_LINE) == 1
    assert changed.count(PREMISE_CHECK_LINE) == 0


def test_validation_retry_hint_keeps_the_premise_line_once_across_three_identical_attempts() -> (
    None
):
    failure_a = [_diagnostic("MD001", "frontmatter is malformed")]

    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint("development_result", failure_a, prior_hint=first)
    third = build_validation_retry_hint("development_result", failure_a, prior_hint=repeat)

    assert first.count(PREMISE_CHECK_LINE) == 0
    assert repeat.count(PREMISE_CHECK_LINE) == 1
    assert third.count(PREMISE_CHECK_LINE) == 1


def test_generic_retry_prompt_renders_the_premise_line_only_on_an_identical_repeat(
    tmp_path: Path,
) -> None:
    prompt_file = _write_prompt(tmp_path)

    first = _render(tmp_path, prompt_file, "the agent produced no output")
    repeat = _render(tmp_path, prompt_file, "the agent produced no output")
    changed = _render(tmp_path, prompt_file, "a different failure happened")

    assert first.count(PREMISE_CHECK_LINE) == 0
    assert repeat.count(PREMISE_CHECK_LINE) == 1
    assert changed.count(PREMISE_CHECK_LINE) == 0


def test_generic_retry_prompt_persists_the_failure_fingerprint_on_the_first_failure(
    tmp_path: Path,
) -> None:
    prompt_file = _write_prompt(tmp_path)
    fingerprint_file = tmp_path / retry_fingerprint_path_for_hint(retry_hint_path("development"))

    first = _render(tmp_path, prompt_file, "the agent produced no output")

    assert first.count(PREMISE_CHECK_LINE) == 0
    assert fingerprint_file.read_text(encoding="utf-8").strip() == retry_hint_fingerprint(
        "development", "the agent produced no output"
    )


def test_generic_retry_prompt_resume_tail_renders_the_premise_line_once(tmp_path: Path) -> None:
    prompt_file = _write_prompt(tmp_path)

    _render(tmp_path, prompt_file, "the agent produced no output", recovery_action="resume")
    repeat = _render(
        tmp_path, prompt_file, "the agent produced no output", recovery_action="resume"
    )

    assert repeat.count(PREMISE_CHECK_LINE) == 1


def test_generic_retry_prompt_new_session_tail_renders_the_premise_line_once(
    tmp_path: Path,
) -> None:
    prompt_file = _write_prompt(tmp_path)

    _render(
        tmp_path, prompt_file, "the agent produced no output", recovery_action="new_session_with_id"
    )
    repeat = _render(
        tmp_path,
        prompt_file,
        "the agent produced no output",
        recovery_action="new_session_with_id",
    )

    assert repeat.count(PREMISE_CHECK_LINE) == 1


def test_generic_retry_prompt_omits_the_premise_line_when_only_the_reason_changes(
    tmp_path: Path,
) -> None:
    prompt_file = _write_prompt(tmp_path)

    first = _render(tmp_path, prompt_file, "the agent produced no output")
    changed_reason = _render(
        tmp_path,
        prompt_file,
        "the agent produced no output",
        reason="AgentCrash",
    )

    assert first.count(PREMISE_CHECK_LINE) == 0
    assert changed_reason.count(PREMISE_CHECK_LINE) == 0


def test_generic_retry_prompt_resume_tail_omits_the_premise_line_when_only_the_reason_changes(
    tmp_path: Path,
) -> None:
    prompt_file = _write_prompt(tmp_path)

    _render(tmp_path, prompt_file, "the agent produced no output", recovery_action="resume")
    changed_reason = _render(
        tmp_path,
        prompt_file,
        "the agent produced no output",
        recovery_action="resume",
        reason="AgentCrash",
    )

    assert changed_reason.count(PREMISE_CHECK_LINE) == 0


def test_generic_retry_prompt_new_session_tail_omits_the_premise_line_when_only_the_reason_changes(
    tmp_path: Path,
) -> None:
    prompt_file = _write_prompt(tmp_path)

    _render(
        tmp_path,
        prompt_file,
        "the agent produced no output",
        recovery_action="new_session_with_id",
    )
    changed_reason = _render(
        tmp_path,
        prompt_file,
        "the agent produced no output",
        recovery_action="new_session_with_id",
        reason="AgentCrash",
    )

    assert changed_reason.count(PREMISE_CHECK_LINE) == 0


def test_generic_retry_prompt_keeps_a_validation_hint_premise_line_undoubled(
    tmp_path: Path,
) -> None:
    prompt_file = _write_prompt(tmp_path)
    failure = [_diagnostic("MD001", "frontmatter is malformed")]
    first_hint = build_validation_retry_hint("development_result", failure)
    repeated_hint = build_validation_retry_hint(
        "development_result", failure, prior_hint=first_hint
    )
    hint_path = tmp_path / retry_hint_path("development")
    hint_path.parent.mkdir(parents=True, exist_ok=True)
    hint_path.write_text(repeated_hint, encoding="utf-8")
    fingerprint_path = tmp_path / retry_fingerprint_path_for_hint(retry_hint_path("development"))
    fingerprint_path.write_text(
        retry_hint_fingerprint("development", "the agent produced no output"), encoding="utf-8"
    )

    rendered = _render(tmp_path, prompt_file, "the agent produced no output")

    assert repeated_hint.count(PREMISE_CHECK_LINE) == 1
    assert rendered.count(PREMISE_CHECK_LINE) == 1
