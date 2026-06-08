"""Black-box tests for the sibling-root symlinking in ralph.skills._installer."""

from __future__ import annotations

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

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def fake_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[Path, tuple[AgentSkillRoot, ...]]]:
    """Redirect every agent skill root under tmp_path and yield (canonical, all)."""
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
            assert entry_path.is_symlink(), (
                f"Expected {entry_path} to be a symlink"
            )
            # Resolving through the symlink should land in the canonical root.
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

    # Force Path.symlink_to to raise OSError for any call so we exercise the
    # shutil.copytree fallback path.
    def _raise_symlink(
        self: Path, target: object, *args: object, **kwargs: object
    ) -> None:
        del target, args, kwargs
        raise OSError("simulated symlink unsupported")

    monkeypatch.setattr(type(Path()), "symlink_to", _raise_symlink)

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
    # shutil.copytree was used; canonical install still happened.
    for name in BASELINE_SKILL_NAMES:
        assert (canonical_dir / name / "SKILL.md").exists()


def test_install_baseline_skills_preserves_user_owned_sibling_skill(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
) -> None:
    _, all_roots = fake_roots
    codex_dir = all_roots[1].resolve()
    user_skill = codex_dir / "using-superpowers"
    user_skill.mkdir(parents=True)
    user_md = user_skill / "SKILL.md"
    user_md.write_text("# user override\n", encoding="utf-8")

    entry, failures = install_baseline_skills()
    assert entry.status.value == "needs_repair"
    assert "sibling-conflict-using-superpowers" in failures
    # The user file is preserved.
    assert user_md.read_text(encoding="utf-8") == "# user override\n"


def test_install_baseline_skills_replaces_existing_managed_symlink(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
    tmp_path: Path,
) -> None:
    canonical_dir, all_roots = fake_roots
    codex_dir = all_roots[1].resolve()
    stale_target = tmp_path / "stale-canonical"
    stale_target.mkdir(parents=True)
    # Pre-create a stale symlink that points nowhere useful.
    (codex_dir / "using-superpowers").symlink_to(stale_target, target_is_directory=True)

    entry, failures = install_baseline_skills()
    assert entry.status.value == "installed_healthy"
    assert failures == []
    entry_path = codex_dir / "using-superpowers"
    assert entry_path.is_symlink()
    assert entry_path.resolve() == (canonical_dir / "using-superpowers").resolve()


def test_install_baseline_skills_replaces_existing_real_directory_at_sibling(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
) -> None:
    """Regression guard: a real (non-symlink) directory at the sibling path must
    be rmtree'd and replaced with a fresh symlink.
    """
    canonical_dir, all_roots = fake_roots
    codex_dir = all_roots[1].resolve()
    user_skill = codex_dir / "using-superpowers"
    user_skill.mkdir(parents=True)
    (user_skill / "stale.txt").write_text("old", encoding="utf-8")

    entry, failures = install_baseline_skills()
    assert entry.status.value == "installed_healthy"
    assert failures == []
    entry_path = codex_dir / "using-superpowers"
    # The directory was replaced — it is now a symlink, not a real dir.
    assert entry_path.is_symlink()
    # Resolves into the canonical copy.
    assert entry_path.resolve() == (canonical_dir / "using-superpowers").resolve()
    # The stale file is gone (replaced by the symlink target, not a leftover real dir).
    assert not (canonical_dir / "using-superpowers" / "stale.txt").exists()


def test_install_baseline_skills_check_update_iterates_all_roots(
    fake_roots: tuple[Path, tuple[AgentSkillRoot, ...]],
) -> None:
    _canonical_dir, all_roots = fake_roots
    install_baseline_skills()
    # Sanity: nothing missing yet.
    assert check_skills_update_available() is False

    # Delete a sibling root entirely.
    codex_dir = all_roots[1].resolve()
    shutil.rmtree(codex_dir)
    assert check_skills_update_available() is True
