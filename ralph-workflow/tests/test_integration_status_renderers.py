from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.status_bar import StatusBarModel, render_status_bar
from ralph.pipeline import build_integration_status
from ralph.recovery.agent_selection import (
    agent_availability,
    format_selection_evidence,
    select_preferred_agent,
)


def test_defect_19_regression_status_names_operator_path_command_and_agent_limit() -> None:
    status = build_integration_status(
        cause="OUT_OF_REACH",
        paths_with_reasons=(("vendor/Ralph-Workflow", "gitlink conflict"),),
        command="git add -- vendor/Ralph-Workflow",
        agents_can_finish=False,
    )

    assert "OUT_OF_REACH" in status.summary
    assert "vendor/Ralph-Workflow" in status.summary
    assert "git add -- vendor/Ralph-Workflow" in status.summary
    assert "agents cannot finish this" in status.summary


def test_defect_20_regression_declared_decisions_keep_a_cause_per_file() -> None:
    status = build_integration_status(
        cause="DECLARED_DECISION",
        paths_with_reasons=(("deleted.txt", "delete/modify"), ("asset.bin", "binary")),
        command="",
        agents_can_finish=True,
    )

    assert status.paths_with_reasons == (
        ("deleted.txt", "delete/modify"),
        ("asset.bin", "binary"),
    )


def test_defect_21_regression_lower_priority_names_selected_chain_predecessor() -> None:
    selection = select_preferred_agent(
        (
            agent_availability("claude", True, 0, False),
            agent_availability("opencode", True, 0, False),
        )
    )

    evidence = format_selection_evidence("development", selection)

    assert (
        "opencode: lower_priority (listed after selected agent claude "
        "in configured chain order)" in evidence
    )
    assert "capability" not in evidence


def test_renderer_parity_regression_status_bar_uses_single_status_summary() -> None:
    status = build_integration_status(
        cause="OUT_OF_REACH",
        paths_with_reasons=(("vendor/Ralph-Workflow", "gitlink conflict"),),
        command="git add -- vendor/Ralph-Workflow",
        agents_can_finish=False,
    )
    output = StringIO()
    context = make_display_context(console=Console(file=output, width=72), env={})
    rendered = render_status_bar(
        StatusBarModel(
            workspace_root=str(Path("/workspace")),
            phase_label="Integration",
            phase_style="theme.status.error",
            integration_status=status,
        ),
        context,
    ).plain

    assert "OUT_OF_REACH" in rendered
    assert "vendor/Ralph-Workflow" in rendered
    assert "git add -- vendor/Ralph-Workflow" in rendered
