"""Tests for _sync_shipped_skills_on_pipeline_run in run.py."""

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
from ralph.skills._content import BASELINE_SKILL_NAMES, get_skill_content
from ralph.skills._installer import install_project_baseline_skills

if TYPE_CHECKING:
    from pathlib import Path


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


def test_sync_seeds_default_git_exclude_on_every_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The .git/info/exclude auto-seed is INDEPENDENT of the project-scope skill predicate."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
    monkeypatch.setattr(run_module, "install_project_baseline_skills", MagicMock())

    gitignore_mock = MagicMock()
    git_exclude_mock = MagicMock()
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", gitignore_mock)
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_git_exclude", git_exclude_mock)

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    gitignore_mock.assert_called_once_with(tmp_path)
    git_exclude_mock.assert_called_once_with(tmp_path)
    run_module.install_project_baseline_skills.assert_not_called()


def test_sync_git_exclude_seed_is_non_fatal_on_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A raising git exclude auto-seed must not raise; logger.debug is emitted."""
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


@pytest.mark.timeout_seconds(10)
def test_sync_shipped_skills_creates_auto_commit_on_dirty_skill_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PA-001 closure: end-to-end auto-commit on a dirty skill tree.

    Runs ``_sync_shipped_skills_on_pipeline_run`` against a fresh git
    repo whose ``.opencode/skills/<name>/SKILL.md`` was pre-staged with
    STALE content and committed. The project-scope install runs FOR REAL
    (no MagicMock on ``install_project_baseline_skills``) so the bundled
    content overwrites the stale content and produces a real diff.
    ``commit_skill_updates`` and ``create_commit`` also run FOR REAL --
    the test asserts the resulting commit subject and body on disk.

    Asserts:
      * the repo's HEAD subject is EXACTLY ``chore(skills): sync baseline bundle``
      * the repo's HEAD body contains both ``Auto-generated by Ralph skill sync``
        and ``Changed skills:`` headers
      * the changed skill names appear in the body under ``Changed skills:``
    """
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    # 1. Initialize a fresh git repo at tmp_path with a baseline commit
    #    containing a stale ``.opencode/skills/<name>/SKILL.md`` file.
    Repo.init(tmp_path)
    repo = Repo(tmp_path)
    repo.config_writer().set_value("user", "name", "Test Author").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()

    name = BASELINE_SKILL_NAMES[0]
    canonical = tmp_path / ".opencode" / "skills" / name
    canonical.mkdir(parents=True, exist_ok=True)
    stale_content = "# stale version from an earlier Ralph run\n"
    (canonical / "SKILL.md").write_text(stale_content, encoding="utf-8")
    (canonical / ".ralph-managed.json").write_text(
        '{"managed_by": "ralph-workflow", "installed_content_sha256": "deadbeef"}',
        encoding="utf-8",
    )

    # 2. Patch SkillManager so user-global is a no-op (signal-only)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    # 3. Force the project-scope install to run
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)

    # 4. Real install_project_baseline_skills (not mocked) -- it will
    #    overwrite stale content with the bundled content.
    monkeypatch.setattr(
        run_module, "install_project_baseline_skills", install_project_baseline_skills
    )

    # 5. Patch the gitignore/exclude auto-seeders so they do not create
    #    noise in the test git repo.
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", lambda _r: None)
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_git_exclude", lambda _r: None)

    # 6. Initial commit so we have a HEAD before the auto-commit runs
    repo.index.add([".opencode"])
    repo.index.commit("initial stale commit")
    repo.close()

    # 7. Run the full pipeline (commit_skill_updates + create_commit are REAL)
    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    # 8. Verify the auto-commit landed on HEAD with the deterministic subject
    repo = Repo(tmp_path)
    try:
        head_subject = repo.head.commit.message.splitlines()[0]
        head_body = "\n".join(repo.head.commit.message.splitlines()[2:])
        assert head_subject == "chore(skills): sync baseline bundle", (
            f"Auto-commit subject must be deterministic; got: {head_subject!r}"
        )
        assert "Auto-generated by Ralph skill sync" in head_body, (
            f"Auto-commit body must contain the auto-generation header; got: {head_body!r}"
        )
        assert "Changed skills:" in head_body, (
            f"Auto-commit body must list changed skill names under 'Changed skills:'; "
            f"got: {head_body!r}"
        )
        assert f"- {name}" in head_body, (
            f"Auto-commit body must list the changed skill name `{name}`; got: {head_body!r}"
        )
        # And the staged SKILL.md must now match the bundled content
        on_disk = (tmp_path / ".opencode" / "skills" / name / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert on_disk == get_skill_content(name), (
            "Auto-commit must carry the bundled SKILL.md content (overwrite-old branch)"
        )
    finally:
        repo.close()
