"""Baseline skill bundle installation and update checks."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._content import BASELINE_SKILL_NAMES, get_skill_content, materialize_skills_to_dir


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _installed_skills_dir() -> Path:
    return Path.home() / ".claude" / "plugins" / "ralph-workflow-skills" / "skills"


def install_baseline_skills() -> tuple[CapabilityEntry, list[str]]:
    target_dir = _installed_skills_dir()
    try:
        materialize_skills_to_dir(target_dir)
    except OSError:
        return (
            CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            ),
            ["skills-materialize-failed"],
        )
    return (
        CapabilityEntry(
            status=CapabilityStatus.INSTALLED_HEALTHY,
            last_check_ok_iso=_now_iso(),
        ),
        [],
    )


def check_skills_update_available() -> bool:
    installed_dir = _installed_skills_dir()
    if not installed_dir.exists():
        return True
    try:
        for name in BASELINE_SKILL_NAMES:
            installed_file = installed_dir / f"{name}.md"
            if not installed_file.exists():
                return True
            installed_hash = hashlib.sha256(installed_file.read_bytes()).digest()
            expected_hash = hashlib.sha256(get_skill_content(name).encode("utf-8")).digest()
            if installed_hash != expected_hash:
                return True
        return False
    except Exception:
        return False


__all__ = ["check_skills_update_available", "install_baseline_skills"]
