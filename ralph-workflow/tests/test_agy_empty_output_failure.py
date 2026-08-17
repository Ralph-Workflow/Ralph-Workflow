"""Regression coverage for actionable AGY clean-exit failures."""

from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from ralph.agents import _agy_upstream_diagnostic as agy_diag
from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.invoke import AgentInvocationError, CompletionCheckOptions, check_process_result
from ralph.config.enums import AgentTransport


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("RESOURCE_EXHAUSTED (code 429)", "quota is exhausted"),
        ("Failed to get OAuth token", "authentication failed"),
        ("Failed to resolve model flag: model x is not recognized", "model is unavailable"),
        (
            'jetski: no output produced — a tool required the "command" permission '
            "that headless mode cannot prompt for, so it was auto-denied. "
            "Alternatively, re-run with --dangerously-skip-permissions ...",
            "auto-denied",
        ),
        (
            "Print mode: timed out after 7 polls (printed=3)",
            "print-mode polling timed out",
        ),
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


# --- Plan S-5: verbatim CLI-log substring pins -------------------------------


@pytest.mark.parametrize(
    ("pattern", "documented_literal"),
    [
        (agy_diag._QUOTA_PATTERN, "RESOURCE_EXHAUSTED"),
        (agy_diag._QUOTA_PATTERN, "quota exhausted"),
        (agy_diag._AUTH_PATTERN, "failed to get OAuth token"),
        (agy_diag._PERMISSION_AUTO_DENY_PATTERN, "auto-denied"),
        (agy_diag._MODEL_PATTERN, "not recognized"),
        (agy_diag._MODEL_PATTERN, "failed to resolve model"),
        (agy_diag._PRINT_MODE_TIMEOUT_PATTERN, r"Print mode: timed out after \d+ polls"),
    ],
)
def test_documented_cli_log_substrings_stay_in_diagnostic_patterns(
    pattern: re.Pattern[str],
    documented_literal: str,
) -> None:
    """Plan S-5: every documented trigger literal is pinned inside its pattern.

    Editing a diagnostic pattern and silently dropping one of the
    documented AGY CLI-log trigger substrings must fail this pin, not
    reach production as an unrecognised-cause regression.
    """
    assert documented_literal in pattern.pattern


def test_agy_default_cli_log_path_is_the_documented_one() -> None:
    """Plan S-5: the fallback log path is ``~/.gemini/antigravity-cli/cli.log``.

    Pinned via the path tail rather than a live ``Path.home()`` comparison:
    the conftest autouse home fixture redirects HOME per test, while the
    module constant is computed once at import time under the real home.
    """
    assert agy_diag._AGY_CLI_LOG_PATH.parts[-3:] == (".gemini", "antigravity-cli", "cli.log")


def test_unrecognised_agy_failure_fails_closed_with_useful_diagnostic(
    tmp_path: Path,
) -> None:
    """Plan S-5: an unrecognised log tail surfaces the canonical diagnostic.

    A brand-new upstream failure mode the patterns do not know must still
    fail closed — never silently pass — and the raised error must name the
    agent and the missing completion evidence so the operator can act.
    """
    cli_log = tmp_path / "cli.log"
    cli_log.write_text("some brand-new upstream failure mode", encoding="utf-8")

    assert agy_diag.agy_empty_output_reason([], cli_log) is None

    with pytest.raises(AgentInvocationError, match="completion evidence") as excinfo:
        check_process_result(
            types.SimpleNamespace(returncode=0),
            "agy/unknown-model",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy_for_transport(AgentTransport.AGY),
                workspace_path=tmp_path,
                agy_cli_log_path=cli_log,
            ),
        )
    rendered = str(excinfo.value)
    assert "completion sentinel" in rendered
    assert "agy/unknown-model" in rendered
