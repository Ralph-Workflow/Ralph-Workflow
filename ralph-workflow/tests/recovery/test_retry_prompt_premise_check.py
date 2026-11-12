from __future__ import annotations

from pathlib import Path

from ralph.phases.required_artifacts import (
    build_retry_hint,
    build_validation_retry_hint,
    retry_hint_fingerprint,
    validation_retry_fingerprint,
)
from ralph.pipeline.effect_executor import write_agent_retry_prompt
from ralph.prompts import commit_review, materialize
from ralph.recovery.retry_prompt import (
    PREMISE_CHECK_LINE,
    build_premise_check_block,
    build_retry_error_block,
    build_validation_retry_footer,
    failure_fingerprint,
    premise_check_line,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_retry_prompt_exposes_the_premise_check_contract": "the premise-check rule is re-landed, so the retry prompt exposes the premise-check contract again",
    "test_retry_prompt_still_exposes_the_formatter_contract": "the premise-check rule is re-landed, so the ordinary formatter contract stays exported",
    "test_failure_fingerprint_is_stable_per_failure_and_differs_across_failures": "the premise-check rule is re-landed on runtime fingerprints, which stay stable per failure and differ across failures",
    "test_validation_retry_hint_renders_the_premise_line_only_on_an_identical_repeat": "the retirement-era expectation that no attempt carries the premise line was wrong: the rule is re-landed, so an identical repeat renders the line",
    "test_generic_retry_hint_renders_the_premise_line_only_on_an_identical_repeat": "the retirement-era expectation that no attempt carries the premise line was wrong: the rule is re-landed, so an identical repeat renders the line",
    "test_persisted_hint_history_keeps_the_premise_line_off_a_changed_failure": "the retirement-era expectation that the persisted history keeps the line off every attempt was wrong: the rule is re-landed, so an identical repeat carries it",
    "test_validation_retry_history_keeps_the_rendered_trailer_out_of_the_attempt_bodies": "the retirement-era expectation that the rendered history ends with the trailer was wrong: the rule is re-landed, so the history ends with the premise line",
    "test_generic_technical_retry_prompt_renders_premise_line_only_on_identical_repeat": "new black-box coverage: the direct agent-retry prompt carries the premise line exactly once on an identical repeat and never on a first or changed failure",
    "test_generic_technical_retry_prompt_without_drain_renders_premise_line_only_on_identical_repeat": "new black-box coverage: without a drain the fingerprint scope falls back to the reason, so an identical repeat carries the premise line exactly once",
    "test_prompt_readers_stay_read_only": "the premise-check rule is re-landed, so the runtime owns the retry state and the hint readers stay read-only",
}


class _Diagnostic:
    def __init__(self, rule_id: str, message: str) -> None:
        self.rule_id = rule_id
        self.message = message
        self.severity = "error"
        self.section = "frontmatter"
        self.line = 1


def test_retry_prompt_exposes_the_premise_check_contract() -> None:
    assert PREMISE_CHECK_LINE == "test the premise shared by the failed fixes before another patch"
    assert callable(premise_check_line)
    assert build_premise_check_block is premise_check_line


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


def test_validation_retry_hint_renders_the_premise_line_only_on_an_identical_repeat() -> None:
    diagnostics = [_Diagnostic("MD001", "frontmatter is malformed")]
    fingerprint = validation_retry_fingerprint("development_result", diagnostics)
    first = build_validation_retry_hint("development_result", diagnostics)
    repeat = build_validation_retry_hint(
        "development_result",
        diagnostics,
        prior_fingerprint=fingerprint,
    )
    changed = build_validation_retry_hint(
        "development_result",
        [_Diagnostic("MD002", "body section is missing")],
        prior_fingerprint=fingerprint,
    )
    assert PREMISE_CHECK_LINE not in first
    assert PREMISE_CHECK_LINE in repeat
    assert PREMISE_CHECK_LINE not in changed


def test_generic_retry_hint_renders_the_premise_line_only_on_an_identical_repeat() -> None:
    fingerprint = retry_hint_fingerprint("development", "artifact was not submitted")
    first = build_retry_hint("development", "artifact was not submitted")
    repeat = build_retry_hint(
        "development",
        "artifact was not submitted",
        prior_fingerprint=fingerprint,
    )
    changed = build_retry_hint(
        "development",
        "a different failure happened",
        prior_fingerprint=fingerprint,
    )
    assert PREMISE_CHECK_LINE not in first
    assert PREMISE_CHECK_LINE in repeat
    assert PREMISE_CHECK_LINE not in changed


def test_persisted_hint_history_keeps_the_premise_line_off_a_changed_failure() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]
    failure_b = [_Diagnostic("MD002", "body section is missing")]
    fingerprint_a = validation_retry_fingerprint("development_result", failure_a)

    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=first,
        prior_fingerprint=fingerprint_a,
    )
    changed = build_validation_retry_hint(
        "development_result",
        failure_b,
        prior_hint=repeat,
        prior_fingerprint=fingerprint_a,
    )

    assert first.count(PREMISE_CHECK_LINE) == 0
    assert repeat.count(PREMISE_CHECK_LINE) == 1
    assert changed.count(PREMISE_CHECK_LINE) == 0


def test_validation_retry_history_keeps_the_rendered_trailer_out_of_the_attempt_bodies() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]
    fingerprint_a = validation_retry_fingerprint("development_result", failure_a)

    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=first,
        prior_fingerprint=fingerprint_a,
    )

    assert repeat.count(build_validation_retry_footer()) == 1
    assert repeat.endswith(PREMISE_CHECK_LINE)


def test_generic_technical_retry_prompt_renders_premise_line_only_on_identical_repeat(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Original task prompt\n", encoding="utf-8")

    first = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_file),
        reason="AgentTimeout",
        detail="same detail",
        context_lines=["the agent produced no output"],
        drain="development",
    )
    repeat = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_file),
        reason="AgentTimeout",
        detail="same detail",
        context_lines=["the agent produced no output"],
        drain="development",
    )
    changed = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_file),
        reason="AgentTimeout",
        detail="a different detail",
        context_lines=["the agent produced no output"],
        drain="development",
    )

    assert PREMISE_CHECK_LINE not in Path(first).read_text(encoding="utf-8")
    assert Path(repeat).read_text(encoding="utf-8").count(PREMISE_CHECK_LINE) == 1
    assert PREMISE_CHECK_LINE not in Path(changed).read_text(encoding="utf-8")


def test_generic_technical_retry_prompt_without_drain_renders_premise_line_only_on_identical_repeat(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Original task prompt\n", encoding="utf-8")

    first = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_file),
        reason="AgentTimeout",
        detail="same detail",
        context_lines=["the agent produced no output"],
    )
    repeat = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_file),
        reason="AgentTimeout",
        detail="same detail",
        context_lines=["the agent produced no output"],
    )
    changed = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_file),
        reason="AgentTimeout",
        detail="a different detail",
        context_lines=["the agent produced no output"],
    )

    assert PREMISE_CHECK_LINE not in Path(first).read_text(encoding="utf-8")
    assert Path(repeat).read_text(encoding="utf-8").count(PREMISE_CHECK_LINE) == 1
    assert PREMISE_CHECK_LINE not in Path(changed).read_text(encoding="utf-8")


def test_prompt_readers_stay_read_only() -> None:
    assert hasattr(materialize, "read_retry_hint")
    assert not hasattr(materialize, "read_and_clear_retry_hint")
    assert hasattr(commit_review, "_read_retry_hint")
    assert not hasattr(commit_review, "_read_and_clear_retry_hint")
