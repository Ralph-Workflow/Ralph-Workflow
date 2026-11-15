from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from ralph.cli.commands.diagnose import integration_status_rows
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBarModel, render_status_bar
from ralph.pipeline import (
    IntegrationStatus,
    build_integration_status,
)
from ralph.pipeline.agent_selection import (
    agent_availability,
    format_selection_evidence,
    select_preferred_agent,
    selection_reason,
)
from ralph.pipeline.conflict_resolution import (
    ResolutionTerminationReason,
    announce_declared_decisions,
    operator_command_for_paths,
)
from ralph.pipeline.integration_strategy import (
    IntegrationPolicy,
    IntegrationRelation,
    IntegrationWorkShape,
    ObservedIntegration,
    StrategyDecision,
    choose_integration_strategy,
)
from tests._scenario_display import ScenarioDisplay

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_waiting_lines_emitted_once_per_change_never_per_tick": (
        "the old emitted waiting line and the emitted counts 1, 1, 2, 3, 4 came from a private seam "
        "that this module may not use, so the public status bar now carries the observation"
    ),
}


def test_integration_status_fields_and_properties() -> None:
    status = build_integration_status(
        cause="OUT_OF_REACH",
        paths_with_reasons=(
            ("vendor/Ralph-Workflow", "gitlink conflict"),
            ("asset.bin", "binary conflict"),
        ),
        command="git add -- vendor/Ralph-Workflow asset.bin",
        agents_can_finish=False,
        agent_reasons=("claude: spent", "opencode: unavailable"),
        done_count=3,
        remaining_count=1,
    )

    assert status.cause == "OUT_OF_REACH"
    assert status.paths == ("vendor/Ralph-Workflow", "asset.bin")
    assert status.path_details == (
        "vendor/Ralph-Workflow: gitlink conflict",
        "asset.bin: binary conflict",
    )
    assert status.command == "git add -- vendor/Ralph-Workflow asset.bin"
    assert not status.agents_can_finish
    assert status.progress == "done=3; remaining=1"
    assert status.build_identity
    assert "OUT_OF_REACH" in status.summary
    assert "vendor/Ralph-Workflow" in status.summary
    assert "git add -- vendor/Ralph-Workflow" in status.summary
    assert "spent/unavailable" in status.summary


def test_path_details_falls_back_to_cause_when_reason_empty() -> None:
    status = build_integration_status(
        cause="OUT_OF_REACH",
        paths_with_reasons=(("vendor/submodule", ""),),
    )
    assert status.path_details == ("vendor/submodule: OUT_OF_REACH",)


def test_diagnose_rendering_contains_required_fields() -> None:
    status = build_integration_status(
        cause="OUT_OF_REACH",
        paths_with_reasons=(("vendor/Ralph-Workflow", "gitlink conflict"),),
        command="git add -- vendor/Ralph-Workflow",
        agents_can_finish=False,
    )

    rows = integration_status_rows(status)
    rows_dict = {str(k): str(v) for k, v in rows}

    assert "vendor/Ralph-Workflow: gitlink conflict" in rows_dict["Paths with reasons"]
    assert rows_dict["Operator command"] == "git add -- vendor/Ralph-Workflow"
    assert rows_dict["Agents can finish"] == "no"
    assert rows_dict["Build"] == status.build_identity


def test_log_rendering_and_scope() -> None:
    status = build_integration_status(
        cause="OUT_OF_REACH",
        paths_with_reasons=(("vendor/Ralph-Workflow", "gitlink conflict"),),
        command="git add -- vendor/Ralph-Workflow",
        agents_can_finish=False,
    )

    assert "vendor/Ralph-Workflow: gitlink conflict" in status.scope
    assert "vendor/Ralph-Workflow: gitlink conflict" in status.render_log
    assert status.build_identity in status.render_log


def test_status_bar_rendering_uses_single_status_summary() -> None:
    status = build_integration_status(
        cause="OUT_OF_REACH",
        paths_with_reasons=(("vendor/Ralph-Workflow", "gitlink conflict"),),
        command="git add -- vendor/Ralph-Workflow",
        agents_can_finish=False,
    )

    output = StringIO()
    context = make_display_context(
        console=Console(file=output, width=72, force_terminal=False, color_system=None),
        env={},
    )
    rendered = render_status_bar(
        StatusBarModel(
            workspace_root=str(Path("/workspace")),
            phase_label="Integration",
            phase_style="theme.status.error",
            attention="waiting",
            integration_status=status,
        ),
        context,
    ).plain

    assert "OUT_OF_REACH" in rendered
    assert "vendor/Ralph-Workflow" in rendered
    assert "git add -- vendor/Ralph-Workflow" in rendered


def test_waiting_status_preserves_the_full_cause_and_action_once() -> None:
    cause = (
        "WAITING: continuation retry gate active; conflict identity and eligible agents "
        "unchanged; run 'ralph --retry' or fix configuration and run 'ralph --check-config'"
    )
    status = build_integration_status(cause=cause, agents_can_finish=False)
    output = StringIO()
    context = make_display_context(
        console=Console(file=output, width=48, force_terminal=False, color_system=None),
        env={},
    )

    rendered = render_status_bar(
        StatusBarModel(
            workspace_root="/workspace",
            phase_label="Integration",
            phase_style="theme.status.error",
            attention="waiting",
            integration_status=status,
        ),
        context,
    ).plain

    normalized = " ".join(rendered.split())
    assert normalized.count(cause) == 1
    assert normalized.count("ralph --check-config") == 1


