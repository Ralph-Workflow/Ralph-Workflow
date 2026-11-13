from __future__ import annotations

from pathlib import Path

from ralph.phases.required_artifacts import (
    build_retry_hint,
    build_validation_retry_hint,
    clear_validation_retry_hint,
    retry_fingerprint_path_for_hint,
    retry_hint_fingerprint,
    retry_hint_path,
    validation_retry_fingerprint,
)
from ralph.pipeline.effect_executor import write_agent_retry_prompt
from ralph.prompts import commit_review, materialize
from ralph.recovery.retry_prompt import (
    build_retry_error_block,
    build_validation_retry_footer,
    failure_fingerprint,
)
from ralph.workspace.fs import FsWorkspace

RALPH_TEST_CHANGE_REASONS = {
    "test_retry_prompt_still_exposes_the_formatter_contract": (
        "PRODUCT_CRITERIA unit 3 puts the premise instruction on the deterministic retry "
        "channels, so the ordinary formatter contract stays exported beside the premise helper"
    ),
    "test_failure_fingerprint_is_stable_per_failure_and_differs_across_failures": (
        "PRODUCT_CRITERIA unit 3 decides the premise instruction from the failure "
        "fingerprint, so the failure fingerprint stays stable per failure and differs "
        "across failures"
    ),
    "test_validation_retry_history_keeps_the_rendered_trailer_out_of_the_attempt_bodies": (
        "PRODUCT_CRITERIA unit 3 adds the premise instruction beside the repeated-failure "
        "banner, so the persisted history is asserted against the rendered validation trailer"
    ),
    "test_persisted_hint_history_accumulates_attempts_across_repeats_and_changes": (
        "PRODUCT_CRITERIA unit 3 adds the premise instruction to the repeated-failure "
        "path, so the persisted attempt history is asserted through the accumulated attempts"
    ),
    "test_validation_retry_hint_accumulates_attempts_across_three_identical_attempts": (
        "PRODUCT_CRITERIA unit 3 renders the premise instruction once on an identical "
        "repeat, so a third identical attempt keeps accumulating attempts and the "
        "rendered validation trailer"
    ),
    "test_generic_retry_hint_keeps_the_failure_summary_and_submission_instructions": (
        "PRODUCT_CRITERIA unit 3 leaves the generic retry hint unchanged, so it is "
        "asserted through its failure summary and submission instructions"
    ),
    "test_generic_retry_prompt_renders_the_failure_and_persists_the_failure_fingerprint": (
        "PRODUCT_CRITERIA unit 3 reads the persisted failure fingerprint to decide the "
        "premise instruction, so the generic agent-retry prompt is asserted through its "
        "rendered failure and the persisted fingerprint"
    ),
    "test_persisted_validation_hint_is_rendered_on_a_repeat_and_cleared_afterwards": (
        "PRODUCT_CRITERIA unit 3 renders the premise instruction once on an identical "
        "repeat, so the persisted validation hint is asserted through its rendering on a "
        "repeat and its clearing"
    ),
    "test_prompt_readers_stay_read_only": (
        "PRODUCT_CRITERIA unit 3 keeps the retry state in the runtime, so the runtime "
        "owns the retry state and the hint readers stay read-only"
    ),
}


class _Diagnostic:
    def __init__(self, rule_id: str, message: str) -> None:
        self.rule_id = rule_id
        self.message = message
        self.severity = "error"
        self.section = "frontmatter"
        self.line = 1


def test_retry_prompt_still_exposes_the_formatter_contract() -> None:
    assert callable(build_validation_retry_footer)
    assert callable(build_retry_error_block)
    assert callable(failure_fingerprint)


def test_failure_fingerprint_is_stable_per_failure_and_differs_across_failures() -> None:
    first = failure_fingerprint("artifact rejected", "line 3 invalid")
    repeat = failure_fingerprint("artifact rejected", "line 3 invalid")
    changed = failure_fingerprint("artifact rejected", "line 9 invalid")
    assert first == repeat
    assert first != changed


def test_validation_retry_history_keeps_the_rendered_trailer_out_of_the_attempt_bodies() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]
    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=first,
    )

    assert repeat.count(build_validation_retry_footer()) == 1
    assert repeat.endswith(build_validation_retry_footer())
    assert repeat.count("ATTEMPT 1") == 1
    assert repeat.count("ATTEMPT 2") == 1


def test_persisted_hint_history_accumulates_attempts_across_repeats_and_changes() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]
    failure_b = [_Diagnostic("MD002", "body section is missing")]
    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=first,
    )
    changed = build_validation_retry_hint(
        "development_result",
        failure_b,
        prior_hint=repeat,
    )

    assert first.count("ATTEMPT 1") == 1
    assert repeat.count("ATTEMPT 1") == 1
    assert repeat.count("ATTEMPT 2") == 1
    assert changed.count("ATTEMPT 1") == 1
    assert changed.count("ATTEMPT 2") == 1
    assert changed.count("ATTEMPT 3") == 1
    assert changed.count(build_validation_retry_footer()) == 1


