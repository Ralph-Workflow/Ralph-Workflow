"""Tests for _sync_shipped_skills_on_pipeline_run in run.py.

These tests are subprocess_e2e: they exercise the real
``_sync_shipped_skills_on_pipeline_run`` entry point and its full
filesystem + git + auto-commit path. They cannot be mocked down to
the per-test 1 s budget without losing the end-to-end contract they
assert.
"""

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
from ralph.git.commit_cleanup import untrack_engine_internal_files
from ralph.skills._agent_paths import _SKILL_ROOT_PREFIXES
from ralph.skills._content import BASELINE_SKILL_NAMES, get_skill_content
from ralph.skills._installer import install_project_baseline_skills

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.timeout_seconds(15), pytest.mark.subprocess_e2e]


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
@pytest.mark.subprocess_e2e
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


@pytest.mark.timeout_seconds(15)
@pytest.mark.subprocess_e2e
def test_install_then_auto_commit_replaces_stale_bundled_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """wt-025 / AC-02: full install + auto-commit path with stale bundled content.

    Mirrors ``test_sync_shipped_skills_creates_auto_commit_on_dirty_skill_tree``
    but with the on-disk SKILL.md pre-staged with a STALE bundled-content
    SHA. The test runs the REAL ``install_project_baseline_skills`` and
    the REAL ``commit_skill_updates`` (no MagicMock) and pins the four
    end-to-end contracts the audit covers:

      (a) The on-disk ``SKILL.md`` equals ``get_skill_content(name)``
          (the bundled content wins -- ``_materialize_canonical_skill``
          overwrites the stale copy).

      (b) The on-disk ``.ralph-managed.json`` marker has
          ``installed_content_sha256 == hashlib.sha256(
              get_skill_content(name).encode()).hexdigest()``
          (the marker is rewritten with the CORRECT sha, not the stale
          one).

      (c) The repo's HEAD commit subject is the literal
          ``chore(skills): sync baseline bundle`` AND the body lists
          the overwritten skill under ``Changed skills:`` (the
          auto-commit captured the overwrite).

      (d) ``git status --porcelain -- <each FIVE prefix>`` returns
          empty bytes -- the working tree is CLEAN across all FIVE
          canonical skill-root prefixes after the commit.

    This is a NEW regression test added by the wt-025 plan. It pins
    AC-02 at the file-system layer; without it, a refactor of
    ``_materialize_canonical_skill`` could silently regress the
    overwrite-on-stale-bundled-sha branch.
    """
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    # 1. Initialize a fresh git repo at tmp_path with a baseline commit
    #    containing a STALE ``.opencode/skills/<name>/SKILL.md`` AND a
    #    STALE marker. The marker sha is a 64-char hex string that does
    #    NOT match the bundled content -- this is the canonical
    #    conflict-replacement trigger.
    Repo.init(tmp_path)
    repo = Repo(tmp_path)
    repo.config_writer().set_value("user", "name", "Test Author").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()

    name = BASELINE_SKILL_NAMES[0]
    canonical = tmp_path / ".opencode" / "skills" / name
    canonical.mkdir(parents=True, exist_ok=True)
    stale_content = "# stale version from an earlier Ralph run\n"
    (canonical / "SKILL.md").write_text(stale_content, encoding="utf-8")
    # 64-hex-char sha that does NOT match the bundled content -- the
    # canonical 'stale bundled SHA' marker shape.
    stale_marker_sha = "deadbeef" * 8  # 64 hex chars
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

    # 2. Patch SkillManager so user-global is a no-op (signal-only)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    # 3. Force the project-scope install to run
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)

    # 4. Real install_project_baseline_skills (not mocked)
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

    # Pre-flight sanity: the on-disk marker carries the STALE sha BEFORE the run.
    pre_marker = json.loads(
        (canonical / ".ralph-managed.json").read_text(encoding="utf-8")
    )
    assert pre_marker["installed_content_sha256"] == stale_marker_sha, (
        "Setup invariant: marker must carry the STALE sha before the run"
    )
    bundled_sha = hashlib.sha256(get_skill_content(name).encode("utf-8")).hexdigest()
    assert stale_marker_sha != bundled_sha, (
        "Setup invariant: stale marker sha must NOT match the bundled sha"
    )

    # 7. Run the full pipeline (commit_skill_updates + create_commit are REAL)
    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    # 8a. On-disk SKILL.md MUST equal the bundled content -- bundled content wins.
    on_disk = (canonical / "SKILL.md").read_text(encoding="utf-8")
    assert on_disk == get_skill_content(name), (
        "AC-02 (a): bundled SKILL.md content MUST overwrite stale on-disk content; "
        f"on-disk first 80 chars: {on_disk[:80]!r}"
    )

    # 8b. On-disk marker MUST be rewritten with the CORRECT bundled sha.
    post_marker = json.loads(
        (canonical / ".ralph-managed.json").read_text(encoding="utf-8")
    )
    assert post_marker["installed_content_sha256"] == bundled_sha, (
        "AC-02 (b): marker MUST be rewritten with the correct bundled sha; "
        f"got: {post_marker['installed_content_sha256']!r}, expected: {bundled_sha!r}"
    )
    assert post_marker["installed_content_sha256"] != stale_marker_sha, (
        "AC-02 (b): marker MUST NOT retain the stale sha"
    )

    # 8c. HEAD commit subject MUST be deterministic; body MUST list the skill.
    repo = Repo(tmp_path)
    try:
        head_message = repo.head.commit.message
        head_subject = head_message.splitlines()[0]
        head_body = "\n".join(head_message.splitlines()[2:])
        assert head_subject == "chore(skills): sync baseline bundle", (
            f"AC-02 (c): auto-commit subject must be deterministic; got: {head_subject!r}"
        )
        assert "Auto-generated by Ralph skill sync" in head_body, (
            f"AC-02 (c): auto-commit body must contain the auto-gen header; "
            f"got: {head_body!r}"
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

    # 8d. Working tree MUST be clean across all FIVE skill-root prefixes.
    # Use ``Repo.is_dirty(path=...)`` from GitPython rather than spawning
    # ``git status`` -- the audit forbids direct subprocess calls in tests
    # (``tests/test_process_audit.py::test_no_direct_subprocess_calls_in_tests``)
    # and ``Repo.is_dirty`` returns True when the path contains any tracked
    # modifications, untracked files, or deletions -- which is exactly the
    # ``git status --porcelain`` contract.
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
    """wt-025 / AC-05 (PA-001 closure): pre-pipeline sync leaves the agent's
    working tree CLEAN.

    Pins the implied pre-pipeline sync + agent-clean-worktree invariant
    from PROMPT.md. The development agent MUST NOT see the skill-tree


    drift at runtime; ``_sync_shipped_skills_on_pipeline_run`` runs as
    Phase 2b BEFORE the agent's commit_cleanup phase and MUST land an
    auto-commit that resolves the drift.

    The test drives the FULL pipeline ordering: a pre-dirty skill tree
    is installed, ``_sync_shipped_skills_on_pipeline_run`` runs, then
    the test simulates the agent's commit_cleanup phase by calling
    ``untrack_engine_internal_files`` with a DEBUG-level loguru sink
    (so both the early-skip DEBUG lines AND any WARNING lines are
    observable).

    Asserts:

      (1) ``repo.head.commit.message.splitlines()[0] == 'chore(skills): sync baseline bundle'``
          -- the auto-commit landed on HEAD BEFORE the agent sees the worktree.

      (2) ``git status --porcelain -- <each FIVE prefix>`` returns
          empty bytes -- the FIVE canonical skill roots are CLEAN.

      (3) ``untrack_engine_internal_files`` returns ``[]`` for any path
          under the FIVE prefixes -- the early-skip fired for the FIVE-root
          symlinks (if any) or canonical files (if any).

      (4) NO captured log message matches
          ``Refusing to git rm --cached symlink under tracked engine-internal path``
          -- ZERO WARNING noise from the agent's commit_cleanup phase.

      (5) AT LEAST ONE captured DEBUG message contains
          ``Skipping tracked skill-root path`` -- the FIVE-root early-skip
          block fired (only required when the test scenario includes a
          tracked FIVE-root path; the fresh git repo scenario may not
          produce one, so this assertion is conditional on the test
          setup including a tracked symlink).
    """
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    # 1. Initialize a fresh git repo with a baseline commit containing a
    #    stale ``.opencode/skills/<name>/SKILL.md`` AND a stale marker.
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

    # 2. Patch SkillManager so user-global is a no-op (signal-only)
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    # 3. Force the project-scope install to run
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)

    # 4. Real install_project_baseline_skills + real commit_skill_updates
    monkeypatch.setattr(
        run_module, "install_project_baseline_skills", install_project_baseline_skills
    )

    # 5. Patch the gitignore/exclude auto-seeders
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", lambda _r: None)
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_git_exclude", lambda _r: None)

    # 6. Initial commit
    repo.index.add([".opencode"])
    repo.index.commit("initial stale commit")
    repo.close()

    # 7. Run the pre-pipeline sync -- this is the EXACT pipeline Phase 2b ordering.
    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    # Assertion (1): HEAD subject MUST be the deterministic auto-commit subject.
    repo = Repo(tmp_path)
    try:
        head_subject = repo.head.commit.message.splitlines()[0]
    finally:
        repo.close()
    assert head_subject == "chore(skills): sync baseline bundle", (
        f"AC-05 (1): auto-commit subject must be deterministic BEFORE the "
        f"agent sees the worktree; got: {head_subject!r}"
    )

    # Assertion (2): working tree MUST be clean across the FIVE skill-root prefixes.
    # Use ``Repo.is_dirty(path=...)`` from GitPython rather than spawning
    # ``git status`` -- the audit forbids direct subprocess calls in tests
    # (``tests/test_process_audit.py::test_no_direct_subprocess_calls_in_tests``)
    # and ``Repo.is_dirty`` returns True when the path contains any tracked
    # modifications, untracked files, or deletions -- which is exactly the
    # ``git status --porcelain`` contract.
    repo = Repo(tmp_path)
    try:
        for prefix in sorted(_SKILL_ROOT_PREFIXES):
            assert not repo.is_dirty(path=prefix, untracked_files=True), (
                f"AC-05 (2): agent's working tree MUST be CLEAN across the "
                f"FIVE skill-root prefixes; prefix {prefix!r} reports dirty"
            )
    finally:
        repo.close()

    # Assertion (3) + (4) + (5): simulate the agent's commit_cleanup phase
    # by calling untrack_engine_internal_files with a DEBUG-level sink so
    # we can observe BOTH the early-skip DEBUG messages AND any WARNING
    # messages. The canonical predicate accepts any path under ``.agent/``.
    captured: list[str] = []
    sink_id = logger.add(captured.append, level="DEBUG", format="{message}")
    try:
        def _is_agent_internal_path(p: str) -> bool:
            return p.startswith(".agent/")

        untracked = untrack_engine_internal_files(tmp_path, _is_agent_internal_path)
    finally:
        logger.remove(sink_id)

    # Assertion (3): no FIVE-root path was untracked (the early-skip ran).
    for prefix in _SKILL_ROOT_PREFIXES:
        offending = [p for p in untracked if p.startswith(prefix)]
        assert not offending, (
            f"AC-05 (3): no FIVE-root path MUST be untracked under {prefix!r}; "
            f"got: {offending!r}"
        )

    # Assertion (4): ZERO WARNING noise for FIVE-root symlinks.
    offending_warnings = [
        msg
        for msg in captured
        if "Refusing to git rm --cached symlink under tracked engine-internal path" in msg
    ]
    assert offending_warnings == [], (
        f"AC-05 (4): ZERO WARNING lines for tracked skill-root paths; "
        f"got: {offending_warnings!r}"
    )

    # Assertion (5): positive evidence that the FIVE-root early-skip fired.
    # The test scenario commits ``.opencode/skills/<name>/SKILL.md`` and
    # ``.opencode/skills/<name>/.ralph-managed.json`` in the initial commit,
    # so both paths are tracked when ``untrack_engine_internal_files``
    # iterates ``repo.index.entries``. The early-skip block MUST emit at
    # least one DEBUG message containing ``Skipping tracked skill-root path``
    # -- this is the positive pin the PA-fix-2 audit asked for. A WARNING-
    # level sink would silently drop this DEBUG message (the PA-005 bug),
    # so this test installs a SINGLE DEBUG-level sink.
    early_skip_msgs = [
        msg
        for msg in captured
        if "Skipping tracked skill-root path" in msg
    ]
    assert early_skip_msgs, (
        f"AC-05 (5): AT LEAST ONE captured DEBUG message MUST contain "
        f"'Skipping tracked skill-root path' (positive early-skip evidence); "
        f"got captured: {captured!r}"
    )


