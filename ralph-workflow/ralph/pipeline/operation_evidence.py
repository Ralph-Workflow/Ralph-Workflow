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

These exclusions are the architectural fix for the production
retained-integration reproduction: linked worktrees share a single
``refs/`` namespace, so an unrelated agent advancing an unrelated
branch can no longer rotate the diagnostic hash and force
``diagnostic_evidence_unchanged`` to return ``False``.

The collector is dependency-injectable so the diagnostic path can be
tested black-box with an in-memory fake. The default implementation
runs ``git`` through the existing :func:`ralph.git.subprocess_runner.run_git`
boundary with the operation root as ``cwd``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

# Per-worktree rebase/merge state file names. These are the exact file
# names git writes under the worktree-private gitdir. The branch
# reflog and ``logs/HEAD`` are intentionally NOT in this set: they live
# under commondir, so reading them lets unrelated worktree activity
# change the diagnostic hash.
_REBASE_MERGE_FILE_NAMES: tuple[str, ...] = (
    "MERGE_HEAD",
    "MERGE_MSG",
    "REBASE_HEAD",
    "ORIG_HEAD",
    "AUTO_MERGE",
    "CHERRY_PICK_HEAD",
)

# Sentinel emitted when a Git invocation fails, an observation file is
# missing, or the file backend refuses a read. Mirrors the existing
# on-disk contract in ``_integration_diagnostic._diagnostic_snapshot``.
_UNREADABLE = "unreadable"

# The five operation-scoped kinds the default collector emits. Each
# kind is the discriminator downstream code uses to bucket the
# observation. ``OperationFact.to_evidence_tuple`` round-trips each
# kind into the legacy ``(key, value)`` pair shape that
# ``IntegrationRecord.diagnostic_evidence`` already understands.
_KIND_HEAD = "head"
_KIND_WORKTREE_PATH = "worktree_path"
_KIND_INDEX_SCOPE = "index_scope"
_KIND_REBASE_STATE = "rebase_state_files"
_KIND_MERGE_STATE = "merge_state_files"
_KIND_OWNERSHIP = "ownership_fingerprint"

# Stable mapping from kind name to a synthetic single-key under the
# legacy on-disk format. The legacy writer stored ``str(absolute_path)``
# for many of the same files; the new collector stamps a kind tag
# instead, so legacy on-disk records that still carry the absolute-path
# keys can be migrated by the one-way legacy-key migrator in
# ``auto_integrate_record._parse_record_payload``.
_KIND_TO_LEGACY_KEY: dict[str, str] = {
    _KIND_HEAD: "head",
    _KIND_WORKTREE_PATH: "worktree_path",
    _KIND_INDEX_SCOPE: "index_scope",
    _KIND_REBASE_STATE: "rebase_state_files",
    _KIND_MERGE_STATE: "merge_state_files",
    _KIND_OWNERSHIP: "ownership_fingerprint",
}


@dataclass(frozen=True)
class OperationFact:
    """A typed, operation-scoped observation produced by the collector.

    Attributes:
        kind: One of the five operation-scoped kinds the default
            collector emits. Reading the dataclass through
            ``to_evidence_tuple`` produces the legacy ``(key, value)``
            pair format the durable record already understands, so
            the on-disk contract is preserved.
        value: The observation value. Never ``None``; a failure to
            read is encoded as the string ``"unreadable"``, matching
            the existing on-disk convention.
        scope: Optional per-path scope token (the path string itself
            for ``index_scope`` entries; ``None`` for the singleton
            kinds). Lets the one-way legacy migrator re-categorise
            path-keyed observations under their new kind tag.
    """

    kind: str
    value: str
    scope: str | None = None

    def to_evidence_tuple(self) -> tuple[str, str]:
        """Return the legacy ``(key, value)`` pair the durable record stores.

        For path-scoped kinds (``index_scope``, ``rebase_state_files``,
        ``merge_state_files``) the key is the kind tag joined with the
        scope, so per-path observations do not collide on the kind tag
        alone. The on-disk contract is ``tuple[tuple[str, str], ...]``
        where each pair is a free-form key and a value -- this method
        produces one pair per :class:`OperationFact`.
        """
        if self.scope is None:
            return (_KIND_TO_LEGACY_KEY.get(self.kind, self.kind), self.value)
        return (f"{_KIND_TO_LEGACY_KEY.get(self.kind, self.kind)}:{self.scope}", self.value)