def test_validation_retry_hint_accumulates_attempts_across_three_identical_attempts() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]

    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=first,
    )
    third = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=repeat,
    )

    assert third.count("ATTEMPT 1") == 1
    assert third.count("ATTEMPT 2") == 1
    assert third.count("ATTEMPT 3") == 1
    assert third.count(build_validation_retry_footer()) == 1
    assert third.endswith(build_validation_retry_footer())


def test_generic_retry_hint_keeps_the_failure_summary_and_submission_instructions() -> None:
    first = build_retry_hint("development", "artifact was not submitted")
    repeat = build_retry_hint(
        "development",
        "artifact was not submitted",
    )
    changed = build_retry_hint(
        "development",
        "a different failure happened",
    )

    assert first.count("Submit the artifact now.") == 1
    assert repeat.count("Submit the artifact now.") == 1
    assert changed.count("Submit the artifact now.") == 1
    assert first.count("PREVIOUS ATTEMPT FAILED") == 1
    assert repeat.count("PREVIOUS ATTEMPT FAILED") == 1
    assert changed.count("PREVIOUS ATTEMPT FAILED") == 1


def test_generic_retry_prompt_renders_the_failure_and_persists_the_failure_fingerprint(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Repair the submitted artifact.\n", encoding="utf-8")
    fingerprint_file = tmp_path / retry_fingerprint_path_for_hint(retry_hint_path("development"))

    def render(detail: str) -> tuple[str, str]:
        retry_prompt = write_agent_retry_prompt(
            workspace_root=tmp_path,
            prompt_file=str(prompt_file),
            reason="AgentTimeout",
            detail=detail,
            context_lines=["the agent produced no output"],
            drain="development",
        )
        rendered = Path(retry_prompt).read_text(encoding="utf-8")
        fingerprint = fingerprint_file.read_text(encoding="utf-8").strip()
        return rendered, fingerprint

    first, first_fingerprint = render("the agent produced no output")
    repeat, repeat_fingerprint = render("the agent produced no output")
    changed, changed_fingerprint = render("a different failure happened")

    assert first.count("ERROR RECOVERY REQUIRED") == 1
    assert (
        first.count("PREVIOUS ATTEMPT FAILED: the previous attempt failed because of AgentTimeout")
        == 1
    )
    assert "the agent produced no output" in first
    assert repeat.count("ERROR RECOVERY REQUIRED") == 1
    assert changed.count("ERROR RECOVERY REQUIRED") == 1

    assert first_fingerprint == retry_hint_fingerprint(
        "development", "the agent produced no output"
    )
    assert repeat_fingerprint == first_fingerprint
    assert changed_fingerprint != repeat_fingerprint
    assert changed_fingerprint == retry_hint_fingerprint(
        "development", "a different failure happened"
    )


def test_persisted_validation_hint_is_rendered_on_a_repeat_and_cleared_afterwards(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    hint_path = retry_hint_path("development")
    fingerprint_path = retry_fingerprint_path_for_hint(hint_path)
    failure = [_Diagnostic("MD001", "frontmatter is malformed")]

    first = build_validation_retry_hint("development_result", failure)
    repeat = build_validation_retry_hint("development_result", failure, prior_hint=first)
    workspace.write(hint_path, repeat)
    workspace.write(fingerprint_path, validation_retry_fingerprint("development_result", failure))

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Repair the retained artifact.\n", encoding="utf-8")
    retry_prompt = Path(
        write_agent_retry_prompt(
            workspace_root=tmp_path,
            prompt_file=str(prompt_file),
            reason="ArtifactValidation",
            detail="artifact validation rejected the retained draft",
            context_lines=["the artifact was rejected"],
            drain="development",
        )
    ).read_text(encoding="utf-8")

    assert repeat in retry_prompt
    assert retry_prompt.count("VALIDATION ERRORS (ACCUMULATED)") == 1
    assert retry_prompt.count("ATTEMPT 1") == 1
    assert retry_prompt.count("ATTEMPT 2") == 1

    clear_validation_retry_hint(workspace, "development")

    assert not workspace.exists(hint_path)
    assert not workspace.exists(fingerprint_path)


def test_prompt_readers_stay_read_only() -> None:
    assert hasattr(materialize, "read_retry_hint")
    assert not hasattr(materialize, "read_and_clear_retry_hint")
    assert hasattr(commit_review, "_read_retry_hint")
    assert not hasattr(commit_review, "_read_and_clear_retry_hint")
