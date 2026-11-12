from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from ralph.git.scoped_auto_commit import (
    capture_pre_write_contents,
    snapshot_dirty_paths_strict,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.skills._agent_paths import (
    AgentSkillRoot,
    agent_skill_roots,
    canonical_agent_skill_root,
    project_sibling_skill_roots,
    project_skill_root,
    sibling_agent_skill_roots,
)
from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._content import (
    _MANAGED_MARKER,
    BASELINE_SKILL_NAMES,
    get_skill_content,
    managed_skill_marker,
    materialize_skills_to_claude_dir,
)
from ralph.skills._installer_candidates import (
    _candidate_skill_paths,
    _diff_written_paths,
)
from ralph.skills._ownership import may_replace
from ralph.skills._prune import (
    _prune_failure_codes,
    _prune_removed_baseline_skills,
    _prune_user_global_skill_roots,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.skills._project_paths import ProjectAgentSkillRoot


@dataclass(frozen=True)
class ProjectSkillInstallOutcome:
    entry: CapabilityEntry
    failures: list[str]
    written_paths: list[str] = field(default_factory=list)
    pre_contents: dict[str, str | None] = field(default_factory=dict)
    intentional_transitions: frozenset[str] = field(default_factory=frozenset)


def install_project_baseline_skills_with_diff(
    workspace_root: Path,
) -> ProjectSkillInstallOutcome:
    candidates = _candidate_skill_paths(workspace_root)
    pre_contents = capture_pre_write_contents(workspace_root, candidates)
    user_dirty_descendants = _detect_user_dirty_tracked_descendants(workspace_root, candidates)
    entry, failures = install_project_baseline_skills(workspace_root)
    written = _diff_written_paths(workspace_root, candidates, pre_contents)
    transition_roots = {
        path
        for path in candidates
        if any(other != path and other.startswith(f"{path}/") for other in candidates)
    }
    intentional = frozenset(
        root
        for root in transition_roots
        if not any(
            descendant in user_dirty_descendants
            for descendant in candidates
            if descendant != root and descendant.startswith(f"{root}/")
        )
    )
    return ProjectSkillInstallOutcome(
        entry=entry,
        failures=failures,
        written_paths=written,
        pre_contents={path: pre_contents[path] for path in written},
        intentional_transitions=intentional,
    )


def _detect_user_dirty_tracked_descendants(
    workspace_root: Path,
    candidates: list[str],
) -> frozenset[str]:
    dirty = snapshot_dirty_paths_strict(workspace_root)
    if dirty is None:
        return frozenset(candidates)
    return frozenset(path for path in candidates if path in dirty)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _dedupe_failure_codes(codes: list[str]) -> list[str]:
    unique: list[str] = []
    for code in codes:
        if code not in unique:
            unique.append(code)
    return unique


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


def _create_symlink(path: Path, target: Path, *, target_is_directory: bool = False) -> None:
    path.symlink_to(target, target_is_directory=target_is_directory)


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
        _create_symlink(sibling_dir, canonical_root / skill_name, target_is_directory=True)
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
        sibling_metadata = sibling_resolved / "metadata.json"
        if sibling_metadata.is_symlink() or sibling_metadata.is_file():
            sibling_metadata.unlink()
        elif sibling_metadata.is_dir():
            shutil.rmtree(sibling_metadata)
        try:
            _create_symlink(sibling_metadata, canonical_root / "metadata.json")
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
    if target_dir is not None:
        prune_failures = _prune_failure_codes(_prune_removed_baseline_skills(resolved_target_dir))
        if prune_failures:
            return (
                CapabilityEntry(
                    status=CapabilityStatus.NEEDS_REPAIR,
                    last_check_fail_iso=_now_iso(),
                ),
                prune_failures,
            )
        return (
            CapabilityEntry(
                status=CapabilityStatus.INSTALLED_HEALTHY,
                last_check_ok_iso=_now_iso(),
            ),
            [],
        )
    sibling_failures = _mirror_baseline_skills_to_siblings(resolved_target_dir)
    prune_failures = _prune_user_global_skill_roots()
    failures = _dedupe_failure_codes([*sibling_failures, *prune_failures])
    if failures:
        return (
            CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            ),
            failures,
        )
    return (
        CapabilityEntry(
            status=CapabilityStatus.INSTALLED_HEALTHY,
            last_check_ok_iso=_now_iso(),
        ),
        [],
    )


# --- Project-scope baseline skill installation --------------------------------
#
# The project scope is a separate fan-out (./.opencode/skills/ canonical +
# 3 project siblings). Same precedence contract as the user-global install
# above: canonical is all-or-nothing, sibling failures are appended flatly.
# The project canonical NEVER appears in the sibling list (see PA-007 in
# _agent_paths.py).


def _may_replace_sibling_entry(sibling_dir: Path, canonical_target: Path) -> bool:
    """Return True when the project-scope sibling entry may be replaced.

    A sibling entry is replaceable ONLY when Ralph can prove it manages
    the entry:

    * a symlink that already points at the canonical target (the healthy
      state) is relinked as before;
    * a symlink to a MANAGED pristine directory is relinked as before; and
    * a directory whose marker and recorded digests prove Ralph manages its
      complete content is replaced as before.

    Every other shape -- a user-owned, user-edited, foreign-marked or
    malformed entry, and a broken symlink Ralph cannot attribute -- is
    PRESERVED and reported as a sibling conflict.
    """
    if sibling_dir.is_symlink():
        return _may_replace_sibling_symlink(sibling_dir, canonical_target)
    return may_replace(sibling_dir)


def _may_replace_sibling_symlink(sibling_dir: Path, canonical_target: Path) -> bool:
    """Return True when the sibling SYMLINK may be relinked to the canonical.

    A symlink that already points at the canonical target is the healthy
    state and is relinked as before. A symlink to a MANAGED pristine
    directory is relinked as before. Every other symlink -- including a
    broken link Ralph cannot attribute -- is preserved.
    """
    try:
        resolved = sibling_dir.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if resolved == canonical_target.resolve(strict=False):
        return True
    return may_replace(sibling_dir)


def _materialize_project_sibling_dir(
    sibling_dir: Path, canonical_root: Path, skill_name: str
) -> str | None:
    """Materialize a single project-scope sibling entry as a symlink to the canonical.

    Returns ``None`` on success or a failure code on conflict / hard failure.
    The replacement is OWNERSHIP-GATED: a sibling entry Ralph cannot prove
    it manages is preserved and reported as ``sibling-conflict-<name>``.
    Uses ``Path.resolve()``-aware checks for macOS ``/tmp`` -> ``/private/tmp``
    indirection safety (PA-007).
    """
    canonical_target = canonical_root / skill_name
    if (sibling_dir.is_symlink() or sibling_dir.exists()) and not _may_replace_sibling_entry(
        sibling_dir, canonical_target
    ):
        return f"sibling-conflict-{skill_name}"

    sibling_dir.parent.mkdir(parents=True, exist_ok=True)
    if sibling_dir.is_symlink():
        sibling_dir.unlink()
    elif sibling_dir.is_dir():
        shutil.rmtree(sibling_dir)
    elif sibling_dir.exists():
        sibling_dir.unlink()

    try:
        resolved_target = canonical_target.resolve()
        resolved_parent = sibling_dir.parent.resolve()
        relative_target = os.path.relpath(resolved_target, start=resolved_parent)
        sibling_dir.symlink_to(relative_target, target_is_directory=True)
    except OSError:
        try:
            shutil.copytree(canonical_target, sibling_dir)
        except OSError:
            return f"sibling-materialize-failed-{skill_name}"
    return None


def _materialize_canonical_skill(canonical: Path, skill_name: str) -> bool:
    """Refresh a single project-scope canonical skill with the bundled content.

    The refresh is OWNERSHIP-GATED (see
    :mod:`ralph.skills._ownership`): the bundled content overwrites the
    canonical entry ONLY when Ralph can prove it manages the entry -- the
    managed marker is present and its recorded digests still match the
    on-disk content. A stale-but-pristine Ralph-managed copy IS refreshed
    to the new bundled content so the auto-commit helper sees exactly one
    diff; a USER-EDITED, USER-OWNED, FOREIGN or UNKNOWN entry is
    PRESERVED and never overwritten (PRODUCT_CRITERIA §3.3: "User-owned
    and user-edited skills are untouched").

    Args:
        canonical: Resolved project-canonical skill directory
            (e.g. ``workspace_root / '.opencode' / 'skills'``).
        skill_name: Name of the skill whose canonical entry to refresh.

    Returns:
        True when at least one of ``SKILL.md`` or the managed marker was
        overwritten with bundled content; False when the entry was
        preserved or the on-disk content already matches the bundled hash.

    The helper is fail-closed: any ``OSError`` during read/write returns
    False so the caller can treat overwrite failures as 'no change' and
    still proceed with the rest of the install / auto-commit pipeline.
    """
    try:
        bundled_content = get_skill_content(skill_name)
        bundled_sha = hashlib.sha256(bundled_content.encode("utf-8")).hexdigest()
        skill_dir = canonical / skill_name
        skill_file = skill_dir / "SKILL.md"
        marker_file = skill_dir / _MANAGED_MARKER
        if not skill_dir.exists():
            skill_dir.mkdir(parents=True, exist_ok=True)
        elif not may_replace(skill_dir):
            # USER-EDITED / USER-OWNED / FOREIGN / UNKNOWN: the entry is
            # the user's, so Ralph never overwrites it.
            return False
        on_disk_hash = (
            hashlib.sha256(skill_file.read_bytes()).hexdigest() if skill_file.exists() else ""
        )
        if on_disk_hash == bundled_sha:
            return False
        write_text_if_changed(DEFAULT_FILE_BACKEND, skill_file, bundled_content, encoding="utf-8")
        write_text_if_changed(
            DEFAULT_FILE_BACKEND,
            marker_file,
            json.dumps(managed_skill_marker(skill_name, installed_sha256=bundled_sha), indent=2)
            + "\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def install_project_baseline_skills(
    workspace_root: Path,
) -> tuple[CapabilityEntry, list[str]]:
    containment_failure = _project_root_outside_workspace(workspace_root)
    if containment_failure is not None:
        return (
            CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            ),
            [containment_failure],
        )
    canonical = project_skill_root(workspace_root)
    canonical_failures = _find_conflicts(canonical)
    if canonical_failures:
        return (
            CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            ),
            [f"skills-conflict-{name}" for name in canonical_failures],
        )
    try:
        materialize_skills_to_claude_dir(canonical)
    except OSError:
        return (
            CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            ),
            ["skills-materialize-failed"],
        )
    for skill_name in BASELINE_SKILL_NAMES:
        _materialize_canonical_skill(canonical, skill_name)
    # Prune any managed skill directory that the baseline no longer
    # ships. The materialize loop only touches BASELINE_SKILL_NAMES, so a
    # retired bundled skill (one whose name is no longer in the baseline)
    # stays on disk otherwise. Without this step, a stale retired skill
    # directory from a prior release would persist in the canonical and
    # every sibling symlink would continue to point at it. The prune is
    # source-aware and hash-aware: it removes a retired directory ONLY
    # when the directory's managed marker proves Ralph manages it AND its
    # content still matches the digest the marker recorded. A user-owned,
    # user-edited, universal-marked or malformed entry is preserved and
    # reported through ``skills-prune-preserved-*`` /
    # ``skills-prune-unknown-*``.
    canonical_prune = _prune_removed_baseline_skills(canonical)
    prune_failures = _prune_failure_codes(canonical_prune)
    # The canonical entries this pass removed are threaded into the
    # sibling passes so a broken sibling symlink can be attributed to the
    # Ralph-managed canonical entry the canonical prune just deleted (and
    # ONLY then: an unattributable broken link is preserved and reported).
    removed_targets = set(canonical_prune.removed_paths)
    sibling_failures: list[str] = []
    siblings: tuple[ProjectAgentSkillRoot, ...] = project_sibling_skill_roots(workspace_root)
    for sibling in siblings:
        sibling_root = sibling.resolve(workspace_root)
        sibling_root.mkdir(parents=True, exist_ok=True)
        # The sibling fan-out runs the same prune so a stale symlink
        # pointing at a now-removed canonical entry does not reappear
        # after the canonical prune.
        sibling_prune = _prune_removed_baseline_skills(
            sibling_root, removed_targets=frozenset(removed_targets)
        )
        removed_targets.update(sibling_prune.removed_paths)
        sibling_failures.extend(_prune_failure_codes(sibling_prune))
        for skill_name in BASELINE_SKILL_NAMES:
            failure = _materialize_project_sibling_dir(
                sibling_dir=sibling_root / skill_name,
                canonical_root=canonical,
                skill_name=skill_name,
            )
            if failure is not None:
                sibling_failures.append(failure)
    failures = _dedupe_failure_codes([*prune_failures, *sibling_failures])
    if failures:
        return (
            CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            ),
            failures,
        )
    self_improving_skills_hook(
        workspace_root=workspace_root,
        canonical_root=canonical,
    )
    return (
        CapabilityEntry(
            status=CapabilityStatus.INSTALLED_HEALTHY,
            last_check_ok_iso=_now_iso(),
        ),
        [],
    )


