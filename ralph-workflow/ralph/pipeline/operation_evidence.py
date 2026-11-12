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

_REBASE_MERGE_FILE_NAMES: tuple[str, ...] = (
    "MERGE_HEAD",
    "MERGE_MSG",
    "REBASE_HEAD",
    "ORIG_HEAD",
    "AUTO_MERGE",
    "CHERRY_PICK_HEAD",
)


def ownership_fingerprint(record: IntegrationRecord) -> str:
    return identity_fingerprint(record)


_IDENTITY_FINGERPRINT_FIELDS: tuple[str, ...] = (
    "target",
    "operation_kind",
    "owning_worktree",
)


def identity_fingerprint(record: IntegrationRecord) -> str:
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
    from ralph.git.operations import _read_head_sha_direct

    direct_sha = _read_head_sha_direct(root)
    if direct_sha is not None:
        return direct_sha
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


def _resolve_evidence_gitdir(root: Path) -> Path | None:
    try:
        from ralph.git.operations import _resolve_gitdir_paths

        gitdir, _ = _resolve_gitdir_paths(root)
        if gitdir is not None and DEFAULT_FILE_BACKEND.exists(gitdir):
            return gitdir
    except (OSError, ValueError):
        gitdir = None
    from ralph.git.subprocess_runner import run_git

    try:
        metadata = run_git(
            ("rev-parse", "--absolute-git-dir"),
            cwd=root,
            label="operation_evidence:gitdir",
        )
    except (OSError, RuntimeError):
        return None
    if metadata.returncode:
        return None
    raw = metadata.stdout.strip()
    return Path(raw) if raw else None


def _read_rebase_state_files(root: Path) -> tuple[OperationFact, ...]:
    if not DEFAULT_FILE_BACKEND.exists(root):
        return (OperationFact(kind=_KIND_REBASE_STATE, value=_UNREADABLE),)
    gitdir = _resolve_evidence_gitdir(root)
    if gitdir is None:
        return (OperationFact(kind=_KIND_REBASE_STATE, value=_UNREADABLE),)
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
    if not DEFAULT_FILE_BACKEND.exists(root):
        return (OperationFact(kind=_KIND_MERGE_STATE, value=_UNREADABLE),)
    gitdir = _resolve_evidence_gitdir(root)
    if gitdir is None:
        return (OperationFact(kind=_KIND_MERGE_STATE, value=_UNREADABLE),)
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
    if not DEFAULT_FILE_BACKEND.exists(root):
        return ()
    gitdir = _resolve_evidence_gitdir(root)
    if gitdir is None:
        return ()

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
    if not DEFAULT_FILE_BACKEND.exists(root):
        return None
    gitdir = _resolve_evidence_gitdir(root)
    if gitdir is None:
        return None
    marker = gitdir / file_name
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
