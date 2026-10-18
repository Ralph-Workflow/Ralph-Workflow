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
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline.operation_evidence_collector import (
    OperationEvidenceCollector,
)
from ralph.pipeline.operation_evidence_memory import InMemoryOperationEvidenceCollector
from ralph.pipeline.operation_evidence_verdict import EvidenceVerdict
from ralph.pipeline.operation_fact import (
    _KIND_COMMIT_CONTROLS,
    _KIND_HEAD,
    _KIND_INDEX_SCOPE,
    _KIND_MERGE_OBJECT_RESOLVABLE,
    _KIND_MERGE_STATE,
    _KIND_OWNERSHIP,
    _KIND_REBASE_OBJECT_RESOLVABLE,
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

    All fields that the diagnostic path itself owns are excluded so
    a legitimate diagnostic-path transition (``RAISE_DIAGNOSTIC_INTERVENTION``,
    ``BUMP_DIAGNOSTIC_ATTEMPT``, ``HOLD_DIAGNOSTIC_EVIDENCE``,
    ``RELEASE_DIAGNOSTIC_EVIDENCE``) does not alter the fingerprint
    it is measured against. The fingerprint therefore only changes
    when an external party (not the diagnostic path) edits the
    record's identity fields, which is what the diagnostic
    re-read check is meant to detect.
    """
    return identity_fingerprint(record)


#: Closed set of identity fields used by :func:`identity_fingerprint`.
#: Adding a new defaulted field to :class:`IntegrationRecord` MUST NOT
#: extend this tuple; the fingerprint is structurally isolated from
#: record-schema changes. Each field's role in the durable identity is
#: documented inline.
#:
#: The set is narrowed to the structurally stable trio
#: ``target`` / ``operation_kind`` / ``owning_worktree`` (AC-4). A
#: successful integration legitimately transitions ``phase`` to
#: ``"integrated"``, sets ``integrated_feature_sha``, clears
#: ``reintegrate_pending``, and may update ``pre_target_sha``; those
#: transitions describe the record's lifecycle, not the durable
#: identity of the operation. The diagnostic path re-derives
#: state from Git on the next seam when the lease is still held;
#: HEAD / index / reflog / worktree path are already excluded by
#: construction (see the function docstring below).
_IDENTITY_FINGERPRINT_FIELDS: tuple[str, ...] = (
    "target",
    "operation_kind",
    "owning_worktree",
)


def identity_fingerprint(record: IntegrationRecord) -> str:
    """SHA-256 fingerprint over the record's CLOSED identity field set.

    The fingerprint is the durable evidence the record's *identity*
    (target, pre-integration SHAs, operation kind, owning worktree)
    has not been changed underneath the diagnostic path. The set of
    fields is closed: a future contributor who adds a new defaulted
    field to :class:`IntegrationRecord` cannot change the fingerprint,
    because the new field is not in :data:`_IDENTITY_FINGERPRINT_FIELDS`.

    The diagnostic path uses the fingerprint as the lease identifier:
    a record whose ``diagnostic_ownership`` carries an equal
    fingerprint is held by the same durable identity. State that
    Ralph itself mutates (HEAD, the index, reflog, the worktree path)
    is NOT in the fingerprint, so a legitimate fast-forward or
    snapshot/reset never invalidates the lease.
    """
    payload: dict[str, object] = {
        field: cast("object", getattr(record, field)) for field in _IDENTITY_FINGERPRINT_FIELDS
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


#: Closed vocabulary of kinds the operation-scoped collector is allowed
#: to emit. Any kind outside this set is rejected by the snapshot
#: boundary so a future contributor cannot silently reintroduce a
#: repository-global key (the production bug the architecture fix
#: eliminated).
_CLOSED_OPERATION_FACT_KINDS: frozenset[str] = frozenset(
    {
        _KIND_HEAD,
        _KIND_WORKTREE_PATH,
        _KIND_INDEX_SCOPE,
        _KIND_REBASE_STATE,
        _KIND_MERGE_STATE,
        _KIND_OWNERSHIP,
        _KIND_UNSTAGED_WORK,
        _KIND_COMMIT_CONTROLS,
        _KIND_MERGE_OBJECT_RESOLVABLE,
        _KIND_REBASE_OBJECT_RESOLVABLE,
    }
)


def _is_closed_kind(kind: str) -> bool:
    """Return True if ``kind`` is one of the allowed operation-scoped kinds."""
    return kind in _CLOSED_OPERATION_FACT_KINDS


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
    if any(
        item[0] in (_KIND_UNSTAGED_WORK, _KIND_COMMIT_CONTROLS)
        for item in record.diagnostic_evidence
        if isinstance(item, tuple) and item
    ):
        return True
    return bool(
        classify_git_operation(record) is not GitOperationKind.NONE
        or record.repair_commit_controls is not None
        or record.repair_pending_diff is not None
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
        observed: tuple[OperationFact, ...] | None = None,
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
        facts.extend(_read_reachability_facts(root, record, observed))
        # Belt-and-braces: the collector is the only writer of operation-
        # scoped evidence, and only the closed ``_KIND_*`` set is allowed.
        # A future contributor cannot reintroduce a repository-global key
        # without a fail-closed RuntimeError; the legacy loader already
        # refuses records carrying ``_LEGACY_GLOBAL_EVIDENCE_KEYS`` so
        # the same surface cannot be reintroduced on either side.
        for fact in facts:
            if not _is_closed_kind(fact.kind):
                raise RuntimeError(
                    f"OperationEvidenceCollector emitted forbidden kind {fact.kind!r}; "
                    "only the closed _KIND_* set is allowed."
                )
        return tuple(facts)

    def evaluate(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> EvidenceVerdict:
        if not observed:
            return EvidenceVerdict.CHANGED
        if any(fact.value == _UNREADABLE for fact in observed):
            return EvidenceVerdict.UNKNOWN
        for fact in observed:
            if fact.kind in (
                _KIND_MERGE_OBJECT_RESOLVABLE,
                _KIND_REBASE_OBJECT_RESOLVABLE,
            ) and fact.value in ("False", "false"):
                return EvidenceVerdict.CHANGED
        current = self.snapshot(
            root,
            record,
            capture_repair_facts=_should_capture_repair_facts(record, observed),
            observed=observed,
        )
        if any(fact.value == _UNREADABLE for fact in current):
            return EvidenceVerdict.UNKNOWN
        for fact in current:
            if fact.kind in (
                _KIND_MERGE_OBJECT_RESOLVABLE,
                _KIND_REBASE_OBJECT_RESOLVABLE,
            ) and fact.value in ("False", "false"):
                return EvidenceVerdict.CHANGED
        current_map = {fact.kind: fact.value for fact in current}
        current_scope: dict[tuple[str, str | None], str] = {
            (fact.kind, fact.scope): fact.value for fact in current
        }
        all_match = all(
            _fact_matches(
                fact,
                current_map.get(fact.kind)
                if fact.scope is None
                else current_scope.get((fact.kind, fact.scope)),
            )
            for fact in observed
        )
        return EvidenceVerdict.UNCHANGED if all_match else EvidenceVerdict.CHANGED

    def verify(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> bool:
        return self.evaluate(root, record, observed) == EvidenceVerdict.UNCHANGED


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


def _read_reachability_facts(
    root: Path,
    record: IntegrationRecord,
    observed: tuple[OperationFact, ...] | None = None,
) -> tuple[OperationFact, ...]:
    from ralph.git.subprocess_runner import run_git

    if not DEFAULT_FILE_BACKEND.exists(root):
        return ()
    try:
        metadata = run_git(
            ("rev-parse", "--absolute-git-dir"),
            cwd=root,
            label="operation_evidence:gitdir:reachability",
        )
    except (OSError, RuntimeError):
        return ()
    if metadata.returncode:
        return ()
    gitdir = Path(metadata.stdout.strip())

    facts: list[OperationFact] = []
    should_check_merge = (
        DEFAULT_FILE_BACKEND.exists(gitdir / "MERGE_HEAD")
        or classify_git_operation(record)
        in (GitOperationKind.RESOLVING_MERGE, GitOperationKind.AWAITING_MERGE_COMMIT)
        or (observed is not None and any(f.kind == _KIND_MERGE_OBJECT_RESOLVABLE for f in observed))
    )
    if should_check_merge:
        facts.append(_read_object_resolvable(root, "MERGE_HEAD", _KIND_MERGE_OBJECT_RESOLVABLE))

    should_check_rebase = (
        DEFAULT_FILE_BACKEND.exists(gitdir / "REBASE_HEAD")
        or classify_git_operation(record)
        in (GitOperationKind.RESOLVING_REBASE, GitOperationKind.AWAITING_CONTINUE)
        or (
            observed is not None and any(f.kind == _KIND_REBASE_OBJECT_RESOLVABLE for f in observed)
        )
    )
    if should_check_rebase:
        facts.append(_read_object_resolvable(root, "REBASE_HEAD", _KIND_REBASE_OBJECT_RESOLVABLE))

    return tuple(facts)


def _read_marker_sha(root: Path, file_name: str) -> str | None:
    from ralph.git.subprocess_runner import run_git

    if not DEFAULT_FILE_BACKEND.exists(root):
        return None
    try:
        metadata = run_git(
            ("rev-parse", "--absolute-git-dir"),
            cwd=root,
            label=f"operation_evidence:gitdir:{file_name}",
        )
    except (OSError, RuntimeError):
        return None
    if metadata.returncode:
        return None
    marker = Path(metadata.stdout.strip()) / file_name
    if not DEFAULT_FILE_BACKEND.exists(marker):
        return None
    try:
        raw = DEFAULT_FILE_BACKEND.read_bytes(marker).decode("utf-8", errors="replace").strip()
    except OSError:
        return None
    return raw.splitlines()[0].strip() if raw else None


def _read_object_resolvable(root: Path, file_name: str, kind: str) -> OperationFact:
    from ralph.git.subprocess_runner import run_git

    sha = _read_marker_sha(root, file_name)
    if not sha:
        return OperationFact(kind=kind, value=_UNREADABLE)
    try:
        res = run_git(
            ("cat-file", "-e", sha),
            cwd=root,
            label=f"operation_evidence:{kind}",
        )
    except (OSError, RuntimeError):
        return OperationFact(kind=kind, value=_UNREADABLE)
    if res.returncode == 0:
        return OperationFact(kind=kind, value="True")
    if res.returncode == 1:
        return OperationFact(kind=kind, value="False")
    return OperationFact(kind=kind, value=_UNREADABLE)


__all__ = [
    "_KIND_MERGE_OBJECT_RESOLVABLE",
    "_KIND_REBASE_OBJECT_RESOLVABLE",
    "EvidenceVerdict",
    "InMemoryOperationEvidenceCollector",
    "OperationEvidenceCollector",
    "OperationFact",
    "SubprocessOperationEvidenceCollector",
    "ownership_fingerprint",
]
