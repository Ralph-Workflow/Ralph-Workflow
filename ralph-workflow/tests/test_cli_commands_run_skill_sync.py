from __future__ import annotations

import hashlib
import io
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from git import Repo
from loguru import logger
from rich.console import Console

from ralph.cli.commands import run as run_module
from ralph.cli.commands._load_result import _LoadResult
from ralph.display.context import make_display_context
from ralph.git.commit_review import untrack_engine_internal_files
from ralph.skills._agent_paths import _SKILL_ROOT_PREFIXES
from ralph.skills._content import BASELINE_SKILL_NAMES, get_skill_content
from ralph.skills._installer import install_project_baseline_skills

if TYPE_CHECKING:
    from pathlib import Path


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "_stub_heavy_sync_paths": (
        "the dead commit helper was removed (zero-dead-code rule), so the stub that patched "
        "it was dropped; these tests stub project_skills_need_install to False, so the live "
        "commit path is never reached"
    ),
}

pytestmark = [pytest.mark.timeout_seconds(5)]


def _stub_heavy_sync_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    skip_project_install: bool = True,
    skip_retention_sweep: bool = True,
    skip_bootstrap_seed: bool = True,
) -> None:
    if skip_project_install:
        monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: False)
        monkeypatch.setattr(run_module, "install_project_baseline_skills", lambda _root: ({}, []))
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

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)


