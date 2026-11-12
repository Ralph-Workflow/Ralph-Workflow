from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ralph.skills import get_skill_content
from ralph.skills._installer import install_project_baseline_skills
from ralph.skills._ownership import (
    MANAGED_MARKER_NAME,
    Ownership,
    classify_entry,
    manifest_digest,
    may_delete,
    may_replace,
)
from ralph.skills.manager import install_baseline_skills

pytestmark = pytest.mark.timeout_seconds(5)

RETIRED_NAME = "retired-managed-skill"


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _plant_managed_dir(root: Path, name: str, *, content: str = "retired\n") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    (skill_dir / MANAGED_MARKER_NAME).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": _digest(content),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return skill_dir


def test_classify_entry_reports_every_ownership_verdict(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()

    managed = _plant_managed_dir(root, "managed-skill")
    assert classify_entry(managed) is Ownership.MANAGED
    assert may_replace(managed) is True
    assert may_delete(managed) is True

    edited = _plant_managed_dir(root, "edited-skill")
    (edited / "SKILL.md").write_text("# user edited\n", encoding="utf-8")
    assert classify_entry(edited) is Ownership.USER_EDITED
    assert may_replace(edited) is False
    assert may_delete(edited) is False

    companion = _plant_managed_dir(root, "companion-skill")
    (companion / "notes.md").write_text("# my notes\n", encoding="utf-8")
    assert classify_entry(companion) is Ownership.USER_EDITED

    unmarked = root / "unmarked-skill"
    unmarked.mkdir()
    (unmarked / "SKILL.md").write_text("# my own skill\n", encoding="utf-8")
    assert classify_entry(unmarked) is Ownership.USER_OWNED
    assert may_replace(unmarked) is False

    malformed = root / "malformed-skill"
    malformed.mkdir()
    (malformed / "SKILL.md").write_text("retired\n", encoding="utf-8")
    (malformed / MANAGED_MARKER_NAME).write_text("not json at all\n", encoding="utf-8")
    assert classify_entry(malformed) is Ownership.UNKNOWN
    assert may_delete(malformed) is False

    universal = root / "universal-skill"
    universal.mkdir()
    universal_content = "universal\n"
    (universal / "SKILL.md").write_text(universal_content, encoding="utf-8")
    (universal / MANAGED_MARKER_NAME).write_text(
        json.dumps(
            {
                "managed_by": "ralph-universal",
                "installed_sha256": _digest(universal_content),
                "files": {"SKILL.md": _digest(universal_content)},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert classify_entry(universal) is Ownership.FOREIGN
    assert may_delete(universal) is False

    assert classify_entry(root / "does-not-exist") is Ownership.ABSENT


def test_classify_entry_classifies_a_symlink_by_its_target(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()

    managed = _plant_managed_dir(root, "managed-skill")
    link = root / "managed-link"
    link.symlink_to(managed, target_is_directory=True)
    assert classify_entry(link) is Ownership.MANAGED

    user_dir = root / "user-skill"
    user_dir.mkdir()
    user_link = root / "user-link"
    user_link.symlink_to(user_dir, target_is_directory=True)
    assert classify_entry(user_link) is Ownership.USER_OWNED

    broken = root / "broken-link"
    broken.symlink_to(root / "does-not-exist", target_is_directory=True)
    assert classify_entry(broken) is Ownership.UNKNOWN
    assert may_delete(broken) is False


def test_install_prunes_only_the_entries_it_can_prove_it_manages(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    with pytest.raises(ValueError):
        get_skill_content(RETIRED_NAME)

    managed = _plant_managed_dir(root, RETIRED_NAME)

    edited = _plant_managed_dir(root, "edited-retired-skill")
    (edited / "SKILL.md").write_text("# user edited\n", encoding="utf-8")

    unmarked = root / "unmarked-retired-skill"
    unmarked.mkdir()
    (unmarked / "SKILL.md").write_text("# my own skill\n", encoding="utf-8")

    malformed = root / "malformed-retired-skill"
    malformed.mkdir()
    (malformed / "SKILL.md").write_text("retired\n", encoding="utf-8")
    (malformed / MANAGED_MARKER_NAME).write_text("not json at all\n", encoding="utf-8")

    universal = root / "universal-retired-skill"
    universal.mkdir()
    universal_content = "universal\n"
    (universal / "SKILL.md").write_text(universal_content, encoding="utf-8")
    (universal / MANAGED_MARKER_NAME).write_text(
        json.dumps(
            {
                "managed_by": "ralph-universal",
                "source": "obra/superpowers",
                "installed_sha256": _digest(universal_content),
                "files": {"SKILL.md": _digest(universal_content)},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    companion = _plant_managed_dir(root, "companion-retired-skill")
    (companion / "notes.md").write_text("# my notes\n", encoding="utf-8")

    broken = root / "broken-unknown-link"
    broken.symlink_to(root / "does-not-exist", target_is_directory=True)

    stale_link = root / "broken-pruned-link"
    stale_link.symlink_to(managed, target_is_directory=True)

    live_link = root / "live-retired-link"
    live_link.symlink_to(edited, target_is_directory=True)

    _entry, failures = install_baseline_skills(target_dir=root)

    assert not managed.exists(), "a provably-managed retired entry must be pruned"

    assert (edited / "SKILL.md").read_text(encoding="utf-8") == "# user edited\n"
    assert (unmarked / "SKILL.md").read_text(encoding="utf-8") == "# my own skill\n"
    assert (malformed / "SKILL.md").read_text(encoding="utf-8") == "retired\n"
    assert (universal / "SKILL.md").read_text(encoding="utf-8") == "universal\n"
    assert (companion / "notes.md").read_text(encoding="utf-8") == "# my notes\n"

    assert not stale_link.is_symlink(), (
        "a broken symlink whose target the prune just removed must be unlinked"
    )
    assert broken.is_symlink(), "a broken symlink Ralph cannot attribute must survive"
    assert live_link.is_symlink(), "a live symlink to a surviving entry must survive"

    assert "skills-prune-preserved-edited-retired-skill" in failures, failures
    assert "skills-prune-preserved-companion-retired-skill" in failures, failures
    assert "skills-prune-unknown-malformed-retired-skill" in failures, failures
    assert "skills-prune-unknown-broken-unknown-link" in failures, failures
    assert "skills-prune-preserved-unmarked-retired-skill" not in failures, failures
    assert "skills-prune-unknown-universal-retired-skill" not in failures, failures


def test_project_install_never_overwrites_a_user_edited_canonical_skill(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    canonical = workspace / ".opencode" / "skills"
    name = "submit-artifact"
    skill_dir = canonical / name
    skill_dir.mkdir(parents=True)
    installed = "# the version Ralph installed\n"
    (skill_dir / "SKILL.md").write_text(installed, encoding="utf-8")
    (skill_dir / MANAGED_MARKER_NAME).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": _digest(installed),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    user_edit = "# my own version\n"
    (skill_dir / "SKILL.md").write_text(user_edit, encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(workspace)

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == user_edit, (
        "a user-edited canonical SKILL.md must never be overwritten"
    )
    assert classify_entry(skill_dir) is Ownership.USER_EDITED


def test_project_install_prunes_the_managed_sibling_and_keeps_the_user_one(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    canonical = workspace / ".opencode" / "skills"
    canonical.mkdir(parents=True)
    managed = _plant_managed_dir(canonical, RETIRED_NAME)

    sibling = workspace / ".claude" / "skills" / RETIRED_NAME
    sibling.mkdir(parents=True)
    (sibling / "SKILL.md").write_text("# my own copy\n", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(workspace)

    assert not managed.exists(), "the provably-managed retired entry must be pruned"
    assert (sibling / "SKILL.md").read_text(encoding="utf-8") == "# my own copy\n", (
        "the unmarked same-name entry must survive the prune"
    )


def test_project_install_preserves_and_reports_an_unmanaged_sibling_entry(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    name = "submit-artifact"
    sibling = workspace / ".claude" / "skills" / name
    sibling.mkdir(parents=True)
    (sibling / "SKILL.md").write_text("# sibling override\n", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        _entry, failures = install_project_baseline_skills(workspace)

    assert (sibling / "SKILL.md").read_text(encoding="utf-8") == "# sibling override\n", (
        "an unmanaged sibling entry must never be overwritten"
    )
    assert f"sibling-conflict-{name}" in failures, failures


def test_project_install_unlinks_the_sibling_link_to_a_just_pruned_canonical_entry(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    canonical = workspace / ".opencode" / "skills"
    canonical.mkdir(parents=True)
    managed = _plant_managed_dir(canonical, RETIRED_NAME)

    sibling_root = workspace / ".claude" / "skills"
    sibling_root.mkdir(parents=True)
    sibling_link = sibling_root / RETIRED_NAME
    sibling_link.symlink_to(managed, target_is_directory=True)
    assert sibling_link.is_symlink()

    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(workspace)

    assert not managed.exists(), "the provably-managed retired entry must be pruned"
    assert not sibling_link.is_symlink(), (
        "the stale sibling link of the just-pruned canonical entry must be unlinked"
    )


def test_prune_preserves_a_managed_retired_skill_that_carries_a_user_symlink(
    tmp_path: Path,
) -> None:
    user_target = tmp_path / "user-notes.md"
    user_target.write_text("# my notes\n", encoding="utf-8")
    root = tmp_path / "skills"
    root.mkdir()
    skill_dir = _plant_managed_dir(root, RETIRED_NAME)
    link = skill_dir / "my-notes.md"
    link.symlink_to(user_target)

    assert classify_entry(skill_dir) is Ownership.USER_EDITED
    assert may_delete(skill_dir) is False
    assert may_replace(skill_dir) is False

    _entry, failures = install_baseline_skills(target_dir=root)

    assert skill_dir.is_dir(), "a managed skill carrying a user symlink must survive"
    assert link.is_symlink(), "the user symlink must survive the prune"
    assert user_target.read_text(encoding="utf-8") == "# my notes\n"
    assert f"skills-prune-preserved-{RETIRED_NAME}" in failures, failures


def test_prune_preserves_a_managed_retired_skill_that_carries_a_broken_user_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    skill_dir = _plant_managed_dir(root, RETIRED_NAME)
    link = skill_dir / "dangling.md"
    link.symlink_to(root / "missing-target.md")

    assert classify_entry(skill_dir) is Ownership.USER_EDITED
    assert may_delete(skill_dir) is False

    _entry, failures = install_baseline_skills(target_dir=root)

    assert skill_dir.is_dir(), "a managed skill carrying a broken user symlink must survive"
    assert link.is_symlink(), "the broken user symlink must survive the prune"
    assert f"skills-prune-preserved-{RETIRED_NAME}" in failures, failures


def test_prune_still_removes_a_managed_skill_whose_marker_records_its_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    content = "recorded\n"
    digest = _digest(content)
    recorded = {"SKILL.md": digest, "alias.md": digest}
    skill_dir = root / RETIRED_NAME
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    (skill_dir / MANAGED_MARKER_NAME).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": RETIRED_NAME,
                "files": recorded,
                "installed_sha256": manifest_digest(recorded),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (skill_dir / "alias.md").symlink_to(skill_dir / "SKILL.md")

    assert classify_entry(skill_dir) is Ownership.MANAGED
    assert may_delete(skill_dir) is True

    _entry, failures = install_baseline_skills(target_dir=root)

    assert not skill_dir.exists(), "a marker-recorded symlink stays managed content"
    assert f"skills-prune-preserved-{RETIRED_NAME}" not in failures, failures


def test_prune_still_preserves_a_managed_retired_skill_that_carries_a_user_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    skill_dir = _plant_managed_dir(root, RETIRED_NAME)
    (skill_dir / "notes.md").write_text("# my notes\n", encoding="utf-8")

    assert classify_entry(skill_dir) is Ownership.USER_EDITED

    _entry, failures = install_baseline_skills(target_dir=root)

    assert skill_dir.is_dir(), "a managed skill carrying a user file must survive"
    assert (skill_dir / "notes.md").read_text(encoding="utf-8") == "# my notes\n"
    assert f"skills-prune-preserved-{RETIRED_NAME}" in failures, failures


def test_install_refuses_to_replace_a_managed_skill_that_carries_a_user_symlink(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    canonical = workspace / ".opencode" / "skills"
    name = "submit-artifact"
    skill_dir = canonical / name
    skill_dir.mkdir(parents=True)
    bundled = get_skill_content(name)
    (skill_dir / "SKILL.md").write_text(bundled, encoding="utf-8")
    (skill_dir / MANAGED_MARKER_NAME).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": _digest(bundled),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    user_target = tmp_path / "user-link-target.md"
    user_target.write_text("# my link\n", encoding="utf-8")
    link = skill_dir / "my-link.md"
    link.symlink_to(user_target)

    control_root = tmp_path / "control"
    control_root.mkdir()
    control = _plant_managed_dir(control_root, RETIRED_NAME)
    assert may_replace(control) is True, "the control skill carries no user symlink"
    assert may_replace(skill_dir) is False, "the user symlink must block the replace"
    assert classify_entry(skill_dir) is Ownership.USER_EDITED

    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(workspace)

    assert link.is_symlink(), "the user symlink must survive the install"
    assert user_target.read_text(encoding="utf-8") == "# my link\n"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == bundled
