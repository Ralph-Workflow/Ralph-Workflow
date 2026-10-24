"""Proof that ``main()`` skips pipeline bootstrap when a subcommand is invoked.

The wt-16 / wt-15 commits moved the ``ctx.invoked_subcommand`` early-return
from the end of ``main()`` to the start. Subcommands (``cleanup``, ``star``,
``workspace-health``, ``contribute``) must dispatch successfully without
invoking the heavy bootstrap path. This file pins that contract so any
regression that re-runs the pipeline bootstrap on the subcommand path fails
the test, not the operator.

The proof is behavioural: every PUBLIC bootstrap collaborator the CLI owns
(the pipeline entry point, the global / local config bootstrap, the config
regenerator, the logging configurator) is replaced with a raising double, and
the Sentry boundary is spied. If the subcommand path reached ANY of them the
test fails.
"""

from __future__ import annotations

import atexit
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import sentry_sdk
import typer.testing

import ralph.cli.main as ralph_cli_main
from ralph.cli.main import app

if TYPE_CHECKING:
    from pathlib import Path

_runner = typer.testing.CliRunner()


def _raise_bootstrap(*_args: object, **_kwargs: object) -> None:
    """Fail loudly if a subcommand dispatch reaches a bootstrap collaborator."""
    msg = "subcommand dispatch must NOT trigger pipeline bootstrap"
    raise AssertionError(msg)


def _patch_bootstrap_to_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace every PUBLIC bootstrap collaborator with a raising double.

    Only public names are patched (``ralph.cli.main.run_pipeline``,
    ``ralph.cli.main.ensure_global_config``, ...) so the assertion targets the
    observable bootstrap behaviour rather than a private helper name.
    """
    for target in (
        "ralph.cli.main.run_pipeline",
        "ralph.cli.main.ensure_global_config",
        "ralph.cli.main.ensure_global_agents_config",
        "ralph.cli.main.ensure_global_mcp_config",
        "ralph.cli.main.ensure_global_policy_configs",
        "ralph.cli.main.ensure_local_configs",
        "ralph.cli.main.regenerate_all",
        "ralph.cli.main.configure_logging",
    ):
        monkeypatch.setattr(target, _raise_bootstrap)


def _spy_sentry_boundary(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """Record every Sentry tag the CLI publishes and keep Sentry offline."""
    tags: list[tuple[str, object]] = []

    monkeypatch.setattr(sentry_sdk, "init", lambda **_kwargs: None)
    monkeypatch.setattr(sentry_sdk, "set_user", lambda _user: None)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda key, value: tags.append((key, value)))
    monkeypatch.setattr(sentry_sdk, "set_context", lambda _key, _data: None)
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda *_a, **_kw: None)
    monkeypatch.setattr(sentry_sdk, "start_session", lambda **_kw: None)
    monkeypatch.setattr(sentry_sdk, "start_transaction", lambda **_kw: None)
    monkeypatch.setattr(sentry_sdk, "add_breadcrumb", lambda **_kw: None)
    monkeypatch.setattr(atexit, "register", lambda func, *_a, **_kw: func)
    return tags


def _isolate_telemetry_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the anonymous telemetry identity file inside the test tree."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)


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


def test_subcommand_does_not_initialize_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The early subcommand-return path never reaches the telemetry bootstrap.

    The Sentry client is only initialized by the pipeline bootstrap, so a
    single ``sentry_sdk.init`` call on the subcommand path would mean the
    heavy bootstrap ran.
    """
    _isolate_telemetry_home(monkeypatch, tmp_path)
    init_calls: list[dict[str, object]] = []
    _spy_sentry_boundary(monkeypatch)
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: init_calls.append(kwargs))
    _patch_bootstrap_to_raise(monkeypatch)

    result = _runner.invoke(app, ["star", "--no-browser"])

    assert result.exit_code == 0, result.output
    assert init_calls == [], "the subcommand path must not initialize the Sentry client"


def test_subcommand_records_telemetry_via_early_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The early subcommand-return path must still forward the telemetry tag.

    The Sentry boundary is the observable surface: the CLI's
    command-invocation chokepoint forwards the invoked subcommand name to
    the telemetry sink, which publishes it as the ``command`` tag.
    """
    _isolate_telemetry_home(monkeypatch, tmp_path)
    tags = _spy_sentry_boundary(monkeypatch)
    ralph_cli_main.init_telemetry()

    result = _runner.invoke(app, ["star", "--no-browser"])

    assert result.exit_code == 0, result.output
    assert ("command", "star") in tags, f"the command tag was not published; got {tags!r}"
