"""The four-condition positive operation-completion proof.

The diagnostic path must not accept an agent's success as proof that the
Git operation finished. It proves the operation against live Git with
four independent conditions (ADR-0008 section 3):

1. **Commit ancestry proof** -- ``HEAD`` matches the record's expected
   SHA and ``HEAD``'s parents match the expected parent SHAs.
2. **Operation ownership proof** -- the record's owning worktree is the
   inspected root and the held diagnostic lease matches the record's
   durable identity.
3. **Clean index and tree proof** -- no ``MERGE_HEAD`` / ``REBASE_HEAD``
   marker, no unmerged index entries, and a clean worktree diff.
4. **Durable atomic transition proof** -- the record is in the
   integrated phase and its diagnostic lease is still trustworthy.

Every helper in this module is read-only on Git: it never deletes or
mutates ``MERGE_HEAD``, ``REBASE_HEAD``, ``ORIG_HEAD``, the index, or
worktree files.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline._integration_diagnostic_evidence import (
    diagnostic_evidence_verdict,
    snapshot_has_unreadable,
)
from ralph.pipeline.auto_integrate_record import parse_lease
from ralph.pipeline.operation_evidence import EvidenceVerdict, identity_fingerprint

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.operation_evidence import OperationEvidenceCollector

def _check_head_match(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    from ralph.git.subprocess_runner import run_git

    try:
        head_res = run_git(
            ("rev-parse", "--verify", "HEAD"),
            cwd=root,
            label="integration_diagnostic:verify_head",
        )
    except (OSError, RuntimeError) as exc:
        return False, f"HEAD rev-parse failed: {exc}"
    if head_res.returncode != 0:
        return False, "HEAD is unreadable"
    head = head_res.stdout.strip()
    allowed_heads = {
        record.integrated_feature_sha,
        record.pre_target_sha,
        record.pre_feature_sha,
    } - {None, ""}
    if head not in allowed_heads:
        return (
            False,
            f"HEAD {head} does not match integrated_feature_sha, pre_target_sha, or pre_feature_sha",
        )
    return True, "HEAD matches"


def _check_parent_match(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    from ralph.git.subprocess_runner import run_git

    try:
        parents_res = run_git(
            ("show", "-s", "--format=%P", "HEAD"),
            cwd=root,
            label="integration_diagnostic:head_parents",
        )
    except (OSError, RuntimeError) as exc:
        return False, f"parents rev-parse failed: {exc}"
    if parents_res.returncode != 0:
        return False, "parents of HEAD are unreadable"
    parents = parents_res.stdout.strip().split()

    if record.merge_commit_parent:
        if record.merge_commit_parent not in parents:
            return (
                False,
                f"parent of HEAD does not match merge_commit_parent {record.merge_commit_parent}",
            )
    elif record.pre_target_sha and record.pre_target_sha not in parents:
        try:
            ancestor_res = run_git(
                ("merge-base", "--is-ancestor", record.pre_target_sha, "HEAD"),
                cwd=root,
                label="integration_diagnostic:check_ancestor",
            )
            if ancestor_res.returncode != 0:
                return (
                    False,
                    f"pre_target_sha {record.pre_target_sha} is not an ancestor of HEAD",
                )
        except (OSError, RuntimeError) as exc:
            return False, f"ancestry check failed: {exc}"
    return True, "parents verified"


def _check_ancestry_proof(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    ok, reason = _check_head_match(root, record)
    if not ok:
        return False, reason
    return _check_parent_match(root, record)


def _check_worktree_match(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    if not record.owning_worktree or not record.owning_worktree.strip():
        return False, "record.owning_worktree is missing or empty"
    try:
        if Path(record.owning_worktree).resolve() != root.resolve():
            return False, f"owning_worktree {record.owning_worktree} does not match {root}"
    except Exception:
        return False, f"owning_worktree {record.owning_worktree} is invalid"
    return True, "worktree matches"


def _check_ownership_proof(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    ok, reason = _check_worktree_match(root, record)
    if not ok:
        return False, reason
    if not record.diagnostic_ownership:
        return False, "diagnostic_ownership is missing or empty"
    fingerprint, held_at = parse_lease(record.diagnostic_ownership)
    if fingerprint is None or held_at is None or fingerprint != identity_fingerprint(record):
        return False, "diagnostic_ownership does not match ownership_fingerprint"
    return True, "ownership verified"


def _check_marker_ref_proof(root: Path, ref_name: str) -> tuple[bool, str]:
    from ralph.git.subprocess_runner import run_git

    try:
        res = run_git(
            ("rev-parse", "--verify", "--quiet", ref_name),
            cwd=root,
            label=f"integration_diagnostic:check_{ref_name.lower()}",
        )
        if res.returncode == 0:
            return False, f"{ref_name} is present"
        if res.returncode != 1:
            return False, f"{ref_name} status is UNKNOWN (unexpected exit code {res.returncode})"
    except (OSError, RuntimeError) as exc:
        return False, f"{ref_name} status is UNKNOWN ({exc})"
    return True, f"{ref_name} absent"


def _check_gitdir_markers_proof(root: Path) -> tuple[bool, str]:
    from ralph.git.subprocess_runner import run_git

    try:
        gitdir_res = run_git(
            ("rev-parse", "--absolute-git-dir"),
            cwd=root,
            label="integration_diagnostic:gitdir",
        )
        if gitdir_res.returncode != 0:
            return (
                False,
                f"gitdir query returned unexpected exit code {gitdir_res.returncode}: UNKNOWN",
            )
        gitdir = Path(gitdir_res.stdout.strip())
        if (gitdir / "MERGE_HEAD").exists():
            return False, "MERGE_HEAD is present"
        if (gitdir / "REBASE_HEAD").exists():
            return False, "REBASE_HEAD is present"
        if (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists():
            return False, "rebase state directory is present"
    except (OSError, RuntimeError) as exc:
        return False, f"gitdir check failed: {exc} (UNKNOWN)"
    return True, "gitdir markers absent"


def _check_clean_diff_proof(root: Path) -> tuple[bool, str]:
    from ralph.git.subprocess_runner import run_git

    try:
        diff_res = run_git(
            ("diff", "--quiet", "HEAD"),
            cwd=root,
            label="integration_diagnostic:diff_head",
        )
        if diff_res.returncode == 1:
            return False, "git diff against HEAD is non-empty"
        if diff_res.returncode != 0:
            return False, f"git diff failed with exit code {diff_res.returncode}: UNKNOWN"
    except (OSError, RuntimeError) as exc:
        return False, f"git diff failed: {exc} (UNKNOWN)"
    return True, "diff against HEAD clean"


def _check_index_tree_proof(root: Path) -> tuple[bool, str]:
    from ralph.git.merge import unmerged_paths

    for ref in ("MERGE_HEAD", "REBASE_HEAD"):
        ok, reason = _check_marker_ref_proof(root, ref)
        if not ok:
            return False, reason

    ok, reason = _check_gitdir_markers_proof(root)
    if not ok:
        return False, reason

    unmerged = unmerged_paths(root)
    if unmerged or "<unmerged-path-query-failed>" in unmerged:
        return False, "unmerged paths in index or unmerged query failed"

    ok, reason = _check_clean_diff_proof(root)
    if not ok:
        return False, reason

    return True, "index and tree verified"


def _has_phase_proof(record: IntegrationRecord) -> bool:
    return record.phase == "integrated" or (
        record.phase == "integrating" and bool(record.integrated_feature_sha)
    )


def _check_atomic_transition_proof(
    record: IntegrationRecord,
    root: Path | None = None,
    collector: OperationEvidenceCollector | None = None,
) -> tuple[bool, str]:
    has_valid_phase = _has_phase_proof(record)
    if not has_valid_phase:
        return False, f"record.phase is '{record.phase}', expected 'integrated'"
    effective_root = (
        root
        if root is not None
        else (Path(record.owning_worktree) if record.owning_worktree else Path.cwd())
    )
    verdict = diagnostic_evidence_verdict(effective_root, record, collector=collector)
    if verdict == EvidenceVerdict.UNKNOWN:
        return False, "diagnostic evidence status is UNKNOWN (observations unreadable)"
    if verdict != EvidenceVerdict.UNCHANGED:
        return False, "diagnostic evidence does not match release conditions"
    # Even when the held lease is valid, the live snapshot may carry
    # UNREADABLE observations (e.g. a half-rewritten gitdir, a
    # half-removed marker). The proof refuses to release while the
    # live state cannot be observed; the diagnostic re-derives on the
    # next seam.
    if snapshot_has_unreadable(effective_root, record):
        return False, "diagnostic evidence status is UNKNOWN (observations unreadable)"
    return True, "atomic transition verified"


def _check_operation_completion_proof(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> tuple[bool, str]:
    checks = (
        _check_ancestry_proof(root, record),
        _check_ownership_proof(root, record),
        _check_index_tree_proof(root),
        _check_atomic_transition_proof(record, root=root, collector=collector),
    )
    for ok, reason in checks:
        if not ok:
            return False, reason
    return True, "positive operation-completion proof satisfied"
