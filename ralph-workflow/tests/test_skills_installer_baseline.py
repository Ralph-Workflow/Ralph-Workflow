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

_INSTALLED_HEALTHY = "installed_healthy"
_NEEDS_REPAIR = "needs_repair"


def test_install_baseline_skills_reports_healthy_install(tmp_path: Path) -> None:
    target_dir = tmp_path / "skills"

    entry, failures = install_baseline_skills(target_dir=target_dir)

    assert entry.status == _INSTALLED_HEALTHY
    assert failures == []


def test_install_baseline_skills_reports_failure_when_materialization_fails(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "skills"
    target_dir.write_text("not a directory", encoding="utf-8")

    entry, failures = install_baseline_skills(target_dir=target_dir)

    assert entry.status == _NEEDS_REPAIR
    assert failures == ["skills-materialize-failed"]


def test_install_baseline_skills_writes_metadata_file(tmp_path: Path) -> None:
    entry, failures = install_baseline_skills(target_dir=tmp_path)

    assert entry.status == _INSTALLED_HEALTHY
    assert failures == []
    assert (tmp_path / "metadata.json").exists()


def test_install_baseline_skills_writes_claude_discoverable_skill_directories(
    tmp_path: Path,
) -> None:
    entry, failures = install_baseline_skills(target_dir=tmp_path)
    # RALPH-TEST-REASON: the retired default skills are no longer shipped, so the fixture uses a retained artifact skill

    assert entry.status == _INSTALLED_HEALTHY, failures
    assert failures == []
    skill_file = tmp_path / "submit-artifact" / "SKILL.md"
    assert skill_file.exists()
    assert skill_file.read_text(encoding="utf-8").startswith("---\nname: submit-artifact")


def test_install_baseline_skills_preserves_conflicting_user_skill(tmp_path: Path) -> None:
    target_dir = tmp_path / "skills"
    user_skill_dir = target_dir / "submit-artifact"
    user_skill_dir.mkdir(parents=True)
    skill_file = user_skill_dir / "SKILL.md"
    skill_file.write_text("# user version\n", encoding="utf-8")

    # RALPH-TEST-REASON: the retired default skills are no longer shipped, so the fixture uses a retained artifact skill
    entry, failures = install_baseline_skills(target_dir=target_dir)

    assert entry.status == _NEEDS_REPAIR
    assert failures == ["skills-conflict-submit-artifact"]
    assert skill_file.read_text(encoding="utf-8") == "# user version\n"


def test_install_baseline_skills_updates_ralph_managed_skill_in_place(tmp_path: Path) -> None:
    target_dir = tmp_path / "skills"
    managed_skill_dir = target_dir / "submit-artifact"
    managed_skill_dir.mkdir(parents=True)
    skill_file = managed_skill_dir / "SKILL.md"
    skill_file.write_text("# old ralph version\n", encoding="utf-8")
    (managed_skill_dir / ".ralph-managed.json").write_text(
        '{"managed_by": "ralph-workflow", "skill": "submit-artifact"}\n',
        encoding="utf-8",
    )

    entry, failures = install_baseline_skills(target_dir=target_dir)
    # RALPH-TEST-REASON: the retired default skills are no longer shipped, so the fixture uses a retained artifact skill

    assert entry.status == _INSTALLED_HEALTHY
    assert failures == []
    assert skill_file.read_text(encoding="utf-8").startswith("---\nname: submit-artifact")


def test_install_baseline_skills_preserves_manually_edited_managed_skill(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "skills"
    managed_skill_dir = target_dir / "submit-artifact"
    managed_skill_dir.mkdir(parents=True)
    original_content = "# original ralph-installed content\n"
    original_sha = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
    skill_file = managed_skill_dir / "SKILL.md"
    skill_file.write_text(original_content, encoding="utf-8")
    (managed_skill_dir / ".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": "submit-artifact",
                "installed_content_sha256": original_sha,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    user_edited_content = "# user edited — do not overwrite\n"
    skill_file.write_text(user_edited_content, encoding="utf-8")

    entry, failures = install_baseline_skills(target_dir=target_dir)

    assert entry.status == _INSTALLED_HEALTHY
    assert failures == []
    assert skill_file.read_text(encoding="utf-8") == user_edited_content


def test_install_baseline_skills_adopts_unmarked_identical_skill(tmp_path: Path) -> None:
    target_dir = tmp_path / "skills"
    skill_dir = target_dir / "submit-artifact"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(get_skill_content("submit-artifact"), encoding="utf-8")

    entry, failures = install_baseline_skills(target_dir=target_dir)

    assert entry.status == _INSTALLED_HEALTHY
    assert failures == []
    assert (skill_dir / ".ralph-managed.json").exists()
