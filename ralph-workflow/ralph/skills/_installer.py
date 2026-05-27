"""Baseline skill bundle installation and update checks."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._content import (
    _MANAGED_MARKER,
    BASELINE_SKILL_NAMES,
    get_skill_content,
    get_skill_metadata,
    materialize_skills_to_claude_dir,
)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _installed_skills_dir() -> Path:
    return Path.home() / ".claude" / "skills"


def _find_conflicts(target_dir: Path) -> list[str]:
    conflicts: list[str] = []
    for name in BASELINE_SKILL_NAMES:
        skill_file = target_dir / name / "SKILL.md"
        if not skill_file.exists():
            continue
        marker_file = target_dir / name / _MANAGED_MARKER
        if marker_file.exists():
            continue
        if skill_file.read_text(encoding="utf-8") != get_skill_content(name):
            conflicts.append(name)
    return conflicts


def install_baseline_skills(target_dir: Path | None = None) -> tuple[CapabilityEntry, list[str]]:
    resolved_target_dir = target_dir or _installed_skills_dir()
    conflicts = _find_conflicts(resolved_target_dir)
    if conflicts:
        return (
            CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            ),
            [f"skills-conflict-{name}" for name in conflicts],
        )
    try:
        materialize_skills_to_claude_dir(resolved_target_dir)
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
        metadata_path = installed_dir / "metadata.json"
        metadata_matches = metadata_path.exists() and (
            metadata_path.read_text(encoding="utf-8")
            == json.dumps(get_skill_metadata(), indent=2) + "\n"
        )
        if not metadata_matches:
            return True
        for name in BASELINE_SKILL_NAMES:
            installed_file = installed_dir / name / "SKILL.md"
            marker_file = installed_dir / name / _MANAGED_MARKER
            installed_hash = (
                hashlib.sha256(installed_file.read_bytes()).digest()
                if installed_file.exists()
                else None
            )
            expected_hash = hashlib.sha256(get_skill_content(name).encode("utf-8")).digest()
            if installed_hash != expected_hash or not marker_file.exists():
                return True
        return False
    except Exception:
        return False


__all__ = ["check_skills_update_available", "install_baseline_skills"]
