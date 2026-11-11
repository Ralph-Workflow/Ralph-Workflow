"""Tests for ralph.skills._installer update checks and retired-skill pruning.

The migration cases below pin the SOURCE-AWARE, HASH-AWARE prune contract:
a retired skill directory is removed ONLY when its managed marker proves
Ralph manages it AND its complete content still matches the digest the
marker recorded. Every other shape is preserved, and the ones the prune
cannot prove pristine are reported through the installer's failure list.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING
from unittest.mock import patch

from ralph.skills._agent_paths import AgentSkillRoot, agent_skill_roots
from ralph.skills._installer import (
    check_skills_update_available,
    install_baseline_skills,
    install_project_baseline_skills,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


#: A skill the shipped bundle no longer contains (retired).
RETIRED_SKILL_NAME = "brainstorming"

#: The managed-marker file name every Ralph-managed skill directory carries.
MANAGED_MARKER_NAME = ".ralph-managed.json"

#: Wire value of the healthy capability status.
_INSTALLED_HEALTHY = "installed_healthy"

#: Wire value of the repair-needed capability status.
_NEEDS_REPAIR = "needs_repair"


def _registered_roots() -> tuple[Path, ...]:
    """Return every REGISTERED user-global skills root, canonical first.

    The list comes from the registry, so a newly registered agent root is
    covered without the test hard-coding a root count.
    """
    return tuple(root.resolve() for root in agent_skill_roots())


def _canonical_root() -> Path:
    """Return the registered canonical user-global skills root."""
    for root in agent_skill_roots():
        if root.is_canonical:
            return root.resolve()
    msg = "no canonical user-global skill root is registered"
    raise AssertionError(msg)


def _digest(content: str) -> str:
    """Return the sha256 of ``content`` encoded as UTF-8."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _plant_managed_skill(root: Path, name: str, *, content: str = "retired\n") -> Path:
    """Plant a retired Ralph-managed skill directory under ``root``.

    The marker carries the REAL prior shape: ``managed_by`` plus a recorded
    ``installed_content_sha256`` digest of the installed content.
    """
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    (skill_dir / MANAGED_MARKER_NAME).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": _digest(content),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return skill_dir


def test_check_skills_update_available_returns_true_when_dir_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: tmp_path / "missing-skills",
    )
    assert check_skills_update_available() is True


def test_check_skills_update_available_returns_false_when_contents_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    install_baseline_skills(target_dir=installed_dir)
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    # Restrict the check to the canonical Claude root so this test stays focused
    # on the "contents match -> no update" contract. The new "iterate every
    # registered root" behavior is covered separately in
    # tests/test_skills_installer_sibling_symlinks.py.
    fake_canonical = AgentSkillRoot(
        agent="claude",
        path_segments=(str(installed_dir),),
        source_url="",
        is_canonical=True,
    )

    monkeypatch.setattr(
        "ralph.skills._installer.agent_skill_roots",
        lambda: (fake_canonical,),
    )
    assert check_skills_update_available() is False


def test_check_skills_update_available_returns_true_when_content_differs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    install_baseline_skills(target_dir=installed_dir)
    (installed_dir / "submit-artifact" / "SKILL.md").write_text("changed", encoding="utf-8")
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    assert check_skills_update_available() is True


def test_check_skills_update_available_returns_true_when_metadata_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    install_baseline_skills(target_dir=installed_dir)
    metadata_path = installed_dir / "metadata.json"
    if metadata_path.exists():
        metadata_path.unlink()
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    assert check_skills_update_available() is True


