"""Baseline skill bundle installation and update checks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

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

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.skills._project_paths import ProjectAgentSkillRoot


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


# --- Project-scope baseline skill installation --------------------------------
#
# The project scope is a separate fan-out (./.opencode/skills/ canonical +
# 3 project siblings). Same precedence contract as the user-global install
# above: canonical is all-or-nothing, sibling failures are appended flatly.
# The project canonical NEVER appears in the sibling list (see PA-007 in
# _agent_paths.py).


def _materialize_project_sibling_dir(
    sibling_dir: Path, canonical_root: Path, skill_name: str
) -> str | None:
    """Materialize a single project-scope sibling entry as a symlink to the canonical.

    Returns ``None`` on success or a failure code on conflict / hard failure.
    Uses ``Path.resolve()``-aware checks for macOS ``/tmp`` -> ``/private/tmp``
    indirection safety (PA-007).
    """
    skill_file = sibling_dir / "SKILL.md"
    marker = sibling_dir / _MANAGED_MARKER
    if skill_file.exists() and not marker.exists():
        try:
            existing = skill_file.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        if existing != get_skill_content(skill_name):
            return f"sibling-conflict-{skill_name}"

    sibling_dir.parent.mkdir(parents=True, exist_ok=True)
    if sibling_dir.is_symlink():
        sibling_dir.unlink()
    elif sibling_dir.is_dir():
        shutil.rmtree(sibling_dir)
    elif sibling_dir.exists():
        sibling_dir.unlink()

    canonical_target = canonical_root / skill_name
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
    """Overwrite a single project-scope canonical skill with the bundled content.

    Project-scope pre-pipeline sync (driven by ``_sync_shipped_skills_on_pipeline_run``)
    requires a deterministic, single-pass overwrite of stale bundled content so the
    auto-commit helper has exactly one diff to commit. This helper honours the
    user-edit preservation contract as follows:

      (1) First call ``materialize_skills_to_claude_dir(canonical)`` so every
          skill whose stored ``.ralph-managed.json`` sha matches the on-disk
          sha (i.e. the user has not edited it since install) is rewritten
          with the bundled content. Skills whose stored sha DOES NOT match
          the on-disk sha are preserved as user-edited (the existing contract
          from ``materialize_skills_to_claude_dir``).
      (2) Then for THIS ``skill_name`` specifically, compare the on-disk
          ``SKILL.md`` hash against the bundled
          ``hashlib.sha256(get_skill_content(skill_name).encode()).hexdigest()``.
          If they still differ (meaning ``materialize_skills_to_claude_dir``
          preserved the skill as user-edited, OR the bundled content has since
          been refreshed and the on-disk copy is older), overwrite the
          ``SKILL.md`` and the managed marker with the bundled content. This
          reconciles the prompt's explicit rule 'If there is a conflict,
          simply replace the old skill with new skill' with the existing
          user-edit preservation contract: the user-global path remains
          signal-only (see ``install_baseline_skills`` / ``SkillManager``),
          but the project-scope pre-pipeline sync always wins for the
          bundled SKILL.md content vs on-disk hash mismatch.

    Args:
        canonical: Resolved project-canonical skill directory
            (e.g. ``workspace_root / '.opencode' / 'skills'``).
        skill_name: Name of the skill whose canonical entry to overwrite.

    Returns:
        True when at least one of ``SKILL.md`` or the managed marker was
        overwritten with bundled content; False when the on-disk content
        already matches the bundled hash.

    The helper is fail-closed: any ``OSError`` during read/write returns
    False so the caller can treat overwrite failures as 'no change' and
    still proceed with the rest of the install / auto-commit pipeline.
    """
    try:
        materialize_skills_to_claude_dir(canonical)
        bundled_content = get_skill_content(skill_name)
        bundled_sha = hashlib.sha256(bundled_content.encode("utf-8")).hexdigest()
        skill_dir = canonical / skill_name
        skill_file = skill_dir / "SKILL.md"
        marker_file = skill_dir / _MANAGED_MARKER
        skill_dir.mkdir(parents=True, exist_ok=True)
        on_disk_hash = (
            hashlib.sha256(skill_file.read_bytes()).hexdigest()
            if skill_file.exists()
            else ""
        )
        if on_disk_hash == bundled_sha:
            return False
        skill_file.write_text(bundled_content, encoding="utf-8")
        marker_file.write_text(
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
    """Install the baseline skill bundle into the project scope.

    Mirrors the user-global ``install_baseline_skills`` precedence contract:
      (1) If the project-canonical has conflicting user-owned skill files,
          return ``NEEDS_REPAIR`` with ``skills-conflict-*`` failures and
          DO NOT materialize or touch any sibling. The user keeps their
          files untouched.
      (2) If canonical conflict detection passes, materialize the bundle
          into the project-canonical root. On ``OSError`` during materialize,
          return ``NEEDS_REPAIR`` with ``["skills-materialize-failed"]`` and
          DO NOT touch any sibling.
      (3) If canonical materialize succeeds, OVERWRITE any REMAINING
          hash-divergent skill with the bundled content via
          ``_materialize_canonical_skill`` so the auto-commit has exactly
          one diff to commit. This implements the project-scope branch of
          the locked conflict-resolution policy: bundled content always
          wins for project-scope pre-pipeline sync (the user-global path
          remains signal-only per ``install_baseline_skills`` /
          ``SkillManager.check_skills_for_updates``).
      (4) Fan out the canonical to the 3 project-scope siblings (claude,
          codex, agy). Any sibling failure
          (``sibling-conflict-*`` / ``sibling-materialize-failed-*``) is
          appended to a flat ``failures`` list.

    CONTAINMENT GATE: a pre-flight ``_project_root_outside_workspace``
    check runs FIRST (before any conflict detection, materialize, or
    sibling fan-out). When the canonical (``.opencode/skills``) or any
    sibling root resolves OUTSIDE ``workspace_root`` (e.g. the user
    pre-created ``./.agents`` as a symlink to an external directory),
    the install returns ``NEEDS_REPAIR`` with a single
    ``skills-outside-workspace-<segment>`` failure code and DOES NOT
    touch the filesystem. The user must manually remove the misdirected
    symlink before the install can run safely. This prevents the
    pre-pipeline sync from silently writing skill files into a
    directory outside the project workspace.

    Returns ``(CapabilityEntry, failures)`` — the failures list is NEVER a
    mix of canonical and sibling codes (the containment gate uses its
    own dedicated code family).
    """
    # Containment gate (analyzed bug): fail closed BEFORE any filesystem
    # mutation when a pre-existing project skill root (canonical OR
    # sibling) resolves outside ``workspace_root``. A symlink at
    # ``workspace_root/.opencode`` (or any sibling root) pointing to an
    # external directory would otherwise cause the install to silently
    # write skill files into that external directory and return
    # INSTALLED_HEALTHY. The user must manually remove the misdirected
    # symlink before this function can run safely.
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
    # Project-scope bundle-update reconciliation (wt-025): after the
    # user-edit-preserving materialize pass, overwrite any REMAINING
    # hash-divergent skill with the bundled content so the auto-commit
    # has exactly one diff to commit. The user-global path
    # (install_baseline_skills) intentionally remains signal-only per
    # the locked conflict-resolution policy.
    for skill_name in BASELINE_SKILL_NAMES:
        _materialize_canonical_skill(canonical, skill_name)
    sibling_failures: list[str] = []
    siblings: tuple[ProjectAgentSkillRoot, ...] = project_sibling_skill_roots(workspace_root)
    for sibling in siblings:
        sibling_root = sibling.resolve(workspace_root)
        sibling_root.mkdir(parents=True, exist_ok=True)
        for skill_name in BASELINE_SKILL_NAMES:
            failure = _materialize_project_sibling_dir(
                sibling_dir=sibling_root / skill_name,
                canonical_root=canonical,
                skill_name=skill_name,
            )
            if failure is not None:
                sibling_failures.append(failure)
    if sibling_failures:
        return (
            CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            ),
            sibling_failures,
        )
    # FUTURE: self-improving skills hook goes here — see docs/sphinx/agents.md
    # §'Self-improving skills (future)'. The hook will fire after every
    # project-canonical+symlink fan-out and let agents write back improvements
    # to .opencode/skills/<name>/SKILL.md with a prompt-confirmation gate.
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
    """No-op placeholder for the self-improving skills hook.

    The future design calls agents back after every project-canonical +
    sibling-symlink fan-out and lets them write back improvements to
    ``./.opencode/skills/<name>/SKILL.md`` (sibling symlinks pick up the
    change automatically because they are symlinks into the canonical).
    A prompt-confirmation gate is the documented contract: the agent
    MUST ask the user before mutating any ``SKILL.md``; this hook is the
    single chokepoint where the prompt-confirmation step will be added.

    Project-scope only by design; user-global is intentionally not wired
    in this iteration to avoid silent home-dir mutation. See
    ``docs/sphinx/agents.md`` §'Self-improving skills (future)' for the
    full scope-decision rationale.

    Args:
        workspace_root: Project workspace root.
        canonical_root: Resolved canonical skills root for the project
            (e.g. ``workspace_root / ".opencode" / "skills"``).
    """
    return None


def _resolve_within_workspace(path: Path, workspace_root: Path) -> Path | None:
    """Resolve ``path`` and verify it stays within ``workspace_root``.

    Returns the resolved path when containment holds; ``None`` when
    ``path`` resolves to a location outside ``workspace_root`` (e.g. a
    pre-existing project skill root is a symlink to an external directory,
    or the workspace_root itself cannot be resolved).

    Uses ``Path.resolve(strict=False)`` on BOTH sides so macOS
    ``/tmp`` -> ``/private/tmp`` symlink indirection is normalized
    before the comparison. Returns ``None`` on ``OSError`` so a broken
    state never propagates and silently bypasses the safety check.
    """
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
    """Return a failure code when a project skill root resolves outside the workspace.

    Iterates the canonical (``.opencode/skills``) AND every sibling root
    (``project_sibling_skill_roots``) and returns the first
    ``skills-outside-workspace-<segment>`` code it finds. Returns ``None``
    when every root resolves within the workspace. Used by BOTH
    ``install_project_baseline_skills`` (to fail-closed before any
    filesystem mutation) and ``_project_skills_need_install`` (to surface
    the misdirected tree as needing repair).
    """
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
    """Return True when the project-scope install should run.

    Precise 'missing' predicate — the canonical must be a real directory
    (PA-005: an ``is_dir()`` check explicitly guards the silent-misclassification
    case where a regular file sits at the canonical path), every baseline
    skill must have a SKILL.md under the canonical, and every baseline
    skill must be a symlink under every project sibling AND that
    symlink must point to the canonical skill entry for the same skill.

    Two NEW containment / target checks added for the external-symlink
    and wrong-target regression coverage (analysis feedback):

      1. CONTAINMENT (analyzed bug): every project skill root
         (``.opencode/skills`` AND every sibling) MUST resolve within
         ``workspace_root``. A pre-existing symlink that points outside
         the workspace is treated as 'install needed' so the repair
         path fires and the symlink can be replaced. Mirrored by
         ``install_project_baseline_skills`` which fails closed as
         NEEDS_REPAIR (the user must manually fix the misdirected
         symlink before the install can run safely).

      2. WRONG-TARGET (analyzed bug): every sibling skill symlink MUST
         resolve to the canonical skill entry for the same skill name.
         A sibling symlink pointing at a wrong canonical (e.g. a stale
         or malicious target) is treated as 'install needed' so the
         repair path re-creates the symlink.
    """
    reasons: list[str] = []
    _collect_project_skills_reasons(workspace_root, reasons)
    return bool(reasons)


def _collect_project_skills_reasons(workspace_root: Path, reasons: list[str]) -> None:
    """Append a reason string for every predicate miss in ``_project_skills_need_install``.

    The predicate is 'install needed when at least one reason is present'.
    Splitting collection from the boolean return keeps the per-check
    semantics clear and lets the caller surface a list of missing pieces
    for diagnostics without duplicating the iteration logic.
    """
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
    "_collect_project_skills_reasons",
    "_mirror_baseline_skills_to_siblings",
    "_mirror_skill_to_sibling_root",
    "_project_root_outside_workspace",
    "_project_skills_need_install",
    "_resolve_within_workspace",
    "check_skills_update_available",
    "install_baseline_skills",
    "install_project_baseline_skills",
    "self_improving_skills_hook",
]
