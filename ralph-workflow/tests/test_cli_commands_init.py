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
from ralph.skills._installer import install_project_baseline_skills

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.timeout_seconds(10)]


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
    _ = install_project_baseline_skills
    monkeypatch.setattr("ralph.skills._installer._project_skills_need_install", lambda _root: False)
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills",
        lambda _root: ({}, []),
    )
    return stream


@pytest.mark.timeout_seconds(3)
def test_init_warns_when_current_worktree_owns_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        init_module,
        "target_worktree_lookup",
        lambda _root, _target: ("found", tmp_path),
    )
    monkeypatch.setattr(
        init_module,
        "ensure_global_config",
        lambda: pytest.fail("setup should not run before this assertion"),
    )

    with pytest.raises(pytest.fail.Exception):
        init_module.init_command(template=None)

    output = stream.getvalue()
    assert "Auto-integration owns" in output
    assert "snapshot" in output
    assert "reset" in output
    assert "uncommitted" in output


def test_init_ownership_warning_uses_configured_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(
        init_module,
        "_load_config_loader",
        lambda: (
            lambda *_args, **_kwargs: type(
                "Config",
                (),
                {"general": type("General", (), {"auto_integrate_target": "release"})()},
            )()
        ),
    )
    monkeypatch.setattr(
        init_module,
        "target_worktree_lookup",
        lambda _root, target: (observed.append(target) and "not-found", None),
    )

    init_module._emit_target_worktree_warning(
        type("Display", (), {"emit_warning": lambda *_args: None})(), tmp_path
    )

    assert observed == ["release"]


def test_try_load_registry_uses_the_current_workspace_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_scope = object()
    received_scope: object | None = None

    def fake_load_config(
        _config_path: Path | None,
        _overrides: dict[str, object],
        *,
        workspace_scope: object,
    ) -> object:
        nonlocal received_scope
        received_scope = workspace_scope
        return object()

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            return object()

    monkeypatch.setattr(init_module, "resolve_workspace_scope", lambda: expected_scope)
    monkeypatch.setattr(init_module, "_load_config_loader", lambda: fake_load_config)
    monkeypatch.setattr(init_module, "_load_agent_registry_factory", lambda: FakeRegistry)

    assert init_module._try_load_registry() is not None
    assert received_scope is expected_scope


@pytest.mark.timeout_seconds(3)
def test_init_command_calls_ensure_baseline_capabilities(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)

    def fake_ensure_that_raises(*args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated skill install failure")

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure_that_raises,
    )

    init_module.init_command(template=None)

    assert (tmp_path / "PROMPT.md").exists()
    assert not (tmp_path / ".agent").exists()

    output = stream.getvalue()
    assert "Ralph" in output
    assert "Created" in output


@pytest.mark.timeout_seconds(3)
def test_init_command_runs_capability_refresh_on_every_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    init_module.init_command(template=None)
    assert calls == 2

    output = stream.getvalue()
    assert "Ralph Workflow initialized in" in output


@pytest.mark.timeout_seconds(3)
def test_init_label_over_existing_prompt_warns_not_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)

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
    assert "refactor" in output, (
        f"Expected the label 'refactor' to be quoted in the warning so "
        f"the operator can act on it, got: {output}"
    )
    assert (
        "edit PROMPT.md directly" in output
        or "remove/rename PROMPT.md" in output
        or "ralph --init refactor" in output
    ), f"Expected the warning to point the operator at the remediation path, got: {output}"
    assert (tmp_path / "PROMPT.md").read_text(encoding="utf-8") == "# my task\ndo the thing\n", (
        "PROMPT.md must NOT be overwritten when an explicit label is passed over an existing file"
    )


@pytest.mark.timeout_seconds(3)
def test_init_label_over_existing_prompt_unknown_label_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
        init_module.init_command(template="feature-specs")

    output = stream.getvalue()
    assert "feature-specs" in output or "Unknown PROMPT.md template" in output, (
        f"Expected the unknown-label name to appear in the warning, got: {output}"
    )


@pytest.mark.timeout_seconds(3)
def test_init_rerun_without_agents_warns_with_install_and_rerun_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.setattr(init_module, "detect_installed_agents", lambda: [])

    init_module._print_fallback_next_steps(
        tmp_path,
        display_context=DisplayContext(
            console=Console(file=stream, force_terminal=False, theme=RALPH_THEME),
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
        ),
    )

    output = stream.getvalue()
    assert "No agent CLIs found on PATH" in output
    assert "https://" in output
    assert "re-run `ralph --init`" in output


def test_init_no_template_over_existing_prompt_stays_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert "starter template was not applied" not in output, (
        f"A bare re-run must not print the template-not-applied warning, got: {output}"
    )