class OperationEvidenceCollector(Protocol):
    """The only component permitted to read Git state for diagnostic evidence.

    Implementations MUST scope every read to the operation worktree
    passed in ``root`` (which the diagnostic path derives from
    ``record.owning_worktree`` for ``target_reconcile`` and from
    ``workspace_scope.root`` otherwise) and MUST NOT read repository
    state owned by an unrelated worktree or branch. Implementations
    MUST also never emit a value of ``None`` -- an unreadable
    observation is encoded as the string ``"unreadable"`` so the
    durable record's equality contract stays stable.
    """

    def snapshot(self, root: Path, record: IntegrationRecord) -> tuple[OperationFact, ...]:
        """Return the operation-scoped facts for ``record``."""
        ...

    def verify(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> bool:
        """Return ``True`` iff the live snapshot still matches ``observed``."""


def ownership_fingerprint(record: IntegrationRecord) -> str:
    """SHA-256 fingerprint of the durable record minus its volatile fields.

    The fingerprint is the durable evidence the record's *identity*
    (target, pre-integration SHAs, operation kind) has not been
    changed underneath the diagnostic path. It is the same value the
    pre-existing ``_ownership_fingerprint`` helper produced, extracted
    here so the collector can emit it as a typed
    :class:`OperationFact` rather than rely on the diagnostic module
    to recompute it on every verification.
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

    def snapshot(self, root: Path, record: IntegrationRecord) -> tuple[OperationFact, ...]:
        facts: list[OperationFact] = [
            OperationFact(kind=_KIND_HEAD, value=_read_head(root)),
            OperationFact(kind=_KIND_WORKTREE_PATH, value=str(root)),
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
        return tuple(facts)

    def verify(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> bool:
        current = self.snapshot(root, record)
        current_map = {fact.kind: fact.value for fact in current}
        current_scope: dict[tuple[str, str | None], str] = {
            (fact.kind, fact.scope): fact.value for fact in current
        }
        for fact in observed:
            if fact.scope is None:
                expected = current_map.get(fact.kind)
            else:
                expected = current_scope.get((fact.kind, fact.scope))
            if expected is None:
                return False
            if fact.value == _UNREADABLE:
                # Unreadable observations are stable: an observation
                # that could not be captured at record time remains
                # unverifiable now, and the diagnostic path must
                # surface that explicitly rather than silently
                # disagreeing.
                continue
            if expected != fact.value:
                return False
        return True


class InMemoryOperationEvidenceCollector:
    """Test fake that returns caller-supplied facts verbatim.

    Tests use this collector to simulate a fingerprint that has (or
    has not) drifted, without depending on the host's ``git`` binary
    or filesystem layout. The fake never falls back to a subprocess
    call, so it is safe to use in tests that explicitly forbid real
    subprocess I/O.
    """

    def __init__(self, facts: tuple[OperationFact, ...] = ()) -> None:
        self._facts = tuple(facts)

    def snapshot(self, root: Path, record: IntegrationRecord) -> tuple[OperationFact, ...]:
        return self._facts

    def verify(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> bool:
        return self._facts == observed


def _read_head(root: Path) -> str:
    from ralph.git.subprocess_runner import run_git

    result = run_git(
        ("rev-parse", "--verify", "HEAD"),
        cwd=root,
        label="operation_evidence:head",
    )
    if result.returncode:
        return _UNREADABLE
    return result.stdout.strip() or _UNREADABLE


def _read_index_scope(root: Path, resolving_paths: tuple[str, ...]) -> str:
    if not resolving_paths:
        return "absent"
    from ralph.git.subprocess_runner import run_git

    result = run_git(
        ("ls-files", "--stage", "-z", "--", *resolving_paths),
        cwd=root,
        label="operation_evidence:index_scope",
    )
    if result.returncode:
        return _UNREADABLE
    return _hash_text(result.stdout)


def _read_rebase_state_files(root: Path) -> tuple[OperationFact, ...]:
    """Read the per-worktree ``rebase-merge/`` and ``rebase-apply/`` files.

    Both directories live under the per-worktree gitdir, so a capture
    from the operation worktree is genuinely operation-scoped. Each
    file is emitted as a scope-tagged :class:`OperationFact` so
    multiple rebase-state files do not collide on the same kind tag.
    The branch reflog and ``logs/HEAD`` are explicitly excluded --
    they are common-dir state and would let unrelated worktree
    activity change the diagnostic hash.
    """
    from ralph.git.subprocess_runner import run_git

    metadata = run_git(
        ("rev-parse", "--absolute-git-dir"),
        cwd=root,
        label="operation_evidence:gitdir",
    )
    if metadata.returncode:
        return ()
    gitdir = Path(metadata.stdout.strip())
    paths = set(DEFAULT_FILE_BACKEND.glob(gitdir, "rebase-merge/*"))
    paths.update(DEFAULT_FILE_BACKEND.glob(gitdir, "rebase-apply/*"))
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
    """Read the per-worktree ``MERGE_HEAD`` / ``MERGE_MSG`` / etc. files.

    All seven of these files live under the per-worktree gitdir, so a
    capture from the operation worktree is operation-scoped. The
    branch reflog and ``logs/HEAD`` are intentionally NOT in this
    set -- they are common-dir state owned by every worktree in the
    repository.
    """
    from ralph.git.subprocess_runner import run_git

    metadata = run_git(
        ("rev-parse", "--absolute-git-dir"),
        cwd=root,
        label="operation_evidence:gitdir",
    )
    if metadata.returncode:
        return ()
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
