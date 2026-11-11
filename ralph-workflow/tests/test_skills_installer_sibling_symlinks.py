from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.skills._agent_paths import AgentSkillRoot
from ralph.skills._content import BASELINE_SKILL_NAMES, get_skill_content
from ralph.skills._installer import (
    check_skills_update_available,
    install_baseline_skills,
)

pytestmark = pytest.mark.timeout_seconds(5)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def fake_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[Path, tuple[AgentSkillRoot, ...]]]:
    canonical_dir = tmp_path / "canonical"
    codex_dir = tmp_path / "codex"
    opencode_dir = tmp_path / "opencode"
    agy_dir = tmp_path / "agy"
    for d in (canonical_dir, codex_dir, opencode_dir, agy_dir):
        d.mkdir(parents=True, exist_ok=True)

    fake_roots_tuple = (
        AgentSkillRoot(
            agent="claude",
            path_segments=(str(canonical_dir),),
            source_url="",
            is_canonical=True,
        ),
        AgentSkillRoot(
            agent="codex",
            path_segments=(str(codex_dir),),
            source_url="",
            is_canonical=False,
        ),
        AgentSkillRoot(
            agent="opencode",
            path_segments=(str(opencode_dir),),
            source_url="",
            is_canonical=False,
        ),
        AgentSkillRoot(
            agent="agy",
            path_segments=(str(agy_dir),),
            source_url="",
            is_canonical=False,
        ),
    )

    monkeypatch.setattr(
        "ralph.skills._installer.agent_skill_roots",
        lambda: fake_roots_tuple,
    )
    monkeypatch.setattr(
        "ralph.skills._installer.canonical_agent_skill_root",
        lambda: fake_roots_tuple[0],
    )
    monkeypatch.setattr(
        "ralph.skills._installer.sibling_agent_skill_roots",
        lambda: fake_roots_tuple[1:],
    )
    yield canonical_dir, fake_roots_tuple


def test_install_baseline_skills_writes_canonical_root(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
) -> None:
    canonical_dir, _ = fake_roots
    entry, failures = install_baseline_skills()
    assert entry.status.value == "installed_healthy"
    assert failures == []
    for name in BASELINE_SKILL_NAMES:
        assert (canonical_dir / name / "SKILL.md").exists()


def test_install_baseline_skills_creates_symlink_in_sibling_roots(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
) -> None:
    canonical_dir, all_roots = fake_roots
    install_baseline_skills()
    for sibling in all_roots[1:]:
        sibling_dir = sibling.resolve()
        for name in BASELINE_SKILL_NAMES:
            entry_path = sibling_dir / name
            assert entry_path.is_symlink(), f"Expected {entry_path} to be a symlink"
            target = entry_path.resolve()
            expected = (canonical_dir / name).resolve()
            assert target == expected, (
                f"Sibling symlink for {sibling.agent}/{name} resolves to {target}, "
                f"expected {expected}"
            )


def test_install_baseline_skills_falls_back_to_copy_when_symlink_fails(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_dir, all_roots = fake_roots

    real_symlink = os.symlink
    sibling_dirs = tuple(str(root.resolve()) for root in all_roots[1:])

    def _fail_sibling_symlink(
        src: str | Path,
        dst: str | Path,
        target_is_directory: bool = False,
    ) -> None:
        if any(str(dst).startswith(sibling_dir) for sibling_dir in sibling_dirs):
            raise OSError("simulated symlink unsupported")
        real_symlink(src, dst, target_is_directory)

    monkeypatch.setattr(os, "symlink", _fail_sibling_symlink)

    entry, failures = install_baseline_skills()
    assert entry.status.value == "installed_healthy"
    assert failures == []
    for sibling in all_roots[1:]:
        sibling_dir = sibling.resolve()
        for name in BASELINE_SKILL_NAMES:
            entry_path = sibling_dir / name
            assert entry_path.is_dir()
            assert not entry_path.is_symlink()
            assert (entry_path / "SKILL.md").read_text(encoding="utf-8") == get_skill_content(name)
    for name in BASELINE_SKILL_NAMES:
        assert (canonical_dir / name / "SKILL.md").exists()


def test_install_baseline_skills_preserves_user_owned_sibling_skill(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
) -> None:
    _, all_roots = fake_roots
    codex_dir = all_roots[1].resolve()
    user_skill = codex_dir / "submit-plan-artifact"
    user_skill.mkdir(parents=True)
    user_md = user_skill / "SKILL.md"
    user_md.write_text("# user override\n", encoding="utf-8")

    entry, failures = install_baseline_skills()
    assert entry.status.value == "needs_repair"
    assert "sibling-conflict-submit-plan-artifact" in failures
    assert user_md.read_text(encoding="utf-8") == "# user override\n"


def test_install_baseline_skills_replaces_existing_managed_symlink(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
    tmp_path: Path,
) -> None:
    canonical_dir, all_roots = fake_roots
    codex_dir = all_roots[1].resolve()
    stale_target = tmp_path / "stale-canonical"
    stale_target.mkdir(parents=True)
    (codex_dir / "submit-plan-artifact").symlink_to(stale_target, target_is_directory=True)

    entry, failures = install_baseline_skills()
    assert entry.status.value == "installed_healthy"
    assert failures == []
    entry_path = codex_dir / "submit-plan-artifact"
    assert entry_path.is_symlink()
    assert entry_path.resolve() == (canonical_dir / "submit-plan-artifact").resolve()


def test_install_baseline_skills_replaces_existing_real_directory_at_sibling(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
) -> None:
    canonical_dir, all_roots = fake_roots
    codex_dir = all_roots[1].resolve()
    user_skill = codex_dir / "submit-plan-artifact"
    user_skill.mkdir(parents=True)
    (user_skill / "stale.txt").write_text("old", encoding="utf-8")

    entry, failures = install_baseline_skills()
    assert entry.status.value == "installed_healthy"
    assert failures == []
    entry_path = codex_dir / "submit-plan-artifact"
    assert entry_path.is_symlink()
    assert entry_path.resolve() == (canonical_dir / "submit-plan-artifact").resolve()
    assert not (canonical_dir / "submit-plan-artifact" / "stale.txt").exists()


def test_install_baseline_skills_check_update_iterates_all_roots(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
) -> None:
    _canonical_dir, all_roots = fake_roots
    install_baseline_skills()
    assert check_skills_update_available() is False

    codex_dir = all_roots[1].resolve()
    shutil.rmtree(codex_dir)
    assert check_skills_update_available() is True