@pytest.mark.timeout_seconds(3)
@pytest.mark.subprocess_e2e
def test_run_pipeline_threads_canonical_run_id_to_sweep(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression for the analysis feedback: ``run_pipeline`` MUST forward
    the canonical run identifier produced by ``_load_configuration`` into
    ``_sync_shipped_skills_on_pipeline_run`` as ``keep_run_id``.

    The previous implementation generated a fresh ``uuid.uuid4().hex`` at
    the call site, which meant the sweep never matched the real receipts
    or completion sentinels written by downstream bridges. This test
    patches ``_load_configuration`` to return a ``_LoadResult`` carrying
    a known ``run_id``, captures the ``keep_run_id`` argument the
    pipeline forwards, and asserts the forwarded value equals the
    canonical ``run_id`` byte-for-byte (NOT a freshly-generated UUID).

    Pins: wt-029 / ANALYSIS-001 / how_to_fix (run_id wiring).
    """
    canonical_run_id = "canonical-pipeline-run-id-deadbeef"
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

    result = run_module.run_pipeline(dry_run=True, inline_prompt="quick canonical run")
    assert result == 0

    assert sync_mock.called, "production sweep call site must invoke the sweep"
    forwarded = sync_mock.call_args.kwargs.get("keep_run_id")
    assert forwarded == canonical_run_id, (
        f"Production sweep MUST forward the canonical load_result.run_id; "
        f"expected {canonical_run_id!r}, got {forwarded!r}"
    )
    assert forwarded is not None, (
        "Production sweep MUST NOT pass keep_run_id=None (RFC-013 P2 contract)"
    )

    # Snapshot the captured keep_run_id across every call; the production
    # caller must be consistent.
    assert captured_keep_run_id, "the production caller did not invoke the sweep"
    for value in captured_keep_run_id:
        assert value == canonical_run_id, (
            f"every sweep call MUST forward the canonical run_id; got {value!r}"
        )
