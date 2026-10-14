"""Operation-scoped evidence collector for retained-integration diagnostics.

The diagnostic path is the only authority that may consult Git state to
prove the evidence carried on :class:`IntegrationRecord.diagnostic_evidence`
has not drifted. Unrelated repository activity must NEVER invalidate the
operation being decided, so the collector reads ONLY the state owned by
the operation worktree:

* the per-worktree ``HEAD`` (worktree-private);
* the worktree path itself (catches worktree moves);
* the per-worktree index entries restricted to
  ``record.resolving_paths`` (never the full repository index);
* the per-worktree ``rebase-merge/`` and ``rebase-apply/`` files (live
  under the worktree-private gitdir);
* the per-worktree ``MERGE_HEAD``, ``MERGE_MSG``, ``REBASE_HEAD``,
  ``ORIG_HEAD``, ``AUTO_MERGE``, and ``CHERRY_PICK_HEAD``;
* the unstaged work and commit controls for repair operations;
* the ownership fingerprint derived from the durable record.

The collector EXCLUDES:

* ``git for-each-ref`` -- reads under commondir and observes every
  branch tip in the repository, including branches in unrelated
  worktrees;
* ``git ls-files --stage`` against the full index -- the per-worktree
  index is path-unscoped, so the digest changes when any tracked path
  in any worktree changes;
* the branch reflog (``logs/refs/heads/<branch>``) and ``logs/HEAD``
  -- they live under commondir and shift on every commit in every
  worktree.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline.operation_evidence_collector import OperationEvidenceCollector
from ralph.pipeline.operation_evidence_memory import InMemoryOperationEvidenceCollector
from ralph.pipeline.operation_fact import (
    _KIND_COMMIT_CONTROLS,
    _KIND_HEAD,
    _KIND_INDEX_SCOPE,
    _KIND_MERGE_STATE,
    _KIND_OWNERSHIP,
    _KIND_REBASE_STATE,
    _KIND_UNSTAGED_WORK,
    _KIND_WORKTREE_PATH,
    _UNREADABLE,
    OperationFact,
)

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

# Per-worktree rebase/merge state file names.
_REBASE_MERGE_FILE_NAMES: tuple[str, ...] = (
    "MERGE_HEAD",
    "MERGE_MSG",
    "REBASE_HEAD",
    "ORIG_HEAD",
    "AUTO_MERGE",
    "CHERRY_PICK_HEAD",
)


def ownership_fingerprint(record: IntegrationRecord) -> str:
    """SHA-256 fingerprint of the durable record minus its volatile fields.

    The fingerprint is the durable evidence the record's *identity*
    (target, pre-integration SHAs, operation kind) has not been
    changed underneath the diagnostic path.
    """
    stable = record.model_copy(
        update={
            "diagnostic_evidence": (),
            "diagnostic_ownership": None,
            "merge_commit_repair_attempts": 0,
            "repair_last_error": None,
        }
    )
    return hashlib.sha256(stable.model_dump_json().encode()).hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _should_capture_repair_facts(
    record: IntegrationRecord,
    observed: tuple[OperationFact, ...] | None = None,
) -> bool:
    if observed and any(
        fact.kind in (_KIND_UNSTAGED_WORK, _KIND_COMMIT_CONTROLS) for fact in observed
    ):
        return True
    return bool(
        record.resolving_merge
        or record.resolving_rebase
        or record.merge_commit_pending
        or record.rebase_continue_pending
        or record.repair_commit_controls is not None
        or record.repair_pending_diff is not None
        or record.diagnostic_evidence
    )


class SubprocessOperationEvidenceCollector:
    """The default collector: reads operation-scoped state through ``git``.

    Never invokes ``git for-each-ref`` (repository-global in a linked
    worktree), never reads the full per-worktree index, and never
    reads the branch reflog or ``logs/HEAD`` (common-dir state not
    owned by the operation worktree). Tests that need a different
    observation set should inject an in-memory collector instead.
    """

    def __init__(self, backend: FileBackend = DEFAULT_FILE_BACKEND) -> None:
        self._backend = backend

    def snapshot(
        self,
        root: Path,
        record: IntegrationRecord,
        *,
        capture_repair_facts: bool | None = None,
    ) -> tuple[OperationFact, ...]:
        facts: list[OperationFact] = [
            OperationFact(kind=_KIND_HEAD, value=_read_head(root)),
            OperationFact(kind=_KIND_WORKTREE_PATH, value=_read_worktree_path(root)),
            OperationFact(
                kind=_KIND_INDEX_SCOPE,
                value=_read_index_scope(root, record.resolving_paths),
            ),
            OperationFact(
                kind=_KIND_OWNERSHIP,
                value=ownership_fingerprint(record),
            ),
        ]
        facts.extend(_read_rebase_state_files(root))
        facts.extend(_read_merge_state_files(root))
        should_repair = (
            capture_repair_facts
            if capture_repair_facts is not None
            else _should_capture_repair_facts(record)
        )
        if should_repair:
            facts.append(_read_unstaged_work(root))
            facts.append(_read_commit_controls(root, self._backend))
        return tuple(facts)

    def verify(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> bool:
        if not observed or any(fact.value == _UNREADABLE for fact in observed):
            return False
        current = self.snapshot(
            root,
            record,
            capture_repair_facts=_should_capture_repair_facts(record, observed),
        )
        if any(fact.value == _UNREADABLE for fact in current):
            return False
        current_map = {fact.kind: fact.value for fact in current}
        current_scope: dict[tuple[str, str | None], str] = {
            (fact.kind, fact.scope): fact.value for fact in current
        }
        return all(
            _fact_matches(
                fact,
                current_map.get(fact.kind)
                if fact.scope is None
                else current_scope.get((fact.kind, fact.scope)),
            )
            for fact in observed
        )


def _fact_matches(fact: OperationFact, expected: str | None) -> bool:
    return (
        expected is not None
        and _UNREADABLE not in (expected, fact.value)
        and expected == fact.value
    )


def _read_worktree_path(root: Path) -> str:
    if not DEFAULT_FILE_BACKEND.exists(root):
        return _UNREADABLE
    return str(root)


def _read_head(root: Path) -> str:
    if not DEFAULT_FILE_BACKEND.exists(root):
        return _UNREADABLE
    from ralph.git.subprocess_runner import run_git

    try:
        result = run_git(
            ("rev-parse", "--verify", "HEAD"),
            cwd=root,
            label="operation_evidence:head",
        )
    except (OSError, RuntimeError):
        return _UNREADABLE
    if result.returncode:
        return _UNREADABLE
    return result.stdout.strip() or _UNREADABLE


def _read_index_scope(root: Path, resolving_paths: tuple[str, ...]) -> str:
    if not resolving_paths:
        return "absent"
    if not DEFAULT_FILE_BACKEND.exists(root):
        return _UNREADABLE
    from ralph.git.subprocess_runner import run_git

    try:
        result = run_git(
            ("ls-files", "--stage", "-z", "--", *resolving_paths),
            cwd=root,
            label="operation_evidence:index_scope",
        )
    except (OSError, RuntimeError):
        return _UNREADABLE
    if result.returncode:
        return _UNREADABLE
    return _hash_text(result.stdout)


def _read_rebase_state_files(root: Path) -> tuple[OperationFact, ...]:
    """Read the per-worktree ``rebase-merge/`` and ``rebase-apply/`` files."""
    if not DEFAULT_FILE_BACKEND.exists(root):
        return (OperationFact(kind=_KIND_REBASE_STATE, value=_UNREADABLE),)
    from ralph.git.subprocess_runner import run_git

    try:
        metadata = run_git(
            ("rev-parse", "--absolute-git-dir"),
            cwd=root,
            label="operation_evidence:gitdir",
        )
    except (OSError, RuntimeError):
        return (OperationFact(kind=_KIND_REBASE_STATE, value=_UNREADABLE),)
    if metadata.returncode:
        return (OperationFact(kind=_KIND_REBASE_STATE, value=_UNREADABLE),)
    gitdir = Path(metadata.stdout.strip())
    try:
        paths = set(DEFAULT_FILE_BACKEND.glob(gitdir, "rebase-merge/*"))
        paths.update(DEFAULT_FILE_BACKEND.glob(gitdir, "rebase-apply/*"))
    except OSError:
        return (OperationFact(kind=_KIND_REBASE_STATE, value=_UNREADABLE),)
    facts: list[OperationFact] = [
        OperationFact(
            kind=_KIND_REBASE_STATE,
            scope=str(path),
            value=_read_file_value(path),
        )
        for path in sorted(paths)
    ]
    return tuple(facts)


def _read_merge_state_files(root: Path) -> tuple[OperationFact, ...]:
    """Read the per-worktree ``MERGE_HEAD`` / ``MERGE_MSG`` / etc. files."""
    if not DEFAULT_FILE_BACKEND.exists(root):
        return (OperationFact(kind=_KIND_MERGE_STATE, value=_UNREADABLE),)
    from ralph.git.subprocess_runner import run_git

    try:
        metadata = run_git(
            ("rev-parse", "--absolute-git-dir"),
            cwd=root,
            label="operation_evidence:gitdir",
        )
    except (OSError, RuntimeError):
        return (OperationFact(kind=_KIND_MERGE_STATE, value=_UNREADABLE),)
    if metadata.returncode:
        return (OperationFact(kind=_KIND_MERGE_STATE, value=_UNREADABLE),)
    gitdir = Path(metadata.stdout.strip())
    facts: list[OperationFact] = [
        OperationFact(
            kind=_KIND_MERGE_STATE,
            scope=str(gitdir / name),
            value=_read_file_value(gitdir / name),
        )
        for name in _REBASE_MERGE_FILE_NAMES
    ]
    return tuple(facts)


def _read_unstaged_work(root: Path) -> OperationFact:
    if not DEFAULT_FILE_BACKEND.exists(root):
        return OperationFact(kind=_KIND_UNSTAGED_WORK, value=_UNREADABLE)
    from ralph.pipeline._pending_repair_edits import capture_unstaged_work

    try:
        unstaged = hashlib.sha256(
            repr(sorted(capture_unstaged_work(root).items())).encode()
        ).hexdigest()
    except Exception:
        unstaged = _UNREADABLE
    return OperationFact(kind=_KIND_UNSTAGED_WORK, value=unstaged)


def _read_commit_controls(
    root: Path,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> OperationFact:
    if not DEFAULT_FILE_BACKEND.exists(root):
        return OperationFact(kind=_KIND_COMMIT_CONTROLS, value=_UNREADABLE)
    from ralph.pipeline._pending_repair_edits import capture_commit_controls

    try:
        controls = hashlib.sha256(capture_commit_controls(root, backend).encode()).hexdigest()
    except Exception:
        controls = _UNREADABLE
    return OperationFact(kind=_KIND_COMMIT_CONTROLS, value=controls)


def _read_file_value(path: Path) -> str:
    if not DEFAULT_FILE_BACKEND.exists(path):
        return "absent"
    try:
        return _hash_text(DEFAULT_FILE_BACKEND.read_bytes(path).decode("utf-8", errors="replace"))
    except OSError:
        return _UNREADABLE


__all__ = [
    "InMemoryOperationEvidenceCollector",
    "OperationEvidenceCollector",
    "OperationFact",
    "SubprocessOperationEvidenceCollector",
    "ownership_fingerprint",
]