def test_check_skills_update_available_returns_true_when_user_conflict_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    install_baseline_skills(target_dir=installed_dir)
    (installed_dir / "submit-artifact" / "SKILL.md").write_text(
        "# user override\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    assert check_skills_update_available() is True


def test_check_skills_update_available_returns_true_when_managed_marker_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    install_baseline_skills(target_dir=installed_dir)
    (installed_dir / "submit-artifact" / MANAGED_MARKER_NAME).unlink()
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    assert check_skills_update_available() is True


# --- Retired-skill migration: the source-aware, hash-aware prune ------------


def test_install_prunes_retired_managed_skill_at_every_registered_user_global_root(
    tmp_path: Path,
) -> None:
    """A retired managed skill is removed at EVERY registered user-global root.

    The root list comes from the ``agent_skill_roots()`` registry, so the
    case keeps covering newly registered agent roots without hard-coding a
    root count.
    """
    home = tmp_path / "home"
    home.mkdir()
    with patch("pathlib.Path.home", return_value=home):
        roots = _registered_roots()
        for root in roots:
            _plant_managed_skill(root, RETIRED_SKILL_NAME)
        entry, failures = install_baseline_skills()

    assert entry.status == _INSTALLED_HEALTHY, failures
    assert failures == []
    for root in roots:
        assert not (root / RETIRED_SKILL_NAME).exists(), (
            f"retired managed skill must be pruned at {root}"
        )


def test_install_preserves_user_edited_retired_managed_skill_and_reports_it(
    tmp_path: Path,
) -> None:
    """A user-edited retired managed skill survives the prune and is reported.

    The fixture is planted at EVERY registered user-global root with the
    REAL prior marker shape (``managed_by`` plus a recorded digest), so the
    case proves the digest-mismatch rule at every root, not just one.
    """
    home = tmp_path / "home"
    home.mkdir()
    with patch("pathlib.Path.home", return_value=home):
        skill_dirs = [
            _plant_managed_skill(root, RETIRED_SKILL_NAME) for root in _registered_roots()
        ]
        for skill_dir in skill_dirs:
            (skill_dir / "SKILL.md").write_text("# user edited\n", encoding="utf-8")
        entry, failures = install_baseline_skills()

    for skill_dir in skill_dirs:
        assert skill_dir.is_dir(), "a user-edited retired skill must never be deleted"
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# user edited\n"
    assert entry.status == _NEEDS_REPAIR
    assert f"skills-prune-preserved-{RETIRED_SKILL_NAME}" in failures


def test_install_preserves_unmarked_retired_skill_directory(tmp_path: Path) -> None:
    """A retired directory WITHOUT a managed marker is user-owned and survives."""
    home = tmp_path / "home"
    home.mkdir()
    with patch("pathlib.Path.home", return_value=home):
        skill_dir = _canonical_root() / RETIRED_SKILL_NAME
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# my own skill\n", encoding="utf-8")
        entry, failures = install_baseline_skills()

    assert skill_dir.is_dir(), "an unmarked directory must never be deleted"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# my own skill\n"
    assert entry.status == _INSTALLED_HEALTHY, failures
    assert failures == []


def test_install_preserves_and_reports_malformed_managed_marker(tmp_path: Path) -> None:
    """A retired directory with a malformed marker survives and is reported."""
    home = tmp_path / "home"
    home.mkdir()
    with patch("pathlib.Path.home", return_value=home):
        skill_dir = _canonical_root() / RETIRED_SKILL_NAME
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("retired\n", encoding="utf-8")
        (skill_dir / MANAGED_MARKER_NAME).write_text("not json at all\n", encoding="utf-8")
        entry, failures = install_baseline_skills()

    assert skill_dir.is_dir(), "a malformed marker must never authorise a delete"
    assert entry.status == _NEEDS_REPAIR
    assert f"skills-prune-unknown-{RETIRED_SKILL_NAME}" in failures


def test_install_preserves_and_reports_unknown_managed_by_marker(tmp_path: Path) -> None:
    """A marker whose ``managed_by`` is unknown survives and is reported."""
    home = tmp_path / "home"
    home.mkdir()
    with patch("pathlib.Path.home", return_value=home):
        skill_dir = _canonical_root() / RETIRED_SKILL_NAME
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("retired\n", encoding="utf-8")
        (skill_dir / MANAGED_MARKER_NAME).write_text(
            json.dumps({"managed_by": "some-other-tool", "skill": RETIRED_SKILL_NAME}) + "\n",
            encoding="utf-8",
        )
        entry, failures = install_baseline_skills()

    assert skill_dir.is_dir(), "an unknown marker owner must never authorise a delete"
    assert entry.status == _NEEDS_REPAIR
    assert f"skills-prune-unknown-{RETIRED_SKILL_NAME}" in failures


def test_install_preserves_and_reports_marker_with_inconsistent_aggregate(
    tmp_path: Path,
) -> None:
    """A new-style marker whose aggregate disagrees with its file map is reported.

    The marker's ``installed_sha256`` must be the sha256 over its recorded
    file map; a marker whose aggregate disagrees records an unusable digest,
    so the prune preserves the directory and reports it.
    """
    home = tmp_path / "home"
    home.mkdir()
    with patch("pathlib.Path.home", return_value=home):
        skill_dir = _canonical_root() / RETIRED_SKILL_NAME
        skill_dir.mkdir(parents=True)
        content = "retired\n"
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        (skill_dir / MANAGED_MARKER_NAME).write_text(
            json.dumps(
                {
                    "managed_by": "ralph-workflow",
                    "skill": RETIRED_SKILL_NAME,
                    "installed_sha256": "0" * 64,
                    "files": {"SKILL.md": _digest(content)},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        entry, failures = install_baseline_skills()

    assert skill_dir.is_dir(), "an inconsistent aggregate must never authorise a delete"
    assert entry.status == _NEEDS_REPAIR
    assert f"skills-prune-unknown-{RETIRED_SKILL_NAME}" in failures


def test_install_preserves_and_reports_managed_skill_with_companion_file(
    tmp_path: Path,
) -> None:
    """A managed directory with a user-added companion file survives and is reported.

    The legacy marker owns exactly ``SKILL.md``; any other file on disk is a
    user-added companion, so the directory is no longer purely managed. The
    fixture is planted at EVERY registered user-global root.
    """
    home = tmp_path / "home"
    home.mkdir()
    with patch("pathlib.Path.home", return_value=home):
        skill_dirs = [
            _plant_managed_skill(root, RETIRED_SKILL_NAME) for root in _registered_roots()
        ]
        for skill_dir in skill_dirs:
            (skill_dir / "notes.md").write_text("# my notes\n", encoding="utf-8")
        entry, failures = install_baseline_skills()

    for skill_dir in skill_dirs:
        assert skill_dir.is_dir(), "a managed directory with a companion file must survive"
        assert (skill_dir / "notes.md").read_text(encoding="utf-8") == "# my notes\n"
    assert entry.status == _NEEDS_REPAIR
    assert f"skills-prune-preserved-{RETIRED_SKILL_NAME}" in failures


def test_install_preserves_universal_marked_skill_across_repeated_prunes(
    tmp_path: Path,
) -> None:
    """A ``ralph-universal``-marked skill survives every prune pass.

    The marker is written by hand per the shared marker contract, so the
    case does not depend on any other unit's code.
    """
    home = tmp_path / "home"
    home.mkdir()
    with patch("pathlib.Path.home", return_value=home):
        skill_dir = _canonical_root() / RETIRED_SKILL_NAME
        skill_dir.mkdir(parents=True)
        content = "universal\n"
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        (skill_dir / MANAGED_MARKER_NAME).write_text(
            json.dumps(
                {
                    "managed_by": "ralph-universal",
                    "source": "obra/superpowers",
                    "source_tag": "v1.0.0",
                    "installed_sha256": _digest(content),
                    "files": {"SKILL.md": _digest(content)},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        first_entry, first_failures = install_baseline_skills()
        second_entry, second_failures = install_baseline_skills()

    assert skill_dir.is_dir(), "a universal-marked skill must never be pruned"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "universal\n"
    assert first_entry.status == _INSTALLED_HEALTHY, first_failures
    assert second_entry.status == _INSTALLED_HEALTHY, second_failures
    assert first_failures == []
    assert second_failures == []


def test_project_install_prunes_retired_managed_skill_at_every_project_root(
    tmp_path: Path,
) -> None:
    """A retired managed skill is removed at the project canonical AND siblings."""
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with patch("pathlib.Path.home", return_value=home):
        canonical = workspace / ".opencode" / "skills"
        canonical.mkdir(parents=True)
        project_roots = [
            canonical,
            workspace / ".claude" / "skills",
            workspace / ".codex" / "skills",
            workspace / ".gemini" / "antigravity-cli" / "skills",
            workspace / ".agents" / "skills",
        ]
        for root in project_roots:
            _plant_managed_skill(root, RETIRED_SKILL_NAME)
        entry, failures = install_project_baseline_skills(workspace)

    assert entry.status == _INSTALLED_HEALTHY, failures
    assert failures == []
    for root in project_roots:
        assert not (root / RETIRED_SKILL_NAME).exists(), (
            f"retired managed skill must be pruned at {root}"
        )