def self_improving_skills_hook(*, workspace_root: Path, canonical_root: Path) -> None:
    return None


def _resolve_within_workspace(path: Path, workspace_root: Path) -> Path | None:
    try:
        workspace_resolved = workspace_root.resolve(strict=False)
        path_resolved = path.resolve(strict=False)
    except OSError:
        return None
    if path_resolved == workspace_resolved:
        return path_resolved
    try:
        path_resolved.relative_to(workspace_resolved)
    except ValueError:
        return None
    return path_resolved


def _project_root_outside_workspace(
    workspace_root: Path,
) -> str | None:
    canonical = project_skill_root(workspace_root)
    if _resolve_within_workspace(canonical, workspace_root) is None:
        return "skills-outside-workspace-canonical"
    for sibling in project_sibling_skill_roots(workspace_root):
        sibling_root = sibling.resolve(workspace_root)
        if _resolve_within_workspace(sibling_root, workspace_root) is None:
            segment = "/".join(sibling.path_segments)
            return f"skills-outside-workspace-{segment}"
    return None


def _project_skills_need_install(workspace_root: Path) -> bool:
    reasons: list[str] = []
    _collect_project_skills_reasons(workspace_root, reasons)
    return bool(reasons)


def _collect_project_skills_reasons(workspace_root: Path, reasons: list[str]) -> None:
    if _project_root_outside_workspace(workspace_root) is not None:
        reasons.append("root-outside-workspace")
    canonical = project_skill_root(workspace_root)
    if not canonical.is_dir():
        reasons.append("canonical-not-directory")
        return
    if not _root_metadata_valid(canonical):
        reasons.append("canonical-metadata-invalid")
    for name in BASELINE_SKILL_NAMES:
        if not (canonical / name / "SKILL.md").exists():
            reasons.append(f"canonical-skill-missing-{name}")
            continue
        if not _root_skill_marker_valid(canonical, name):
            reasons.append(f"canonical-marker-invalid-{name}")
    canonical_resolved = canonical.resolve()
    for sibling in project_sibling_skill_roots(workspace_root):
        sibling_root = sibling.resolve(workspace_root)
        for name in BASELINE_SKILL_NAMES:
            sibling_dir = sibling_root / name
            if not sibling_dir.is_symlink():
                reasons.append(f"sibling-not-symlink-{sibling.agent}-{name}")
                continue
            canonical_target = (canonical_resolved / name).resolve()
            if sibling_dir.resolve() != canonical_target:
                reasons.append(f"sibling-wrong-target-{sibling.agent}-{name}")


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
    skill_file = resolved / name / "SKILL.md"
    if not marker.exists() or not skill_file.exists():
        return False
    try:
        recorded_raw: object = json.loads(marker.read_text(encoding="utf-8"))
        actual_hash = hashlib.sha256(skill_file.read_bytes()).hexdigest()
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(recorded_raw, dict):
        return False
    recorded = cast("dict[str, object]", recorded_raw)
    expected_hash = _compute_skill_hash(name)
    return (
        recorded.get("managed_by") == "ralph-workflow"
        and recorded.get("installed_content_sha256") == expected_hash
        and actual_hash == expected_hash
    )


def check_skills_update_available() -> bool:
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
    "ProjectSkillInstallOutcome",
    "_collect_project_skills_reasons",
    "_mirror_baseline_skills_to_siblings",
    "_mirror_skill_to_sibling_root",
    "_project_root_outside_workspace",
    "_project_skills_need_install",
    "_prune_removed_baseline_skills",
    "_resolve_within_workspace",
    "check_skills_update_available",
    "install_baseline_skills",
    "install_project_baseline_skills",
    "install_project_baseline_skills_with_diff",
    "self_improving_skills_hook",
]
