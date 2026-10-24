"""The ``design_capture_command`` fact is judged by ONE rule set, at authoring and at runtime.

Regression: a managed repo's design-system policy carried prose after the
command (``make visual-capture (the bounded Tier 1 ...; ...`...`)``). Policy
validation accepted it, so the visual pipeline rejected it on every cycle and
silently skipped visual review -- and the rejection was then chained onto an
unrelated agent-run crash, hiding the real failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline import runner
from ralph.project_policy import _content_checks
from ralph.visual.policy_facts import (
    DESIGN_SYSTEM_POLICY_RELPATH,
    capture_command_problem,
    parse_policy_facts,
    validate_capture_command,
)

_PROSE = (
    "make visual-capture (the bounded Tier 1 capture command; the make target "
    "forwards URL=, OUT= to `script/capture_visual_with_server.ts`)"
)
_DESIGN_FILE = Path(DESIGN_SYSTEM_POLICY_RELPATH).name

_VALID_FACTS = (
    "RALPH-FACT: design_target: /\n"
    "RALPH-FACT: viewports: 375x812, 1440x900\n"
    "RALPH-FACT: themes: light, dark\n"
    "RALPH-FACT: states: default, empty, loading, error, overflow\n"
)


def _policy(command: str) -> str:
    return f"# Design system\n\nRALPH-FACT: design_capture_command: {command}\n{_VALID_FACTS}"


def test_prose_annotation_is_reported_with_the_runtime_reason() -> None:
    problem = capture_command_problem(_policy(_PROSE))
    assert problem is not None
    assert "shell metacharacters" in problem


@pytest.mark.parametrize(
    "command",
    ["make visual-capture", "npm exec -- tsx script/capture.ts", "node script/walker.ts --all"],
)
def test_bare_commands_are_accepted(command: str) -> None:
    assert capture_command_problem(_policy(command)) is None
    assert parse_policy_facts(_policy(command)).design_capture_command == command


@pytest.mark.parametrize(
    "command",
    [
        "make",
        "make ../escape",
        "make --out=../x",
        "make a; rm -rf x",
        "make a | tee b",
        "make $(whoami)",
    ],
)
def test_unusable_commands_are_reported(command: str) -> None:
    assert capture_command_problem(_policy(command)) is not None


def test_absent_or_blank_fact_is_not_this_checks_problem() -> None:
    """Presence is the required-fact check's job, not this one's."""
    assert capture_command_problem("# Design system\n\nNo facts here.\n") is None
    assert capture_command_problem(_policy(" ")) is None


@pytest.mark.parametrize(
    "command", ["make visual-capture OUT=out/x.png", "make a ..b", "make v1.2..3"]
)
def test_dots_that_are_not_a_traversal_segment_are_accepted(command: str) -> None:
    assert capture_command_problem(_policy(command)) is None


def test_first_occurrence_wins_exactly_like_the_runtime_parser() -> None:
    text = _policy("make visual-capture") + "RALPH-FACT: design_capture_command: bad; thing\n"
    assert capture_command_problem(text) is None
    assert parse_policy_facts(text).design_capture_command == "make visual-capture"


def test_authoring_and_runtime_agree_on_every_case() -> None:
    for command in ("make visual-capture", _PROSE, "make", "a;b c", "make ../x"):
        runtime_rejects = False
        try:
            validate_capture_command(command)
        except ValueError:
            runtime_rejects = True
        assert (capture_command_problem(_policy(command)) is not None) is runtime_rejects


def test_validator_emits_a_finding_for_the_design_system_policy() -> None:
    findings = _content_checks._check_capture_command_fact(
        _policy(_PROSE), f"docs/ralph-workflow-policy/{_DESIGN_FILE}", _DESIGN_FILE
    )
    assert len(findings) == 1
    assert "bare capture command" in findings[0].required_outcome
    assert "design_capture_command" in findings[0].requirement_id


def test_validator_is_silent_for_a_valid_command_and_for_other_policies() -> None:
    path = f"docs/ralph-workflow-policy/{_DESIGN_FILE}"
    assert (
        _content_checks._check_capture_command_fact(
            _policy("make visual-capture"), path, _DESIGN_FILE
        )
        == []
    )
    # Other policies reference the command descriptively; only the design-system
    # policy is parsed by the visual pipeline.
    assert (
        _content_checks._check_capture_command_fact(
            _policy(_PROSE), "docs/ralph-workflow-policy/ux-policy.md", "ux-policy.md"
        )
        == []
    )


def test_unusable_policy_yields_a_reason_not_an_exception(tmp_path: Path) -> None:
    policy = tmp_path / DESIGN_SYSTEM_POLICY_RELPATH
    policy.parent.mkdir(parents=True)
    policy.write_text(_policy(_PROSE), encoding="utf-8")

    plan = runner._resolve_policy_capture_plan(tmp_path)

    assert isinstance(plan, str)
    assert "shell metacharacters" in plan


def test_missing_policy_file_yields_a_reason(tmp_path: Path) -> None:
    plan = runner._resolve_policy_capture_plan(tmp_path)
    assert isinstance(plan, str)


def test_usable_policy_yields_a_capture_plan(tmp_path: Path) -> None:
    policy = tmp_path / DESIGN_SYSTEM_POLICY_RELPATH
    policy.parent.mkdir(parents=True)
    policy.write_text(_policy("make visual-capture"), encoding="utf-8")

    plan = runner._resolve_policy_capture_plan(tmp_path)

    assert not isinstance(plan, str)
    facts, _request, matrix_key = plan
    assert facts.design_capture_command == "make visual-capture"
    assert matrix_key


def test_agent_failure_is_not_chained_onto_the_policy_problem(tmp_path: Path) -> None:
    policy = tmp_path / DESIGN_SYSTEM_POLICY_RELPATH
    policy.parent.mkdir(parents=True)
    policy.write_text(_policy(_PROSE), encoding="utf-8")

    def failing_agent() -> object:
        raise RuntimeError("agent run failed")

    with pytest.raises(RuntimeError) as excinfo:
        runner._run_policy_declared_visual_capture(
            workspace_root=tmp_path,
            run_id="run-1",
            cycle_id="cycle-1",
            invoke_agent=failing_agent,  # type: ignore[arg-type]  # reason: minimal fake agent
            env_getter=lambda name: "secret" if name == "RALPH_BROKER_SECRET" else None,
        )

    assert excinfo.value.__context__ is None
