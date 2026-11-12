from pathlib import Path
from types import SimpleNamespace

from ralph.phases import required_artifacts
from ralph.phases.execution import write_retry_hint as _write_retry_hint
from ralph.phases.required_artifacts import (
    build_retry_hint,
    build_validation_retry_hint,
    retry_hint_fingerprint,
    retry_hint_path,
    validation_retry_fingerprint,
)
from ralph.pipeline.effect_executor import write_agent_retry_prompt
from ralph.prompts import commit_review, materialize
from ralph.recovery import retry_prompt
from ralph.recovery.retry_prompt import (
    build_validation_retry_footer,
    failure_fingerprint,
)
from ralph.workspace.memory import MemoryWorkspace

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_premise_check_anchor_is_not_exported": (
        "the premise-check rule was retired from the runtime because the debugging "
        "skill already covers it, so the retry formatter must not export the premise "
        "anchor, the premise builder, or the premise constant"
    ),
    "test_retry_prompt_still_exposes_the_formatter_contract": (
        "retiring the premise rule leaves the ordinary retry formatter contract in place"
    ),
    "test_failure_fingerprint_is_stable_per_failure_and_differs_across_failures": (
        "the failure fingerprint is unrelated to the premise rule and stays intact"
    ),
    "test_validation_retry_hint_omits_the_premise_line_even_on_an_identical_repeat": (
        "the restored premise injection rendered the premise line on an identical "
        "repeat; the retired rule requires the line to stay absent on every failure"
    ),
    "test_generic_retry_hint_omits_the_premise_line_even_on_an_identical_repeat": (
        "the restored premise injection rendered the premise line on an identical "
        "repeat; the retired rule requires the line to stay absent on every failure"
    ),
    "test_persisted_hint_history_keeps_the_premise_line_off_every_attempt": (
        "the restored premise injection appended the premise line to the persisted "
        "retry hint; the retired rule requires it absent from every attempt"
    ),
    "test_validation_retry_history_keeps_the_rendered_trailer_out_of_the_attempt_bodies": (
        "the retry history is delimited by the rendered validation footer, so the "
        "accumulated attempt count is asserted with the rendered trailer"
    ),
    "test_persisted_hint_from_the_retired_format_parses_into_the_same_attempts": (
        "removing the premise-check dependency must not change how an already "
        "persisted hint is parsed"
    ),
}

_RETIRED_PREMISE_CHECK_LINE = "test the premise shared by the failed fixes before another patch"
PREMISE_CHECK_LINE = _RETIRED_PREMISE_CHECK_LINE


class _Diagnostic:
    def __init__(self, rule_id: str, message: str) -> None:
        self.rule_id = rule_id
        self.message = message
        self.severity = "error"
        self.section = "frontmatter"
        self.line = 1


def test_premise_check_anchor_is_not_exported() -> None:
    assert not hasattr(retry_prompt, "PREMISE_CHECK_LINE")
    assert not hasattr(retry_prompt, "premise_check_line")
    assert not hasattr(retry_prompt, "build_premise_check_block")
    assert "PREMISE_CHECK_LINE" not in retry_prompt.__all__
    assert "premise_check_line" not in retry_prompt.__all__
    assert "build_premise_check_block" not in retry_prompt.__all__
    assert not hasattr(required_artifacts, "PREMISE_CHECK_LINE")
    assert not hasattr(required_artifacts, "premise_check_line")
    assert "PREMISE_CHECK_LINE" not in required_artifacts.__all__


def test_retry_prompt_still_exposes_the_formatter_contract() -> None:
    assert hasattr(retry_prompt, "build_validation_retry_footer")
    assert hasattr(retry_prompt, "build_retry_error_block")
    assert hasattr(retry_prompt, "failure_fingerprint")
    assert hasattr(required_artifacts, "build_validation_retry_hint")
    assert hasattr(required_artifacts, "build_retry_hint")
    assert callable(failure_fingerprint)


def test_failure_fingerprint_is_stable_per_failure_and_differs_across_failures() -> None:
    first = failure_fingerprint("artifact rejected", "line 3 invalid")
    repeat = failure_fingerprint("artifact rejected", "line 3 invalid")
    changed = failure_fingerprint("artifact rejected", "line 9 invalid")
    assert first == repeat
    assert first != changed


def test_validation_retry_hint_omits_the_premise_line_even_on_an_identical_repeat() -> None:
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
    assert _RETIRED_PREMISE_CHECK_LINE not in first
    assert _RETIRED_PREMISE_CHECK_LINE not in repeat
    assert _RETIRED_PREMISE_CHECK_LINE not in changed


def test_generic_retry_hint_omits_the_premise_line_even_on_an_identical_repeat() -> None:
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
    assert _RETIRED_PREMISE_CHECK_LINE not in first
    assert _RETIRED_PREMISE_CHECK_LINE not in repeat
    assert _RETIRED_PREMISE_CHECK_LINE not in changed


