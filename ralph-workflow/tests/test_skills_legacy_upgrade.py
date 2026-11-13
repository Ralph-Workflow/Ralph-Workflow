from __future__ import annotations

import contextlib
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

import pytest

from ralph.skills.manager import SkillManager
from ralph.skills.upstream import UNIVERSAL_SKILL_NAMES
from tests.test_skills_install_auto_commit import (
    _REPO_CACHE,
    _commit_all_initial,
    _commit_worktree,
    _init_repo,
    _open_repo,
    _plant_managed_skill,
    _plant_unmanaged_skill,
)

pytestmark = [
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(15),
]

_USER_GLOBAL_ROOT_SEGMENTS: tuple[tuple[str, ...], ...] = (
    (".claude", "skills"),
    (".codex", "skills"),
    (".config", "opencode", "skills"),
    (".gemini", "antigravity-cli", "skills"),
    (".agents", "skills"),
    (".gemini", "config", "skills"),
)

_PROJECT_SIBLING_ROOT_SEGMENTS: tuple[tuple[str, ...], ...] = (
    (".claude", "skills"),
    (".codex", "skills"),
    (".gemini", "antigravity-cli", "skills"),
    (".agents", "skills"),
)

_SKILL_SYNC_COMMIT_SUBJECT = "chore(skills): sync baseline bundle"


@pytest.fixture(autouse=True)
def _close_cached_repositories() -> Iterator[None]:
    yield
    for repo in list(_REPO_CACHE.values()):
        with contextlib.suppress(Exception):
            repo.close()
    _REPO_CACHE.clear()


def _artifact_skill_names() -> tuple[str, ...]:
    content_dir = files("ralph.skills").joinpath("content")
    return tuple(
        sorted(
            path.name.removesuffix(".md")
            for path in content_dir.iterdir()
            if path.is_file() and path.name.endswith(".md")
        )
    )


def _use_full_artifact_skill_set(monkeypatch: pytest.MonkeyPatch) -> tuple[str, ...]:
    names = _artifact_skill_names()
    for module in (
        "ralph.skills._content",
        "ralph.skills._installer",
        "ralph.skills._installer_candidates",
        "ralph.skills._prune",
    ):
        monkeypatch.setattr(f"{module}.BASELINE_SKILL_NAMES", names)
    return names


def _installed_skill_names(root: Path) -> set[str]:
    return {
        entry.name for entry in root.iterdir() if entry.is_dir() and (entry / "SKILL.md").is_file()
    }


def test_fresh_project_install_contains_only_the_packaged_artifact_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_names = set(_use_full_artifact_skill_set(monkeypatch))
    home = tmp_path / "home"
    home.mkdir()

    with patch("pathlib.Path.home", return_value=home):
        manager = SkillManager(state_path=tmp_path / "state.json")
        _state, failures = manager.reinstall_baseline_skills(workspace_root=tmp_path)

    assert failures == [], f"a fresh install must not report failures, got {failures!r}"
    project_roots = (
        tmp_path / ".opencode" / "skills",
        *(tmp_path.joinpath(*segments) for segments in _PROJECT_SIBLING_ROOT_SEGMENTS),
    )
    for root in project_roots:
        installed_names = _installed_skill_names(root)
        assert installed_names == expected_names, (
            f"fresh install must contain exactly the packaged artifact skills in {root}"
        )
        assert not set(UNIVERSAL_SKILL_NAMES) & installed_names, (
            f"fresh project install must not contain universal skills in {root}"
        )


def test_upgrade_from_legacy_managed_bundle_prunes_globally_and_commits_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_names = _use_full_artifact_skill_set(monkeypatch)
    legacy_names = (
        "brainstorming",
        "writing-plans",
        "my-edited-skill",
        *(f"legacy-skill-{index:02d}" for index in range(26)),
    )
    assert len(legacy_names) == 29
    assert set(legacy_names).isdisjoint(artifact_names)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    _init_repo(repo_root)
    _commit_all_initial(repo_root)

    user_names = {"my-user-skill", "my-edited-skill"}
    with patch("pathlib.Path.home", return_value=home):
        project_roots = tuple(
            repo_root.joinpath(*segments) for segments in _PROJECT_SIBLING_ROOT_SEGMENTS
        )
        project_roots += (repo_root / ".opencode" / "skills",)
        user_roots = tuple(home.joinpath(*segments) for segments in _USER_GLOBAL_ROOT_SEGMENTS)
        for root in (*project_roots, *user_roots):
            for name in legacy_names:
                _plant_managed_skill(root, name, f"# legacy {name}\n")
            (root / "my-edited-skill" / "SKILL.md").write_text(
                "# my edited skill\n",
                encoding="utf-8",
            )
            _plant_unmanaged_skill(root, "my-user-skill")

        seed_head = _commit_worktree(repo_root, "seed legacy managed skills")
        manager = SkillManager(state_path=tmp_path / "state.json")
        _state, failures = manager.reinstall_baseline_skills(workspace_root=repo_root)

        expected_names = set(artifact_names) | user_names
        for root in (*project_roots, *user_roots):
            assert _installed_skill_names(root) == expected_names, (
                f"upgrade must leave only artifact skills and untouched user skills in {root}"
            )
            assert not set(UNIVERSAL_SKILL_NAMES) & _installed_skill_names(root)

    assert "skills-prune-preserved-my-edited-skill" in failures

    repo = _open_repo(repo_root)
    try:
        new_commits = []
        for commit in repo.iter_commits():
            if commit.hexsha == seed_head:
                break
            new_commits.append(commit)
        assert len(new_commits) == 1, (
            f"upgrade must produce exactly one skill-sync commit; got {len(new_commits)}: "
            f"{[(c.hexsha[:8], c.message.splitlines()[0]) for c in new_commits]}"
        )
        assert new_commits[0].message.splitlines()[0] == _SKILL_SYNC_COMMIT_SUBJECT
        first_upgrade_head = repo.head.commit.hexsha
    finally:
        repo.close()

    with patch("pathlib.Path.home", return_value=home):
        manager.reinstall_baseline_skills(workspace_root=repo_root)

    repo = _open_repo(repo_root)
    try:
        assert repo.head.commit.hexsha == first_upgrade_head, (
            "a second upgrade with no changes must not create another commit"
        )
    finally:
        repo.close()
