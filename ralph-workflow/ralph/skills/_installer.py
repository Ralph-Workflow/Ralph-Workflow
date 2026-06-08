"""Baseline skill bundle installation and update checks."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from ralph.skills._agent_paths import (
    AgentSkillRoot,
    agent_skill_roots,
    canonical_agent_skill_root,
    sibling_agent_skill_roots,
)
from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._content import (
    _MANAGED_MARKER,
    BASELINE_SKILL_NAMES,
    get_skill_content,
    materialize_skills_to_claude_dir,
)

if TYPE_CHECKING:
    from pathlib import Path


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _installed_skills_dir() -> Path:
    return canonical_agent_skill_root().resolve()


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


def _compute_skill_hash(name: str) -> str:
    return hashlib.sha256(get_skill_content(name).encode("utf-8")).hexdigest()


def _mirror_skill_to_sibling_root(
    *, skill_name: str, sibling_root: AgentSkillRoot, canonical_root: Path
) -> str | None:
    sibling_dir = sibling_root.resolve() / skill_name
    marker = sibling_dir / _MANAGED_MARKER
    if sibling_dir.exists() and not marker.exists():
        skill_file = sibling_dir / "SKILL.md"
        if skill_file.exists() and skill_file.read_text(encoding="utf-8") != get_skill_content(
            skill_name
        ):
            return f"sibling-conflict-{skill_name}"
    sibling_dir.parent.mkdir(parents=True, exist_ok=True)
    if sibling_dir.is_symlink():
        sibling_dir.unlink()
    elif sibling_dir.is_dir():
        shutil.rmtree(sibling_dir)
    try:
        sibling_dir.symlink_to(canonical_root / skill_name, target_is_directory=True)
    except OSError:
        try:
            shutil.copytree(canonical_root / skill_name, sibling_dir)
        except OSError:
            return f"sibling-materialize-failed-{skill_name}"
    return None


def _mirror_baseline_skills_to_siblings(canonical_root: Path) -> list[str]:
    failures: list[str] = []
    for sibling in sibling_agent_skill_roots():
        sibling_resolved = sibling.resolve()
        sibling_resolved.mkdir(parents=True, exist_ok=True)
        # Mirror the canonical metadata.json at the sibling root so the
        # check_skills_update_available() iteration can verify each registered
        # root against the same source of truth.
        sibling_metadata = sibling_resolved / "metadata.json"
        if sibling_metadata.is_symlink() or sibling_metadata.is_file():
            sibling_metadata.unlink()
        elif sibling_metadata.is_dir():
            shutil.rmtree(sibling_metadata)
        try:
            sibling_metadata.symlink_to(canonical_root / "metadata.json")
        except OSError:
            try:
                shutil.copy2(canonical_root / "metadata.json", sibling_metadata)
            except OSError:
                failures.append("sibling-materialize-failed-metadata")
        for name in BASELINE_SKILL_NAMES:
            failure = _mirror_skill_to_sibling_root(
                skill_name=name, sibling_root=sibling, canonical_root=canonical_root
            )
            if failure is not None:
                failures.append(failure)
    return failures


def install_baseline_skills(
    target_dir: Path | None = None,
) -> tuple[CapabilityEntry, list[str]]:
    """Install the baseline skill bundle.

    Precedence contract (locked):
      (1) If the canonical directory already has conflicting user-owned skill
          files, return ``NEEDS_REPAIR`` with ``skills-conflict-*`` failures
          and DO NOT materialize, mirror, or touch sibling roots. The user
          keeps their files untouched.
      (2) If canonical conflict detection passes, attempt to materialize the
          bundle into the canonical root. On ``OSError`` during materialize,
          return ``NEEDS_REPAIR`` with ``["skills-materialize-failed"]`` and
          DO NOT run the sibling mirror.
      (3) If canonical materialize succeeds, run the sibling mirror across
          every supported agent's user-global skill root. Any sibling failure
          (``sibling-conflict-*`` / ``sibling-materialize-failed-*``) is
          appended to a flat ``failures`` list.

    The returned ``failures`` list is therefore NEVER a mix of canonical and
    sibling codes — canonical is all-or-nothing.
    """
    resolved_target_dir = _installed_skills_dir() if target_dir is None else target_dir
    canonical_failures = _find_conflicts(resolved_target_dir)
    if canonical_failures:
        return (
            CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            ),
            [f"skills-conflict-{name}" for name in canonical_failures],
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
    sibling_failures = _mirror_baseline_skills_to_siblings(resolved_target_dir)
    if sibling_failures:
        return (
            CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            ),
            sibling_failures,
        )
    return (
        CapabilityEntry(
            status=CapabilityStatus.INSTALLED_HEALTHY,
            last_check_ok_iso=_now_iso(),
        ),
        [],
    )


def _root_metadata_valid(resolved: Path) -> bool:
    metadata = resolved / "metadata.json"
    if not metadata.exists():
        return False
    try:
        on_disk_raw: object = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(on_disk_raw, dict)


def _root_skill_marker_valid(resolved: Path, name: str) -> bool:
    marker = resolved / name / _MANAGED_MARKER
    if not marker.exists():
        return False
    try:
        recorded_raw: object = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(recorded_raw, dict):
        return False
    recorded = cast("dict[str, object]", recorded_raw)
    if recorded.get("managed_by") != "ralph-workflow":
        return False
    return recorded.get("installed_content_sha256") == _compute_skill_hash(name)


def check_skills_update_available() -> bool:
    """Return True if any registered user-global skill root needs an update.

    Iterates every entry returned by ``agent_skill_roots()`` and returns True
    on the first missing or mismatched root. Each root is checked for:
      - existence of the root directory and a valid ``metadata.json``
      - a ``.ralph-managed.json`` marker for every baseline skill with the
        correct ``managed_by`` and ``content_sha256`` hash
    """
    for root in agent_skill_roots():
        resolved = root.resolve()
        if not resolved.exists():
            return True
        if not _root_metadata_valid(resolved):
            return True
        for name in BASELINE_SKILL_NAMES:
            if not _root_skill_marker_valid(resolved, name):
                return True
    return False


__all__ = [
    "_mirror_baseline_skills_to_siblings",
    "_mirror_skill_to_sibling_root",
    "check_skills_update_available",
    "install_baseline_skills",
]
