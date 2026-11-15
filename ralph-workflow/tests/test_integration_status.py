from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from ralph.cli.commands.diagnose import integration_status_rows
from ralph.display.context import make_display_context
from ralph.display.status_bar import StatusBarModel, render_status_bar
from ralph.pipeline._integration_status import build_integration_status
from ralph.pipeline.agent_selection import (
    agent_availability,
    format_selection_evidence,
    select_preferred_agent,
    selection_reason,
)
from ralph.pipeline.conflict_resolution._driver_events import (
    announce_declared_decisions,
    emit_attempt_outcome,
    reset_driver_events_state,
)
from ralph.pipeline.resolution_outcome import (
    NextStrategyOutcome,
    WaitingOutcome,
)


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


def test_declared_decisions_announcement_names_cause_per_file() -> None:
    emitted: list[str] = []
    status = build_integration_status(
        cause="DECLARED_DECISION",
        paths_with_reasons=(
            ("deleted.txt", "delete/modify"),
            ("asset.bin", "binary"),
        ),
    )

    announce_declared_decisions(
        None,
        status,
    )
    # With monkeypatched _emit:
    import ralph.pipeline.conflict_resolution._driver_events as driver_events

    original_emit = driver_events._emit
    try:
        driver_events._emit = lambda _d, line: emitted.append(line)
        announce_declared_decisions(None, status)
    finally:
        driver_events._emit = original_emit

    assert len(emitted) == 1
    assert "deleted.txt: delete/modify" in emitted[0]
    assert "asset.bin: binary" in emitted[0]


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
    reset_driver_events_state()
    emitted: list[str] = []

    import ralph.pipeline.conflict_resolution._driver_events as driver_events

    original_emit = driver_events._emit
    try:
        driver_events._emit = lambda _d, line: emitted.append(line)

        waiting1 = WaitingOutcome(external_condition="all-agents-cooling-down", resumes_when="10s")
        waiting2 = WaitingOutcome(external_condition="all-agents-cooling-down", resumes_when="10s")
        waiting_changed = WaitingOutcome(
            external_condition="all-agents-cooling-down", resumes_when="5s"
        )

        # Tick 1: first waiting outcome
        emit_attempt_outcome(None, waiting1)
        assert len(emitted) == 1
        assert emitted[0] == "waiting: all-agents-cooling-down; resumes_when=10s"

        # Tick 2: identical waiting outcome (must NOT emit duplicate line)
        emit_attempt_outcome(None, waiting2)
        assert len(emitted) == 1

        # Tick 3: changed waiting condition (must emit once)
        emit_attempt_outcome(None, waiting_changed)
        assert len(emitted) == 2
        assert emitted[1] == "waiting: all-agents-cooling-down; resumes_when=5s"

        # Tick 4: non-waiting outcome resets state
        emit_attempt_outcome(None, NextStrategyOutcome(strategy="completion_in_place"))
        assert len(emitted) == 3

        # Tick 5: waiting outcome again after strategy change (must emit)
        emit_attempt_outcome(None, waiting1)
        assert len(emitted) == 4
    finally:
        driver_events._emit = original_emit
        reset_driver_events_state()
