"""Tests for _sync_shipped_skills_on_pipeline_run in run.py.

Most cases are MagicMock unit tests in the default suite. Only the
real-git install/autocommit paths are marked ``subprocess_e2e``: they
exercise the full filesystem + git + auto-commit path and cannot be
mocked down without losing the end-to-end contract they assert.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from git import Repo
from loguru import logger
from rich.console import Console

from ralph.cli.commands import run as run_module
from ralph.display.context import make_display_context

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.timeout_seconds(5)]


# wt-012: the producer-level wrapper that records the byte-exact
# diff for the auto-commit at the install boundary. Tests that want
# to drive the REAL install + commit path patch this on its defining
# module so the lazy import inside ``sync_shipped_skills`` picks up
# the real implementation.
def _real_producer_wrapper():
    """Return the unpatched producer-level wrapper used by real-install tests."""
    import importlib

    return importlib.import_module(
        "ralph.skills._installer"
    ).install_project_baseline_skills_with_diff


def _stub_heavy_sync_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    skip_project_install: bool = True,
    skip_auto_commit: bool = True,
    skip_retention_sweep: bool = True,
    skip_bootstrap_seed: bool = True,
) -> None:
    """Stub out the heavy side-effect paths that the sync helper fans out to.

    Most MagicMock tests in this file only care about one helper (SkillManager,
    auto_seed_default_git_exclude, ...). The other side-effect paths
    (``commit_skill_writes``, ``sweep_agent_dir``,
    ``install_project_baseline_skills_with_diff``) are lazy-imported
    and run FOR REAL on every call, costing ~0.6 s per test. Stub them
    when the test does not exercise them so the default suite stays
    inside the immutable combined 60 s budget. Each lazy-import target
    is patched on its defining module so the import inside
    ``_sync_shipped_skills_on_pipeline_run`` picks up the stub.
    """
    if skip_project_install:
        # ``run.py`` does a top-level ``from ralph.skills._installer import ...``
        # (the names are bound into ``run_module`` at import time), so
        # patching the installer module is a no-op for these two names.
        # Patch the rebinding on ``run_module`` instead.
        monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
        # The producer-level wrapper is lazy-imported inside
        # ``sync_shipped_skills``; patch it on the defining module so
        # the lazy import picks up the stub.
        from ralph.skills._installer import ProjectSkillInstallOutcome

        monkeypatch.setattr(
            "ralph.skills._installer.install_project_baseline_skills_with_diff",
            lambda _root: ProjectSkillInstallOutcome(
                entry=MagicMock(), failures=[], written_paths=[], pre_contents={}
            ),
        )
    if skip_auto_commit:
        monkeypatch.setattr(
            "ralph.skills._auto_commit.commit_skill_updates",
            lambda *args: None,
        )
        # wt-012: the producer-level helper is the one actually called.
        from ralph.git.scoped_auto_commit import ScopedCommitResult, ScopedCommitStatus

        monkeypatch.setattr(
            "ralph.skills._auto_commit.commit_skill_writes",
            lambda *args, **_kwargs: ScopedCommitResult(status=ScopedCommitStatus.NOOP),
        )
    if skip_retention_sweep:
        monkeypatch.setattr(
            "ralph.workspace.agent_dir_retention.sweep_agent_dir",
            lambda *args, **_kw: 0,
        )
    if skip_bootstrap_seed:
        monkeypatch.setattr(
            "ralph.config.bootstrap.auto_seed_default_gitignore",
            lambda _root: None,
        )
        monkeypatch.setattr(
            "ralph.config.bootstrap.auto_seed_default_git_exclude",
            lambda _root: None,
        )


def test_sync_calls_check_skills_for_updates_even_without_state_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_heavy_sync_paths(monkeypatch)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    mock_manager.check_skills_for_updates.assert_called_once_with()


def test_sync_calls_check_skills_for_updates_when_state_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_heavy_sync_paths(monkeypatch)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    mock_manager.check_skills_for_updates.assert_called_once_with()


def test_sync_is_non_fatal_on_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_heavy_sync_paths(monkeypatch)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.side_effect = RuntimeError("simulated failure")
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)  # must not raise


def test_sync_seeds_missing_project_skills(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When predicate is True, the producer-level install wrapper is called."""
    # The producer-level path requires a git repo so
    # ``snapshot_dirty_paths_strict`` can read the working tree.
    Repo.init(tmp_path)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    from ralph.skills._installer import ProjectSkillInstallOutcome

    fake_install = MagicMock(
        return_value=ProjectSkillInstallOutcome(
            entry=MagicMock(), failures=[], written_paths=[], pre_contents={}
        )
    )
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills_with_diff", fake_install
    )

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    fake_install.assert_called_once_with(tmp_path)