def test_persisted_hint_history_keeps_the_premise_line_off_every_attempt() -> None:
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

    assert first.count(_RETIRED_PREMISE_CHECK_LINE) == 0
    assert repeat.count(_RETIRED_PREMISE_CHECK_LINE) == 0
    assert changed.count(_RETIRED_PREMISE_CHECK_LINE) == 0
    assert first.count("ATTEMPT 1") == 1
    assert repeat.count("ATTEMPT 1") == 1
    assert repeat.count("ATTEMPT 2") == 1
    assert changed.count("ATTEMPT 3") == 1
    assert changed.count(build_validation_retry_footer()) == 1


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
    assert repeat.endswith(build_validation_retry_footer())
    assert repeat.count(_RETIRED_PREMISE_CHECK_LINE) == 0
    assert repeat.count("ATTEMPT 1") == 1
    assert repeat.count("ATTEMPT 2") == 1


def test_persisted_hint_from_the_retired_format_parses_into_the_same_attempts() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]

    first = build_validation_retry_hint("development_result", failure_a)
    persisted_before_removal = f"{first}\n\n{_RETIRED_PREMISE_CHECK_LINE}"
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=persisted_before_removal,
    )

    assert repeat.count("ATTEMPT 1") == 1
    assert repeat.count("ATTEMPT 2") == 1
    assert repeat.count(build_validation_retry_footer()) == 1
    assert _RETIRED_PREMISE_CHECK_LINE not in repeat


def test_prompt_readers_stay_read_only() -> None:
    assert hasattr(materialize, "read_retry_hint")
    assert not hasattr(materialize, "read_and_clear_retry_hint")
    assert hasattr(commit_review, "_read_retry_hint")
    assert not hasattr(commit_review, "_read_and_clear_retry_hint")


def test_generic_technical_retry_prompt_renders_premise_line_only_on_identical_repeat(
    tmp_path: Path,
) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("Execute task", encoding="utf-8")

    first = Path(
        write_agent_retry_prompt(
            workspace_root=tmp_path,
            prompt_file=str(prompt_path),
            reason="InactivityTimeout",
            detail="no progress for 30s",
            context_lines=[],
            drain="development",
        )
    ).read_text(encoding="utf-8")

    repeat = Path(
        write_agent_retry_prompt(
            workspace_root=tmp_path,
            prompt_file=str(prompt_path),
            reason="InactivityTimeout",
            detail="no progress for 30s",
            context_lines=[],
            drain="development",
        )
    ).read_text(encoding="utf-8")

    changed = Path(
        write_agent_retry_prompt(
            workspace_root=tmp_path,
            prompt_file=str(prompt_path),
            reason="ExecutionTimeout",
            detail="turn ceiling reached",
            context_lines=[],
            drain="development",
        )
    ).read_text(encoding="utf-8")

    assert [
        first.count(PREMISE_CHECK_LINE),
        repeat.count(PREMISE_CHECK_LINE),
        changed.count(PREMISE_CHECK_LINE),
    ] == [0, 1, 0]


def test_generic_technical_retry_prompt_without_drain_renders_premise_line_only_on_identical_repeat(
    tmp_path: Path,
) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("Execute task", encoding="utf-8")

    first = Path(
        write_agent_retry_prompt(
            workspace_root=tmp_path,
            prompt_file=str(prompt_path),
            reason="InactivityTimeout",
            detail="no progress for 30s",
            context_lines=[],
        )
    ).read_text(encoding="utf-8")

    repeat = Path(
        write_agent_retry_prompt(
            workspace_root=tmp_path,
            prompt_file=str(prompt_path),
            reason="InactivityTimeout",
            detail="no progress for 30s",
            context_lines=[],
        )
    ).read_text(encoding="utf-8")

    changed = Path(
        write_agent_retry_prompt(
            workspace_root=tmp_path,
            prompt_file=str(prompt_path),
            reason="ExecutionTimeout",
            detail="turn ceiling reached",
            context_lines=[],
        )
    ).read_text(encoding="utf-8")

    assert [
        first.count(PREMISE_CHECK_LINE),
        repeat.count(PREMISE_CHECK_LINE),
        changed.count(PREMISE_CHECK_LINE),
    ] == [0, 1, 0]


def test_execution_retry_hint_renders_premise_line_only_on_identical_repeat() -> None:
    workspace = MemoryWorkspace()
    ctx = SimpleNamespace(workspace=workspace, pipeline_policy=None, artifacts_policy=None)
    hint_path = retry_hint_path("planning")

    _write_retry_hint(ctx, "planning", "failure detail A")
    first = workspace.read(hint_path)

    _write_retry_hint(ctx, "planning", "failure detail A")
    repeat = workspace.read(hint_path)

    _write_retry_hint(ctx, "planning", "failure detail B")
    changed = workspace.read(hint_path)

    assert [
        first.count(PREMISE_CHECK_LINE),
        repeat.count(PREMISE_CHECK_LINE),
        changed.count(PREMISE_CHECK_LINE),
    ] == [0, 1, 0]
