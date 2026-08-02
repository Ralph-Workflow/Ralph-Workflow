"""Regression coverage for terminal-colour diagnosis rows."""

from __future__ import annotations

from dataclasses import replace
from io import StringIO

from rich.console import Console

from ralph.cli.commands import diagnose as diagnose_module
from ralph.display.context import make_display_context
from ralph.display.parallel_display import resolve_active_display
from ralph.display.theme import RALPH_THEME


def test_diagnose_terminal_colour_rows_show_snapshot_provenance(
    monkeypatch,
) -> None:
    """S-4: diagnose identifies the snapshot that supplied the running package."""
    monkeypatch.setattr(diagnose_module._build_meta, "BUILD_FLAVOR", "-build")
    monkeypatch.setattr(diagnose_module._build_meta, "BUILD_SOURCE_COMMIT", "abc123")
    monkeypatch.setattr(
        diagnose_module._build_meta, "BUILD_INSTALLED_AT", "2026-08-02T12:00:00+00:00"
    )
    stream = StringIO()
    context = replace(
        make_display_context(env={"PYTHONPATH": "/snapshot", "VIRTUAL_ENV": "/snapshot/.venv"}),
        console=Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME),
    )

    diagnose_module._check_terminal_colour(context, display=resolve_active_display(None, context))

    output = stream.getvalue()
    for value in (
        "Build flavor",
        "-build",
        "Built from commit",
        "abc123",
        "Installed at",
        "2026-08-02T12:00:00+00:00",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "Warning: colour system is unset",
    ):
        assert value in output


def test_diagnose_terminal_colour_omits_warning_when_colour_is_available() -> None:
    """S-4: a colour-capable console needs no generic remediation warning."""
    stream = StringIO()
    context = make_display_context(
        console=Console(file=stream, force_terminal=True, color_system="truecolor", theme=RALPH_THEME),
        env={},
    )

    diagnose_module._check_terminal_colour(context, display=resolve_active_display(None, context))

    assert "Warning: colour system is unset" not in stream.getvalue()
