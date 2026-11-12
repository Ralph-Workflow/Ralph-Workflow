"""Ownership-gated prune / replace contract for skill entries.

The ownership rules live in ONE shared module
(:mod:`ralph.skills._ownership`), so the prune and the project-scope
installer gate every delete / replace / relink on the SAME predicate:

* a MANAGED entry (a Ralph marker whose recorded digests still match the
  complete on-disk content) is the ONLY entry Ralph may remove or
  overwrite;
* every other shape survives: a user-owned directory, a user-edited
  directory, a directory carrying a user-added companion file, a
  malformed marker, a ``ralph-universal``-marked directory, and a broken
  symlink Ralph cannot attribute to a just-pruned managed entry;
* a broken symlink IS removed when the prune can attribute its missing
  target to the Ralph-managed canonical entry the SAME pass just deleted.

Every assertion below is made on observable outcomes: the files that
survive on disk, the entries the install reports, and the ownership
verdict the shared predicate returns.
"""

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
    may_delete,
    may_replace,
)
from ralph.skills.manager import install_baseline_skills

pytestmark = pytest.mark.timeout_seconds(5)

#: A skill name the shipped bundle does NOT contain (the retired shape).
RETIRED_NAME = "retired-managed-skill"


def _digest(content: str) -> str:
    """Return the sha256 of ``content`` encoded as UTF-8."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _plant_managed_dir(root: Path, name: str, *, content: str = "retired\n") -> Path:
    """Plant a PRISTINE Ralph-managed skill directory under ``root``.

    The marker carries the REAL installed shape (``managed_by`` plus the
    recorded ``installed_content_sha256`` digest of the installed content),
    so the ownership predicate can prove Ralph manages the directory.
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
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return skill_dir


def test_classify_entry_reports_every_ownership_verdict(tmp_path: Path) -> None:
    """The shared predicate returns the documented verdict for every shape."""
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
    """A live link to a MANAGED directory is MANAGED; a broken link is UNKNOWN."""
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
    """A MIXED tree: only the provably-managed entry is removed.

    The fixture holds one entry per ownership shape. The install must
    remove the pristine managed entry, preserve every other entry, and
    REPORT the ones it cannot prove pristine.
    """
    root = tmp_path / "skills"
    root.mkdir()
    with pytest.raises(ValueError):
        get_skill_content(RETIRED_NAME)

    # 1. A pristine managed entry -- the ONLY entry Ralph may remove.
    managed = _plant_managed_dir(root, RETIRED_NAME)

    # 2. A user-edited managed entry -- preserved and reported.
    edited = _plant_managed_dir(root, "edited-retired-skill")
    (edited / "SKILL.md").write_text("# user edited\n", encoding="utf-8")

    # 3. An unmarked entry with the same shape as a retired skill --
    #    user-owned, preserved (and not Ralph's to report).
    unmarked = root / "unmarked-retired-skill"
    unmarked.mkdir()
    (unmarked / "SKILL.md").write_text("# my own skill\n", encoding="utf-8")

    # 4. A malformed marker -- preserved and reported.
    malformed = root / "malformed-retired-skill"
    malformed.mkdir()
    (malformed / "SKILL.md").write_text("retired\n", encoding="utf-8")
    (malformed / MANAGED_MARKER_NAME).write_text("not json at all\n", encoding="utf-8")

    # 5. A ``ralph-universal``-marked entry -- preserved.
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

    # 6. A managed entry carrying a user-added companion file -- preserved
    #    and reported.
    companion = _plant_managed_dir(root, "companion-retired-skill")
    (companion / "notes.md").write_text("# my notes\n", encoding="utf-8")

    # 7. A broken symlink Ralph cannot attribute -- preserved and reported.
    broken = root / "broken-unknown-link"
    broken.symlink_to(root / "does-not-exist", target_is_directory=True)

    # 8. A broken symlink whose target IS the managed entry the prune
    #    removes -- unlinked (Ralph can attribute it).
    stale_link = root / "broken-pruned-link"
    stale_link.symlink_to(managed, target_is_directory=True)

    # 9. A LIVE symlink to a retired entry that survives the prune -- left
    #    intact.
    live_link = root / "live-retired-link"
    live_link.symlink_to(edited, target_is_directory=True)

    _entry, failures = install_baseline_skills(target_dir=root)

    # The pristine managed entry is gone.
    assert not managed.exists(), "a provably-managed retired entry must be pruned"

    # Every other entry survives.
    assert (edited / "SKILL.md").read_text(encoding="utf-8") == "# user edited\n"
    assert (unmarked / "SKILL.md").read_text(encoding="utf-8") == "# my own skill\n"
    assert (malformed / "SKILL.md").read_text(encoding="utf-8") == "retired\n"
    assert (universal / "SKILL.md").read_text(encoding="utf-8") == "universal\n"
    assert (companion / "notes.md").read_text(encoding="utf-8") == "# my notes\n"

    # The symlinks: the attributable one is unlinked, the unattributable
    # one and the live one survive.
    assert not stale_link.is_symlink(), (
        "a broken symlink whose target the prune just removed must be unlinked"
    )
    assert broken.is_symlink(), "a broken symlink Ralph cannot attribute must survive"
    assert live_link.is_symlink(), "a live symlink to a surviving entry must survive"

    # The entries the prune cannot prove pristine are REPORTED.
    assert "skills-prune-preserved-edited-retired-skill" in failures, failures
    assert "skills-prune-preserved-companion-retired-skill" in failures, failures
    assert "skills-prune-unknown-malformed-retired-skill" in failures, failures
    assert "skills-prune-unknown-broken-unknown-link" in failures, failures
    # The user-owned and the universal-marked entries are not Ralph's to
    # report.
    assert "skills-prune-preserved-unmarked-retired-skill" not in failures, failures
    assert "skills-prune-unknown-universal-retired-skill" not in failures, failures


def test_project_install_never_overwrites_a_user_edited_canonical_skill(
    tmp_path: Path,
) -> None:
    """A project-scope user-edited ``SKILL.md`` is preserved, not overwritten.

    The canonical entry carries a Ralph marker whose recorded digest no
    longer matches the on-disk content, so Ralph cannot prove it manages
    the entry: the bundled content must NOT overwrite the user's edit.
    """
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
    # The user edits the installed content: the recorded digest no longer
    # matches, so the entry is USER-EDITED.
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
    """The same skill name in two scopes: only the managed one is removed.

    The canonical root holds a pristine managed retired entry (Ralph may
    remove it); a sibling root holds an UNMARKED directory with the SAME
    name (the user's own copy, which Ralph must not touch).
    """
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
    """A sibling entry Ralph cannot prove it manages is preserved + reported.

    The sibling root holds a directory with a baseline skill name whose
    content diverges from the bundled content and that carries no Ralph
    marker: the install must neither replace it nor relink it, and it must
    report the conflict.
    """
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
    """The stale sibling link of a just-pruned canonical entry is unlinked.

    The canonical root holds a pristine managed retired entry; a sibling
    root holds the symlink that points at it. The install prunes the
    canonical entry first, so the sibling link becomes BROKEN -- and
    attributable to the entry the canonical prune just removed. That is
    the ONLY reason the link may be unlinked.
    """
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
