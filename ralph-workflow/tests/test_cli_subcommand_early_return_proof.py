"""Proof that ``main()`` skips pipeline bootstrap when a subcommand is invoked.

The wt-16 / wt-15 commits moved the ``ctx.invoked_subcommand`` early-return
from the end of ``main()`` to the start. Subcommands (``cleanup``, ``star``,
``workspace-health``, ``contribute``) must dispatch successfully without
invoking the heavy bootstrap path. This file pins that contract so any
regression that re-runs the pipeline bootstrap on the subcommand path fails
the test, not the operator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import typer.testing

from ralph.cli.main import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


_runner = typer.testing.CliRunner()


def _patch_bootstrap_to_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch every pipeline-bootstrap helper so a stray call raises loudly.

    Covers both the private (underscored) names that ``main()`` resolves
    through the module scope and the public aliases that the rest of the
    code base imports.
    """

    def _raise_bootstrap(*_args: object, **_kwargs: object) -> None:
        msg = "subcommand dispatch must NOT trigger pipeline bootstrap"
        raise AssertionError(msg)

    target_names: tuple[str, ...] = (
        "_bootstrap_global_configs",
        "bootstrap_global_configs",
        "_bootstrap_for_command",
        "_bootstrap_global_configs_or_exit",
        "_configure_logging",
        "configure_logging",
        "_init_telemetry",
        "init_telemetry",
        # config bootstrap helpers consumed through _bootstrap_for_command
        "ensure_global_config",
        "ensure_global_agents_config",
        "ensure_global_mcp_config",
        "ensure_global_policy_configs",
        "ensure_local_configs",
        "regenerate_all",
        # helpers touched only by the pipeline path
        "_handle_early_exit_flags",
        "_handle_generate_local_config",
        "_handle_force_init_skills",
        "_handle_regenerate_config",
        "diagnose_command",
        "init_command",
        "explain_command",
        "check_policy_command",
        "handle_list_agents",
        "handle_list_providers",
        "handle_check_config",
        "handle_check_mcp",
        "handle_commit_plumbing",
        "run_pipeline",
        "invoke_pipeline",
    )
    for fn_name in target_names:
        if (
            hasattr(app, "__module__")
            and fn_name not in __import__("ralph.cli.main", fromlist=[fn_name]).__dict__
        ):
            continue
        monkeypatch.setattr(f"ralph.cli.main.{fn_name}", _raise_bootstrap)


def test_cleanup_subcommand_does_not_trigger_pipeline_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ralph cleanup`` short-circuits before any bootstrap helper runs."""
    _patch_bootstrap_to_raise(monkeypatch)
    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = _runner.invoke(app, ["cleanup", "--force"])

    assert result.exit_code == 0, result.output


def test_star_subcommand_does_not_trigger_pipeline_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ralph star --no-browser`` short-circuits before any bootstrap helper runs."""
    _patch_bootstrap_to_raise(monkeypatch)
    result = _runner.invoke(app, ["star", "--no-browser"])

    assert result.exit_code == 0, result.output


def test_subcommand_records_telemetry_via_early_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The early subcommand-return path must still forward the telemetry tag."""
    captured: list[str] = []

    def _capture(name: str) -> object:
        captured.append(name)
        return None

    def _capture_disabled() -> bool:
        return False

    monkeypatch.setattr("ralph.telemetry._sentry.is_telemetry_disabled", _capture_disabled)
    monkeypatch.setattr("ralph.telemetry._sentry.record_command_invocation", _capture)

    # The public telemetry sink is the observable surface: the CLI's
    # command-invocation chokepoint forwards the invoked subcommand name
    # to ``record_command_invocation``, so the capture above pins the
    # early-return path without reaching into a private CLI helper.
    result = _runner.invoke(app, ["star", "--no-browser"])

    assert result.exit_code == 0, result.output
    assert captured == ["star"], captured
