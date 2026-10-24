"""Behavioral tests for the baseline skill installer.

The installer is driven through its PUBLIC entry point
(:func:`ralph.skills.manager.install_baseline_skills`, re-exported by the
public ``ralph.skills.manager`` module that owns the ``SkillManager``
facade) and every assertion is made on observable outcomes:

* the returned capability status / failure codes, and
* the files that end up on disk under the injected target directory.

No private module attribute is patched: the target directory is injected
through the public ``target_dir`` parameter, so the canonical user-global
skill root is never touched.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from ralph.skills import get_skill_content
from ralph.skills.manager import install_baseline_skills

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout_seconds(5)

#: Wire values of the capability status enum (the observable contract the
#: installer returns). Comparing the value keeps the assertion on the
#: installer's OUTPUT rather than on the enum class it happens to use.
_INSTALLED_HEALTHY = "installed_healthy"
_NEEDS_REPAIR = "needs_repair"


def test_install_baseline_skills_reports_healthy_install(tmp_path: Path) -> None:
    """A clean install reports the healthy status and no failure codes."""
    target_dir = tmp_path / "skills"

    entry, failures = install_baseline_skills(target_dir=target_dir)

    assert entry.status == _INSTALLED_HEALTHY
    assert failures == []


def test_install_baseline_skills_reports_failure_when_materialization_fails(
    tmp_path: Path,
) -> None:
    """An unwritable target root yields the materialize failure code."""
    # A regular file where the skill root belongs makes the directory
    # creation fail with an OSError, which is the real materialize failure.
    target_dir = tmp_path / "skills"
    target_dir.write_text("not a directory", encoding="utf-8")

    entry, failures = install_baseline_skills(target_dir=target_dir)

    assert entry.status == _NEEDS_REPAIR
    assert failures == ["skills-materialize-failed"]


def test_install_baseline_skills_writes_metadata_file(tmp_path: Path) -> None:
    """The installer publishes the bundle metadata next to the skills."""
    entry, failures = install_baseline_skills(target_dir=tmp_path)

    assert entry.status == _INSTALLED_HEALTHY
    assert failures == []
    assert (tmp_path / "metadata.json").exists()


def test_install_baseline_skills_writes_claude_discoverable_skill_directories(
    tmp_path: Path,
) -> None:
    """Each baseline skill lands as a Claude-discoverable directory."""
    entry, failures = install_baseline_skills(target_dir=tmp_path)

    assert entry.status == _INSTALLED_HEALTHY, failures
    assert failures == []
    skill_file = tmp_path / "using-superpowers" / "SKILL.md"
    assert skill_file.exists()
    assert skill_file.read_text(encoding="utf-8").startswith("---\nname: using-superpowers")


def test_install_baseline_skills_preserves_conflicting_user_skill(tmp_path: Path) -> None:
    """A user-authored skill is never overwritten; the conflict is reported."""
    target_dir = tmp_path / "skills"
    user_skill_dir = target_dir / "using-superpowers"
    user_skill_dir.mkdir(parents=True)
    skill_file = user_skill_dir / "SKILL.md"
    skill_file.write_text("# user version\n", encoding="utf-8")

    entry, failures = install_baseline_skills(target_dir=target_dir)

    assert entry.status == _NEEDS_REPAIR
    assert failures == ["skills-conflict-using-superpowers"]
    assert skill_file.read_text(encoding="utf-8") == "# user version\n"


def test_install_baseline_skills_updates_ralph_managed_skill_in_place(tmp_path: Path) -> None:
    """A ralph-managed skill is refreshed to the current baseline content."""
    target_dir = tmp_path / "skills"
    managed_skill_dir = target_dir / "using-superpowers"
    managed_skill_dir.mkdir(parents=True)
    skill_file = managed_skill_dir / "SKILL.md"
    skill_file.write_text("# old ralph version\n", encoding="utf-8")
    (managed_skill_dir / ".ralph-managed.json").write_text(
        '{"managed_by": "ralph-workflow", "skill": "using-superpowers"}\n',
        encoding="utf-8",
    )

    entry, failures = install_baseline_skills(target_dir=target_dir)

    assert entry.status == _INSTALLED_HEALTHY
    assert failures == []
    assert skill_file.read_text(encoding="utf-8").startswith("---\nname: using-superpowers")


def test_install_baseline_skills_preserves_manually_edited_managed_skill(
    tmp_path: Path,
) -> None:
    """A manually edited managed skill keeps the operator's content."""
    target_dir = tmp_path / "skills"
    managed_skill_dir = target_dir / "using-superpowers"
    managed_skill_dir.mkdir(parents=True)
    original_content = "# original ralph-installed content\n"
    original_sha = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
    skill_file = managed_skill_dir / "SKILL.md"
    skill_file.write_text(original_content, encoding="utf-8")
    (managed_skill_dir / ".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": "using-superpowers",
                "installed_content_sha256": original_sha,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # Simulate manual user edit after ralph installed the skill
    user_edited_content = "# user edited — do not overwrite\n"
    skill_file.write_text(user_edited_content, encoding="utf-8")

    entry, failures = install_baseline_skills(target_dir=target_dir)

    # All other skills are installed; the edited one is skipped — overall still healthy
    assert entry.status == _INSTALLED_HEALTHY
    assert failures == []
    # The manually edited file must NOT be overwritten
    assert skill_file.read_text(encoding="utf-8") == user_edited_content


def test_install_baseline_skills_adopts_unmarked_identical_skill(tmp_path: Path) -> None:
    """An identical but unmarked skill is adopted instead of reported as conflict."""
    target_dir = tmp_path / "skills"
    skill_dir = target_dir / "using-superpowers"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(get_skill_content("using-superpowers"), encoding="utf-8")

    entry, failures = install_baseline_skills(target_dir=target_dir)

    assert entry.status == _INSTALLED_HEALTHY
    assert failures == []
    assert (skill_dir / ".ralph-managed.json").exists()
