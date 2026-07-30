"""Regression coverage for actionable AGY clean-exit failures."""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.invoke import AgentInvocationError, CompletionCheckOptions, check_process_result
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("RESOURCE_EXHAUSTED (code 429)", "quota is exhausted"),
        ("Failed to get OAuth token", "authentication failed"),
        ("Failed to resolve model flag: model x is not recognized", "model is unavailable"),
    ],
)
def test_agy_empty_output_regression_names_actionable_upstream_cause(
    tmp_path: Path,
    evidence: str,
    expected: str,
) -> None:
    """Plan S-3: AGY rc=0 without completion evidence names captured root cause."""
    cli_log = tmp_path / "cli.log"
    cli_log.write_text(evidence, encoding="utf-8")
    with pytest.raises(AgentInvocationError, match=expected):
        check_process_result(
            types.SimpleNamespace(returncode=0),
            "agy/gemini-3.6-flash-low",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy_for_transport(AgentTransport.AGY),
                workspace_path=tmp_path,
                agy_cli_log_path=cli_log,
            ),
        )


def test_agy_empty_output_regression_without_cause_still_fails(tmp_path: Path) -> None:
    """Plan S-3: a clean AGY exit with no evidence never becomes success."""
    cli_log = tmp_path / "cli.log"
    cli_log.write_text("", encoding="utf-8")
    with pytest.raises(AgentInvocationError, match="completion evidence"):
        check_process_result(
            types.SimpleNamespace(returncode=0),
            "agy/gemini-3.6-flash-low",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy_for_transport(AgentTransport.AGY),
                workspace_path=tmp_path,
                agy_cli_log_path=cli_log,
            ),
        )