def test_sync_seeds_missing_project_skills(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
@pytest.mark.subprocess_e2e
def test_sync_shipped_skills_creates_auto_commit_on_dirty_skill_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

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
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": hashlib.sha256(
                    stale_content.encode("utf-8")
                ).hexdigest(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)

    monkeypatch.setattr(
        run_module, "install_project_baseline_skills", install_project_baseline_skills
    )

    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", lambda _r: None)
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_git_exclude", lambda _r: None)

    repo.index.add([".opencode"])
    repo.index.commit("initial stale commit")
    repo.close()

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

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
        on_disk = (tmp_path / ".opencode" / "skills" / name / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert on_disk == get_skill_content(name), (
            "Auto-commit must carry the bundled SKILL.md content (overwrite-old branch)"
        )
    finally:
        repo.close()


@pytest.mark.timeout_seconds(15)
@pytest.mark.subprocess_e2e
def test_install_then_auto_commit_replaces_stale_bundled_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    Repo.init(tmp_path)
    repo = Repo(tmp_path)
    repo.config_writer().set_value("user", "name", "Test Author").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()

    name = BASELINE_SKILL_NAMES[0]
    canonical = tmp_path / ".opencode" / "skills" / name
    canonical.mkdir(parents=True, exist_ok=True)
    stale_content = "# stale version from an earlier Ralph run\n"
    (canonical / "SKILL.md").write_text(stale_content, encoding="utf-8")
    stale_marker_sha = hashlib.sha256(stale_content.encode("utf-8")).hexdigest()
    (canonical / ".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": stale_marker_sha,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)

    monkeypatch.setattr(
        run_module, "install_project_baseline_skills", install_project_baseline_skills
    )

    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", lambda _r: None)
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_git_exclude", lambda _r: None)

    repo.index.add([".opencode"])
    repo.index.commit("initial stale commit")
    repo.close()

    pre_marker = json.loads((canonical / ".ralph-managed.json").read_text(encoding="utf-8"))
    assert pre_marker["installed_content_sha256"] == stale_marker_sha, (
        "Setup invariant: marker must carry the STALE sha before the run"
    )
    bundled_sha = hashlib.sha256(get_skill_content(name).encode("utf-8")).hexdigest()
    assert stale_marker_sha != bundled_sha, (
        "Setup invariant: stale marker sha must NOT match the bundled sha"
    )

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    on_disk = (canonical / "SKILL.md").read_text(encoding="utf-8")
    assert on_disk == get_skill_content(name), (
        "AC-02 (a): bundled SKILL.md content MUST overwrite stale on-disk content; "
        f"on-disk first 80 chars: {on_disk[:80]!r}"
    )

    post_marker = json.loads((canonical / ".ralph-managed.json").read_text(encoding="utf-8"))
    assert post_marker["installed_content_sha256"] == bundled_sha, (
        "AC-02 (b): marker MUST be rewritten with the correct bundled sha; "
        f"got: {post_marker['installed_content_sha256']!r}, expected: {bundled_sha!r}"
    )
    assert post_marker["installed_content_sha256"] != stale_marker_sha, (
        "AC-02 (b): marker MUST NOT retain the stale sha"
    )

    repo = Repo(tmp_path)
    try:
        head_message = repo.head.commit.message
        head_subject = head_message.splitlines()[0]
        head_body = "\n".join(head_message.splitlines()[2:])
        assert head_subject == "chore(skills): sync baseline bundle", (
            f"AC-02 (c): auto-commit subject must be deterministic; got: {head_subject!r}"
        )
        assert "Auto-generated by Ralph skill sync" in head_body, (
            f"AC-02 (c): auto-commit body must contain the auto-gen header; got: {head_body!r}"
        )
        assert "Changed skills:" in head_body, (
            f"AC-02 (c): auto-commit body must contain the 'Changed skills:' section; "
            f"got: {head_body!r}"
        )
        assert f"- {name}" in head_body, (
            f"AC-02 (c): auto-commit body must list the changed skill name `{name}`; "
            f"got: {head_body!r}"
        )
    finally:
        repo.close()

    repo = Repo(tmp_path)
    try:
        for prefix in sorted(_SKILL_ROOT_PREFIXES):
            assert not repo.is_dirty(path=prefix, untracked_files=True), (
                f"AC-02 (d): working tree MUST be clean across the FIVE "
                f"skill-root prefixes; prefix {prefix!r} reports dirty"
            )
    finally:
        repo.close()


@pytest.mark.timeout_seconds(15)
@pytest.mark.subprocess_e2e
def test_skill_sync_autocommits_before_agent_sees_skill_tree_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    Repo.init(tmp_path)
    repo = Repo(tmp_path)
    repo.config_writer().set_value("user", "name", "Test Author").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()

    name = BASELINE_SKILL_NAMES[0]
    canonical = tmp_path / ".opencode" / "skills" / name
    canonical.mkdir(parents=True, exist_ok=True)
    stale_content = "# stale version from an earlier Ralph run\n"
    (canonical / "SKILL.md").write_text(stale_content, encoding="utf-8")
    stale_marker_sha = "deadbeef" * 8
    (canonical / ".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": stale_marker_sha,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)

    monkeypatch.setattr(
        run_module, "install_project_baseline_skills", install_project_baseline_skills
    )

    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", lambda _r: None)
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_git_exclude", lambda _r: None)

    repo.index.add([".opencode"])
    repo.index.commit("initial stale commit")
    repo.close()

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    repo = Repo(tmp_path)
    try:
        head_subject = repo.head.commit.message.splitlines()[0]
    finally:
        repo.close()
    assert head_subject == "chore(skills): sync baseline bundle", (
        f"AC-05 (1): auto-commit subject must be deterministic BEFORE the "
        f"agent sees the worktree; got: {head_subject!r}"
    )

    repo = Repo(tmp_path)
    try:
        for prefix in sorted(_SKILL_ROOT_PREFIXES):
            assert not repo.is_dirty(path=prefix, untracked_files=True), (
                f"AC-05 (2): agent's working tree MUST be CLEAN across the "
                f"FIVE skill-root prefixes; prefix {prefix!r} reports dirty"
            )
    finally:
        repo.close()

    captured: list[str] = []
    sink_id = logger.add(captured.append, level="DEBUG", format="{message}")
    try:

        def _is_agent_internal_path(p: str) -> bool:
            return p.startswith(".agent/")

        untracked = untrack_engine_internal_files(tmp_path, _is_agent_internal_path)
    finally:
        logger.remove(sink_id)

    for prefix in _SKILL_ROOT_PREFIXES:
        offending = [p for p in untracked if p.startswith(prefix)]
        assert not offending, (
            f"AC-05 (3): no FIVE-root path MUST be untracked under {prefix!r}; got: {offending!r}"
        )

    offending_warnings = [
        msg
        for msg in captured
        if "Refusing to git rm --cached symlink under tracked engine-internal path" in msg
    ]
    assert offending_warnings == [], (
        f"AC-05 (4): ZERO WARNING lines for tracked skill-root paths; got: {offending_warnings!r}"
    )

    early_skip_msgs = [msg for msg in captured if "Skipping tracked skill-root path" in msg]
    assert early_skip_msgs, (
        f"AC-05 (5): AT LEAST ONE captured DEBUG message MUST contain "
        f"'Skipping tracked skill-root path' (positive early-skip evidence); "
        f"got captured: {captured!r}"
    )


@pytest.mark.timeout_seconds(3)
def test_run_pipeline_threads_canonical_run_id_to_sweep(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical_run_id = "canonical-pipeline-run-id-deadbeef"
    monkeypatch.delenv("RALPH_BROKER_SECRET", raising=False)
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    workspace_scope_stub = MagicMock()
    workspace_scope_stub.root = tmp_path

    load_result = _LoadResult(
        config=MagicMock(),
        workspace_scope=workspace_scope_stub,
        initial_state=None,
        policy_bundle=None,
        run_id=canonical_run_id,
    )

    monkeypatch.setattr(run_module, "_load_configuration", lambda *_a, **_kw: load_result)

    def _fake_preflight(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(run_module, "_run_preflight_checks", _fake_preflight)

    sync_mock = MagicMock()
    monkeypatch.setattr(run_module, "_sync_shipped_skills_on_pipeline_run", sync_mock)
    monkeypatch.setattr(run_module, "_warn_if_capabilities_degraded", lambda *_a, **_kw: None)

    captured_keep_run_id: list[object] = []

    def _capture(**kwargs: object) -> None:
        captured_keep_run_id.append(kwargs.get("keep_run_id"))

    sync_mock.side_effect = _capture

    result = run_module.run_pipeline(dry_run=True)
    assert result == 0
    assert run_module.os.environ.get("RALPH_BROKER_SECRET")

    assert sync_mock.called, "production sweep call site must invoke the sweep"
    forwarded = sync_mock.call_args.kwargs.get("keep_run_id")
    assert forwarded == canonical_run_id, (
        f"Production sweep MUST forward the canonical load_result.run_id; "
        f"expected {canonical_run_id!r}, got {forwarded!r}"
    )
    assert forwarded is not None, (
        "Production sweep MUST NOT pass keep_run_id=None (RFC-013 P2 contract)"
    )

    assert captured_keep_run_id, "the production caller did not invoke the sweep"
    for value in captured_keep_run_id:
        assert value == canonical_run_id, (
            f"every sweep call MUST forward the canonical run_id; got {value!r}"
        )