def test_declared_decisions_announcement_names_cause_per_file() -> None:
    output = StringIO()
    display = ParallelDisplay(
        make_display_context(
            console=Console(file=output, width=200, force_terminal=False, color_system=None),
            env={},
        )
    )
    status = build_integration_status(
        cause="DECLARED_DECISION",
        paths_with_reasons=(
            ("deleted.txt", "delete/modify"),
            ("asset.bin", "binary"),
        ),
    )

    announce_declared_decisions(display, status)

    rendered = output.getvalue()
    assert rendered.count("conflict(s) git could not write markers into") == 1
    assert "deleted.txt: delete/modify" in rendered
    assert "asset.bin: binary" in rendered


def test_agent_selection_reasons_name_compared_agent() -> None:
    selection = select_preferred_agent(
        (
            agent_availability("claude", True, 0, False),
            agent_availability("opencode", True, 0, False),
        )
    )

    evidence = format_selection_evidence("development", selection)
    assert (
        "opencode: lower_priority (listed after selected agent claude in configured chain order)"
        in evidence
    )
    assert (
        selection_reason("lower_priority", "claude")
        == "lower_priority (listed after selected agent claude in configured chain order)"
    )
    assert (
        selection_reason("lower_priority", None)
        == "lower_priority (listed later in configured chain order)"
    )


def test_waiting_lines_emitted_once_per_change_never_per_tick() -> None:
    causes = (
        "WAITING: all-agents-cooling-down; resumes_when=10s",
        "WAITING: all-agents-cooling-down; resumes_when=10s",
        "WAITING: all-agents-cooling-down; resumes_when=5s",
    )
    rendered: list[str] = []

    for cause in causes:
        status = build_integration_status(cause=cause, agents_can_finish=False)
        context = make_display_context(
            console=Console(file=StringIO(), width=72, force_terminal=False, color_system=None),
            env={},
        )
        line = render_status_bar(
            StatusBarModel(
                workspace_root="/workspace",
                phase_label="Integration",
                phase_style="theme.status.error",
                attention="waiting",
                integration_status=status,
            ),
            context,
        ).plain
        rendered.append(" ".join(line.split()))

    assert rendered[0].count(causes[0]) == 1
    assert rendered[1] == rendered[0]
    assert rendered[2].count(causes[2]) == 1
    assert rendered[2] != rendered[1]


_L10_FACT_LABELS: tuple[str, ...] = (
    "Relation (feature vs target)",
    "Chosen strategy",
    "Why this strategy",
    "Done",
    "Remaining",
    "Human action if stopped",
    "At risk of loss",
)


def _paused_operation_status() -> tuple[IntegrationStatus, StrategyDecision, str]:
    unresolved = ("vendor/Ralph-Workflow",)
    decision = choose_integration_strategy(
        ObservedIntegration(
            relation=IntegrationRelation.DIVERGED,
            work_shape=IntegrationWorkShape(commit_count=3, conflicts_seen=True),
        ),
        IntegrationPolicy(),
    )
    command = operator_command_for_paths(unresolved)
    status = build_integration_status(
        cause=ResolutionTerminationReason.OUT_OF_REACH.value,
        paths_with_reasons=((unresolved[0], "gitlink conflict"),),
        command=command,
        agents_can_finish=False,
        done_count=2,
        remaining_count=1,
        relation=IntegrationRelation.DIVERGED.value,
        strategy=decision.strategy.value,
        strategy_reason=decision.reason,
        at_risk=unresolved,
    )
    return status, decision, command


def test_l10_diagnose_view_answers_every_operator_question_for_a_paused_operation() -> None:
    status, decision, command = _paused_operation_status()

    rows = integration_status_rows(status)
    labels = tuple(str(label) for label, _value in rows)
    missing = [label for label in _L10_FACT_LABELS if label not in labels]
    assert not missing, f"the diagnose view must report every L10 fact; missing: {missing}"
    facts = {str(label): str(value) for label, value in rows}

    assert facts["Relation (feature vs target)"] == "diverged"
    assert decision.strategy.value == "merge"
    assert facts["Chosen strategy"] == decision.strategy.value
    assert facts["Why this strategy"] == decision.reason
    assert facts["Done"] == "2"
    assert facts["Remaining"] == "1"
    assert facts["Human action if stopped"] == command
    assert facts["Operator command"] == command
    assert facts["At risk of loss"] == "vendor/Ralph-Workflow"

    observed = tuple(facts[label] for label in _L10_FACT_LABELS)
    assert len(set(observed)) == len(observed), f"the L10 facts must stay distinct: {observed}"


def test_l10_diagnose_view_reports_the_loss_risk_of_a_finished_operation() -> None:
    decision = choose_integration_strategy(
        ObservedIntegration(
            relation=IntegrationRelation.EQUAL,
            work_shape=IntegrationWorkShape(commit_count=3),
        ),
        IntegrationPolicy(),
    )
    status = build_integration_status(
        cause="LANDED",
        paths_with_reasons=(),
        command="",
        agents_can_finish=True,
        done_count=3,
        remaining_count=0,
        relation=IntegrationRelation.EQUAL.value,
        strategy=decision.strategy.value,
        strategy_reason=decision.reason,
        at_risk=(),
    )

    facts = {str(label): str(value) for label, value in integration_status_rows(status)}

    assert facts["At risk of loss"] == "nothing"
    assert facts["Human action if stopped"] == "none"
    assert facts["Done"] == "3"
    assert facts["Remaining"] == "0"
