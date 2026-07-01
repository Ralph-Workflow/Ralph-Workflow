"""Tests for ralph.cli.commands.init baseline capability integration."""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.cli.commands import init as init_module
from ralph.display.context import DisplayContext
from ralph.display.theme import RALPH_THEME
from ralph.skills import manager as manager_module
from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_state import CapabilityState
from ralph.skills._capability_status import CapabilityStatus

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.subprocess_e2e


def _attach_console(monkeypatch: pytest.MonkeyPatch, module: object) -> StringIO:
    stream = StringIO()
    console = Console(
        file=stream,
        force_terminal=False,
        color_system=None,
        theme=RALPH_THEME,
    )

    ctx = DisplayContext(
        console=console,
        theme=RALPH_THEME,
        width=80,
        mode="wide",
        narrow=False,
        color_enabled=True,
        glyphs_enabled=True,
        headline_max_chars=120,
        condenser_soft_limit=400,
        condenser_hard_limit=4000,
        streaming_checkpoint_chars=4000,
        streaming_checkpoint_fragments=20,
        streaming_dedup_enabled=True,
        streaming_checkpoints_enabled=True,
        thinking_preview_min_chars=80,
        tool_result_headline_min_chars=80,
    )

    def fake_make_display_context(**kwargs: object) -> object:
        return ctx

    monkeypatch.setattr(module, "make_display_context", fake_make_display_context)
    return stream


def test_init_command_calls_ensure_baseline_capabilities(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """init_command should call SkillManager.ensure_baseline_capabilities()."""
    _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)

    called = False

    def fake_ensure(_self_obj: object, *, workspace_root: object) -> object:
        nonlocal called
        called = True
        return CapabilityState(), []

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template="default")

    assert called, "ensure_baseline_capabilities was not called"


def test_init_command_prints_capability_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """init_command output should include Built-in and Managed capability labels."""
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)

    def fake_ensure(
        _self_obj: object, *, workspace_root: object
    ) -> tuple[CapabilityState, list[str]]:
        return (
            CapabilityState(
                web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
                skills=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            ),
            [],
        )

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template="default")

    output = stream.getvalue()
    assert "Built-in" in output, "Expected 'Built-in' label in init output"
    assert "Managed" in output, "Expected 'Managed' label in init output"


def test_init_command_skill_failure_does_not_block_init(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If skill installation fails, init should still create files and complete."""
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)

    def fake_ensure_that_raises(*args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated skill install failure")

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure_that_raises,
    )

    # Should not raise
    init_module.init_command(template="default")

    # Files should still be created
    assert (tmp_path / "PROMPT.md").exists()
    assert (tmp_path / ".agent" / "mcp.toml").exists()

    # Error should be silently swallowed (no crash)
    output = stream.getvalue()
    assert "Ralph" in output
    assert "Created" in output


def test_init_command_runs_capability_refresh_on_every_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Capability refresh MUST run on every ralph --init invocation, including re-runs.

    The previous test name `test_init_command_fallback_path_skips_capability_refresh`
    pinned the buggy behavior (skip on re-run). The new contract is the
    always-on auto-skill-install — every invocation triggers the skill
    installer + the full capability summary table.
    """
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)

    calls = 0

    def fake_ensure(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return CapabilityState(), []

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template="default")
    assert calls == 1

    # Re-run also runs the capability refresh.
    init_module.init_command(template="default")
    assert calls == 2

    output = stream.getvalue()
    assert "Ralph Workflow initialized in" in output
