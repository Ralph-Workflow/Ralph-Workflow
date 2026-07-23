"""Tests for ralph.cli.commands.init baseline capability integration.

These tests are subprocess_e2e: they exercise the real `ralph --init` entry
point and its full filesystem path. They cannot be mocked down to the
per-test 1 s budget without losing the end-to-end contract they assert.
"""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

import pytest
import typer
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


pytestmark = [pytest.mark.timeout_seconds(10), pytest.mark.subprocess_e2e]


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
        mode="default",
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


@pytest.mark.timeout_seconds(3)
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

    init_module.init_command(template=None)

    assert called, "ensure_baseline_capabilities was not called"


@pytest.mark.timeout_seconds(3)
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

    init_module.init_command(template=None)

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
    init_module.init_command(template=None)

    # Prompt and global configuration setup still complete.
    assert (tmp_path / "PROMPT.md").exists()
    assert not (tmp_path / ".agent").exists()

    # Error should be silently swallowed (no crash)
    output = stream.getvalue()
    assert "Ralph" in output
    assert "Created" in output


@pytest.mark.timeout_seconds(3)
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

    init_module.init_command(template=None)
    assert calls == 1

    # Re-run also runs the capability refresh.
    init_module.init_command(template=None)
    assert calls == 2

    output = stream.getvalue()
    assert "Ralph Workflow initialized in" in output


@pytest.mark.timeout_seconds(3)
def test_init_label_over_existing_prompt_warns_not_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``ralph --init <label>`` over an existing PROMPT.md must NOT silently ignore the label.

    AC-03: an explicit valid label must print a clear "template was not applied"
    warning that names the label and tells the operator how to apply it,
    instead of silently doing nothing. PROMPT.md content must be unchanged.
    """
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)

    # Pre-create PROMPT.md with a sentinel-free marker so we can verify
    # the init path leaves it alone (no overwrite).
    (tmp_path / "PROMPT.md").write_text("# my task\ndo the thing\n", encoding="utf-8")

    def fake_ensure(_self_obj: object, *, workspace_root: object) -> object:
        return CapabilityState(), []

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template="refactor")

    output = stream.getvalue()
    # The warning names the label so the operator can act on it.
    assert "refactor" in output, (
        f"Expected the label 'refactor' to be quoted in the warning so "
        f"the operator can act on it, got: {output}"
    )
    # The warning points the operator at the remediation.
    assert (
        "edit PROMPT.md directly" in output
        or "remove/rename PROMPT.md" in output
        or "ralph --init refactor" in output
    ), (
        f"Expected the warning to point the operator at the remediation "
        f"path, got: {output}"
    )
    # The original PROMPT.md content was preserved (no overwrite).
    assert (tmp_path / "PROMPT.md").read_text(encoding="utf-8") == "# my task\ndo the thing\n", (
        "PROMPT.md must NOT be overwritten when an explicit label is "
        "passed over an existing file"
    )


@pytest.mark.timeout_seconds(3)
def test_init_label_over_existing_prompt_unknown_label_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``ralph --init <bad-label>`` over an existing PROMPT.md must still error non-zero.

    AC-03: an UNKNOWN label over an existing PROMPT.md must NOT silently
    succeed (the typo'd label still has to be flagged). The init path
    resolves the template first to surface unknown labels, then prints
    the same warning a valid label would emit.
    """
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "PROMPT.md").write_text("# existing\n", encoding="utf-8")

    def fake_ensure(_self_obj: object, *, workspace_root: object) -> object:
        return CapabilityState(), []

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    with pytest.raises(typer.Exit):
        init_module.init_command(template="feature-specs")  # plural — typo

    output = stream.getvalue()
    # Unknown label must surface the same it/why/fix envelope as the
    # bare ``ralph --init feature-specs`` path.
    assert "feature-specs" in output or "Unknown PROMPT.md template" in output, (
        f"Expected the unknown-label name to appear in the warning, got: {output}"
    )


@pytest.mark.timeout_seconds(3)
def test_init_no_template_over_existing_prompt_stays_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bare ``ralph --init`` (no template) over an existing PROMPT.md stays silent.

    AC-03: only an EXPLICIT --init <label> triggers the new warning; a bare
    re-run keeps its existing behavior (no spurious warning).
    """
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "PROMPT.md").write_text("# existing\n", encoding="utf-8")

    def fake_ensure(_self_obj: object, *, workspace_root: object) -> object:
        return CapabilityState(), []

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template=None)

    output = stream.getvalue()
    # The AC-03 warning should NOT appear for a bare re-run.
    assert "starter template was not applied" not in output, (
        f"A bare re-run must not print the template-not-applied warning, "
        f"got: {output}"
    )
