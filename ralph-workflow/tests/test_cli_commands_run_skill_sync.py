"""Tests for _sync_shipped_skills_on_pipeline_run in run.py."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from loguru import logger
from rich.console import Console

from ralph.cli.commands import run as run_module
from ralph.display.context import make_display_context

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_sync_calls_check_skills_for_updates_even_without_state_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    mock_manager.check_skills_for_updates.assert_called_once_with()


def test_sync_calls_check_skills_for_updates_when_state_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    mock_manager.check_skills_for_updates.assert_called_once_with()


def test_sync_is_non_fatal_on_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.side_effect = RuntimeError("simulated failure")
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)  # must not raise


def test_sync_seeds_missing_project_skills(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When predicate is True, install_project_baseline_skills is called."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    fake_install = MagicMock(return_value=(MagicMock(), []))
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    monkeypatch.setattr(run_module, "install_project_baseline_skills", fake_install)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    fake_install.assert_called_once_with(tmp_path)


def test_sync_skips_project_install_when_canonical_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When predicate is False, install_project_baseline_skills is NOT called."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    fake_install = MagicMock(return_value=(MagicMock(), []))
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
    monkeypatch.setattr(run_module, "install_project_baseline_skills", fake_install)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    fake_install.assert_not_called()


def test_sync_seeds_project_skills_even_when_user_global_needs_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project install runs even when user-global check returns True."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = True
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    fake_install = MagicMock(return_value=(MagicMock(), []))
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    monkeypatch.setattr(run_module, "install_project_baseline_skills", fake_install)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    fake_install.assert_called_once_with(tmp_path)


def test_sync_is_non_fatal_on_project_install_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A raising install must not raise; logger.debug is emitted (PA-008)."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    def _raising(_root: Path) -> tuple[object, list[str]]:
        raise RuntimeError("simulated project install failure")

    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    monkeypatch.setattr(run_module, "install_project_baseline_skills", _raising)

    captured: list[str] = []
    sink_id = logger.add(captured.append, level="DEBUG", format="{message}")
    try:
        run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)
    finally:
        logger.remove(sink_id)

    assert any("Project-scope skill install failed" in message for message in captured), (
        f"Expected debug log line, got: {captured!r}"
    )


def test_sync_surfaces_force_init_skills_hint_on_project_skill_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project-scope NEEDS_REPAIR triggers the helper with the failures list (called once)."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    fake_install = MagicMock(return_value=(MagicMock(), ["sibling-conflict-using-superpowers"]))
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    monkeypatch.setattr(run_module, "install_project_baseline_skills", fake_install)

    hint_mock = MagicMock()
    monkeypatch.setattr(run_module, "_print_project_skill_conflict_hint", hint_mock)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    hint_mock.assert_called_once_with(["sibling-conflict-using-superpowers"])


def test_sync_hint_text_mentions_force_init_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The hint's literal text must mention ralph --force-init-skills so the user can act on it."""
    stream = io.StringIO()
    captured_console = Console(
        file=stream,
        force_terminal=False,
        color_system=None,
    )
    captured_ctx = make_display_context(console=captured_console)
    monkeypatch.setattr(run_module, "make_display_context", lambda **_kwargs: captured_ctx)

    run_module._print_project_skill_conflict_hint(["sibling-conflict-using-superpowers"])

    rendered = stream.getvalue()
    assert "ralph --force-init-skills" in rendered, (
        f"Expected `ralph --force-init-skills` hint in captured console text; got: {rendered!r}"
    )
    assert "sibling-conflict-using-superpowers" in rendered, (
        f"Expected the failure code in captured console text; got: {rendered!r}"
    )


def test_sync_does_not_print_hint_on_clean_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Empty failures list from install_project_baseline_skills: the hint is NOT called."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    fake_install = MagicMock(return_value=(MagicMock(), []))
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    monkeypatch.setattr(run_module, "install_project_baseline_skills", fake_install)

    hint_mock = MagicMock()
    monkeypatch.setattr(run_module, "_print_project_skill_conflict_hint", hint_mock)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    hint_mock.assert_not_called()


def test_sync_seeds_default_gitignore_on_every_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The .gitignore auto-seed is INDEPENDENT of the project-scope skill predicate."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
    monkeypatch.setattr(run_module, "install_project_baseline_skills", MagicMock())

    gitignore_mock = MagicMock()
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", gitignore_mock)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    gitignore_mock.assert_called_once_with(tmp_path)
    run_module.install_project_baseline_skills.assert_not_called()


def test_sync_gitignore_seed_is_non_fatal_on_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A raising gitignore auto-seed must not raise; logger.debug is emitted."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    monkeypatch.setattr(
        "ralph.config.bootstrap.auto_seed_default_gitignore",
        MagicMock(side_effect=RuntimeError("simulated")),
    )

    captured: list[str] = []
    sink_id = logger.add(captured.append, level="DEBUG", format="{message}")
    try:
        run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)
    finally:
        logger.remove(sink_id)

    assert any("Project .gitignore auto-seed failed" in message for message in captured), (
        f"Expected debug log line, got: {captured!r}"
    )


def test_sync_surfaces_force_init_skills_hint_on_user_global_update_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the user-global check returns True, the hint helper is called once with True."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = True
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
    monkeypatch.setattr(run_module, "install_project_baseline_skills", MagicMock())

    hint_mock = MagicMock()
    monkeypatch.setattr(run_module, "_print_user_global_update_hint", hint_mock)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    hint_mock.assert_called_once_with()


def test_sync_does_not_surface_user_global_hint_when_update_not_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the user-global check returns False, the hint helper is NOT called."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
    monkeypatch.setattr(run_module, "install_project_baseline_skills", MagicMock())

    hint_mock = MagicMock()
    monkeypatch.setattr(run_module, "_print_user_global_update_hint", hint_mock)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    hint_mock.assert_not_called()


def test_sync_user_global_hint_text_mentions_force_init_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The hint text must mention ralph --force-init-skills so the user can act on it."""
    stream = io.StringIO()
    captured_console = Console(
        file=stream,
        force_terminal=False,
        color_system=None,
    )
    captured_ctx = make_display_context(console=captured_console)
    monkeypatch.setattr(run_module, "make_display_context", lambda **_kwargs: captured_ctx)

    run_module._print_user_global_update_hint()

    rendered = stream.getvalue()
    normalized = " ".join(rendered.split())
    assert "ralph --force-init-skills" in normalized, (
        f"Expected `ralph --force-init-skills` hint in captured console text; got: {rendered!r}"
    )