def test_sync_skips_project_install_when_canonical_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When predicate is False, the producer-level install wrapper is NOT called."""
    Repo.init(tmp_path)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    from ralph.skills._installer import ProjectSkillInstallOutcome

    fake_install = MagicMock(
        return_value=ProjectSkillInstallOutcome(
            entry=MagicMock(), failures=[], written_paths=[], pre_contents={}
        )
    )
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills_with_diff", fake_install
    )

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    fake_install.assert_not_called()


def test_sync_seeds_project_skills_even_when_user_global_needs_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project install runs even when user-global check returns True."""
    Repo.init(tmp_path)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = True
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    from ralph.skills._installer import ProjectSkillInstallOutcome

    fake_install = MagicMock(
        return_value=ProjectSkillInstallOutcome(
            entry=MagicMock(), failures=[], written_paths=[], pre_contents={}
        )
    )
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills_with_diff", fake_install
    )

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    fake_install.assert_called_once_with(tmp_path)


def test_sync_is_non_fatal_on_project_install_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A raising install must not raise; logger.warning is emitted (wt-012)."""
    Repo.init(tmp_path)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    def _raising(_root: Path) -> object:
        raise RuntimeError("simulated project install failure")

    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills_with_diff", _raising
    )

    captured: list[str] = []
    sink_id = logger.add(captured.append, level="DEBUG", format="{message}")
    try:
        run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)
    finally:
        logger.remove(sink_id)

    assert any("Skill auto-commit failed" in message for message in captured), (
        f"Expected log line about auto-commit failure, got: {captured!r}"
    )


def test_sync_surfaces_force_init_skills_hint_on_project_skill_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project-scope NEEDS_REPAIR triggers the helper with the failures list (called once)."""
    Repo.init(tmp_path)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    from ralph.skills._installer import ProjectSkillInstallOutcome

    fake_install = MagicMock(
        return_value=ProjectSkillInstallOutcome(
            entry=MagicMock(),
            failures=["sibling-conflict-using-superpowers"],
            written_paths=[],
            pre_contents={},
        )
    )
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills_with_diff", fake_install
    )

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
    """Empty failures list from the producer wrapper: the hint is NOT called."""
    Repo.init(tmp_path)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    from ralph.skills._installer import ProjectSkillInstallOutcome

    fake_install = MagicMock(
        return_value=ProjectSkillInstallOutcome(
            entry=MagicMock(), failures=[], written_paths=[], pre_contents={}
        )
    )
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills_with_diff", fake_install
    )

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
    fake_install = MagicMock()
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills_with_diff", fake_install
    )

    gitignore_mock = MagicMock()
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", gitignore_mock)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    gitignore_mock.assert_called_once_with(tmp_path)
    fake_install.assert_not_called()


def test_sync_seeds_default_git_exclude_on_every_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The .git/info/exclude auto-seed is INDEPENDENT of the project-scope skill predicate."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
    fake_install = MagicMock()
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills_with_diff", fake_install
    )

    gitignore_mock = MagicMock()
    git_exclude_mock = MagicMock()
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", gitignore_mock)
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_git_exclude", git_exclude_mock)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    gitignore_mock.assert_called_once_with(tmp_path)
    git_exclude_mock.assert_called_once_with(tmp_path)
    fake_install.assert_not_called()


def test_sync_git_exclude_seed_is_non_fatal_on_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A raising git exclude auto-seed must not raise; logger.debug is emitted."""
    # Keep the bootstrap stub disabled so the test's targeted
    # ``auto_seed_default_git_exclude`` raising-mock is the only
    # bootstrap interference (the parallel gitignore call must run for
    # real to exercise the auto-seed path).
    _stub_heavy_sync_paths(monkeypatch, skip_bootstrap_seed=False)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    monkeypatch.setattr(
        "ralph.config.bootstrap.auto_seed_default_git_exclude",
        MagicMock(side_effect=RuntimeError("simulated")),
    )

    captured: list[str] = []
    sink_id = logger.add(captured.append, level="DEBUG", format="{message}")
    try:
        run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)
    finally:
        logger.remove(sink_id)

    assert any(
        "Project .gitignore/.git/info/exclude auto-seed failed" in message for message in captured
    ), f"Expected debug log line, got: {captured!r}"


def test_sync_gitignore_seed_is_non_fatal_on_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A raising gitignore auto-seed must not raise; logger.debug is emitted."""
    _stub_heavy_sync_paths(monkeypatch, skip_bootstrap_seed=False)
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

    assert any(
        "Project .gitignore/.git/info/exclude auto-seed failed" in message for message in captured
    ), f"Expected debug log line, got: {captured!r}"


def test_sync_surfaces_force_init_skills_hint_on_user_global_update_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the user-global check returns True, the hint helper is called once with True."""
    Repo.init(tmp_path)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = True
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
    fake_install = MagicMock()
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills_with_diff", fake_install
    )

    hint_mock = MagicMock()
    monkeypatch.setattr(run_module, "_print_user_global_update_hint", hint_mock)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    hint_mock.assert_called_once_with()


def test_sync_does_not_surface_user_global_hint_when_update_not_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the user-global check returns False, the hint helper is NOT called."""
    Repo.init(tmp_path)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
    fake_install = MagicMock()
    monkeypatch.setattr(
        "ralph.skills._installer.install_project_baseline_skills_with_diff", fake_install
    )

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
