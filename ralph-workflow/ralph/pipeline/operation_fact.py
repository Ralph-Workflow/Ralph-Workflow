"""Typed operation-scoped observation fact.

Defines :class:`OperationFact`, a typed fact produced by an
evidence collector and stored in :class:`IntegrationRecord.diagnostic_evidence`.
"""

from __future__ import annotations

from dataclasses import dataclass

# Sentinel emitted when a Git invocation fails, an observation file is
# missing, or the file backend refuses a read. Mirrors the on-disk
# contract in ``_integration_diagnostic._diagnostic_snapshot``.
_UNREADABLE = "unreadable"

# The operation-scoped kinds the default collector emits. Each
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
_KIND_UNSTAGED_WORK = "unstaged_work"
_KIND_COMMIT_CONTROLS = "commit_controls"

# Stable mapping from kind name to a synthetic single-key under the
# legacy on-disk format.
_KIND_TO_LEGACY_KEY: dict[str, str] = {
    _KIND_HEAD: "head",
    _KIND_WORKTREE_PATH: "worktree_path",
    _KIND_INDEX_SCOPE: "index_scope",
    _KIND_REBASE_STATE: "rebase_state_files",
    _KIND_MERGE_STATE: "merge_state_files",
    _KIND_OWNERSHIP: "ownership_fingerprint",
    _KIND_UNSTAGED_WORK: "unstaged_work",
    _KIND_COMMIT_CONTROLS: "commit_controls",
}


@dataclass(frozen=True)
class OperationFact:
    """A typed, operation-scoped observation produced by the collector.

    Attributes:
        kind: One of the operation-scoped kinds the collector emits.
            Reading the dataclass through ``to_evidence_tuple`` produces
            the legacy ``(key, value)`` pair format the durable record
            already understands, so the on-disk contract is preserved.
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


__all__ = [
    "_KIND_COMMIT_CONTROLS",
    "_KIND_HEAD",
    "_KIND_INDEX_SCOPE",
    "_KIND_MERGE_STATE",
    "_KIND_OWNERSHIP",
    "_KIND_REBASE_STATE",
    "_KIND_TO_LEGACY_KEY",
    "_KIND_UNSTAGED_WORK",
    "_KIND_WORKTREE_PATH",
    "_UNREADABLE",
    "OperationFact",
]
