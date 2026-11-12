from __future__ import annotations

from dataclasses import dataclass

_UNREADABLE = "unreadable"

_KIND_HEAD = "head"
_KIND_WORKTREE_PATH = "worktree_path"
_KIND_INDEX_SCOPE = "index_scope"
_KIND_REBASE_STATE = "rebase_state_files"
_KIND_MERGE_STATE = "merge_state_files"
_KIND_OWNERSHIP = "ownership_fingerprint"
_KIND_UNSTAGED_WORK = "unstaged_work"
_KIND_COMMIT_CONTROLS = "commit_controls"
_KIND_MERGE_OBJECT_RESOLVABLE = "merge_object_resolvable"
_KIND_REBASE_OBJECT_RESOLVABLE = "rebase_object_resolvable"

_KIND_TO_LEGACY_KEY: dict[str, str] = {
    _KIND_HEAD: "head",
    _KIND_WORKTREE_PATH: "worktree_path",
    _KIND_INDEX_SCOPE: "index_scope",
    _KIND_REBASE_STATE: "rebase_state_files",
    _KIND_MERGE_STATE: "merge_state_files",
    _KIND_OWNERSHIP: "ownership_fingerprint",
    _KIND_UNSTAGED_WORK: "unstaged_work",
    _KIND_COMMIT_CONTROLS: "commit_controls",
    _KIND_MERGE_OBJECT_RESOLVABLE: "merge_object_resolvable",
    _KIND_REBASE_OBJECT_RESOLVABLE: "rebase_object_resolvable",
}


@dataclass(frozen=True)
class OperationFact:
    kind: str
    value: str
    scope: str | None = None

    def to_evidence_tuple(self) -> tuple[str, str]:
        if self.scope is None:
            return (_KIND_TO_LEGACY_KEY.get(self.kind, self.kind), self.value)
        return (f"{_KIND_TO_LEGACY_KEY.get(self.kind, self.kind)}:{self.scope}", self.value)


__all__ = [
    "_KIND_COMMIT_CONTROLS",
    "_KIND_HEAD",
    "_KIND_INDEX_SCOPE",
    "_KIND_MERGE_OBJECT_RESOLVABLE",
    "_KIND_MERGE_STATE",
    "_KIND_OWNERSHIP",
    "_KIND_REBASE_OBJECT_RESOLVABLE",
    "_KIND_REBASE_STATE",
    "_KIND_TO_LEGACY_KEY",
    "_KIND_UNSTAGED_WORK",
    "_KIND_WORKTREE_PATH",
    "_UNREADABLE",
    "OperationFact",
]
