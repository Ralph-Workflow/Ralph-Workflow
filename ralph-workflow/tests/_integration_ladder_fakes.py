"""Shared scripted doubles for the integration-ladder test modules.

The git-plumbing, filesystem-quarantine and environment-probe fakes and
the durable-ledger seeding helpers the ladder test modules share. Pure
in-memory doubles: no real Git, no subprocess; the clock fake lives in
:mod:`tests._fake_git_clock`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.integration_attempt_ledger import AttemptLedger, AttemptLedgerState
from ralph.pipeline.integration_decision import NextAction
from ralph.pipeline.integration_ladder import (
    LADDER_ATTEMPT_BUDGET,
    EnvironmentFailure,
    LandResult,
    RepointResult,
    ladder_charge_count,
    record_salvage_attempt,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.integration_observation import InProgressKind


# ---------------------------------------------------------------------------
# Fakes.
# ---------------------------------------------------------------------------
class _FakePlumbing:
    """Scripted :class:`GitPlumbing` double recording every mutation."""

    def __init__(
        self,
        *,
        changed: tuple[str, ...] = ("src/a.py",),
        changed_unreadable: bool = False,
        head: str | None = "abc123",
        target_shas: dict[str, str] | None = None,
        commit_ref: str | None = "refs/ralph-backup/fake",
        readable_refs: frozenset[str] = frozenset({"refs/ralph-backup/fake"}),
        rename_blocked: frozenset[str] = frozenset(),
        pruned_landed: tuple[str, ...] = ("refs/ralph-backup/landed",),
        fail_patch: bool = False,
        fail_tar: bool = False,
        fail_restore: bool = False,
        fail_abort: bool = False,
        fail_first_reset: bool = False,
        fail_reset_forever: bool = False,
        fail_recreate: bool = False,
        fail_create_worktree: bool = False,
        fail_apply: bool = False,
        land_result: LandResult = "landed",
        repoint_result: RepointResult = "repointed",
        fail_safe_directory: bool = False,
    ) -> None:
        self._changed = changed
        self._changed_unreadable = changed_unreadable
        self._head = head
        self._target_shas = target_shas if target_shas is not None else {"main": "f00d"}
        self._commit_ref = commit_ref
        self._readable_refs = readable_refs
        self.rename_blocked = rename_blocked
        self._pruned_landed = pruned_landed
        self.fail_patch = fail_patch
        self.fail_tar = fail_tar
        self.fail_restore = fail_restore
        self.fail_abort = fail_abort
        self.fail_first_reset = fail_first_reset
        self.fail_reset_count = 0
        self.fail_reset_forever = fail_reset_forever
        self.fail_recreate = fail_recreate
        self.fail_create_worktree = fail_create_worktree
        self.fail_apply = fail_apply
        self._land_result = land_result
        self._repoint_result = repoint_result
        self.fail_safe_directory = fail_safe_directory
        self.written: set[Path] = set()
        self.calls: list[str] = []

    def changed_paths(self, root: Path) -> tuple[str, ...] | None:
        self.calls.append("changed_paths")
        if self._changed_unreadable:
            return None
        return self._changed

    def head_sha(self, root: Path) -> str | None:
        self.calls.append("head_sha")
        return self._head

    def target_sha(self, root: Path, target: str) -> str | None:
        self.calls.append(f"target_sha:{target}")
        return self._target_shas.get(target)

    def commit_dirt(self, root: Path, head_sha: str, owning_worktree: str | None) -> str | None:
        self.calls.append("commit_dirt")
        return self._commit_ref

    def ref_is_readable(self, root: Path, ref: str) -> bool:
        self.calls.append(f"ref_is_readable:{ref}")
        return ref in self._readable_refs

    def prune_landed_backup_refs(
        self, root: Path, is_landed: Callable[[str], bool]
    ) -> tuple[str, ...]:
        self.calls.append("prune_landed_backup_refs")
        return tuple(ref for ref in self._pruned_landed if is_landed(ref))

    def write_patch(self, root: Path, paths: tuple[str, ...], dest: Path) -> bool:
        self.calls.append(f"write_patch:{dest.name}")
        if not self.fail_patch:
            self.written.add(dest)
        return not self.fail_patch

    def archive_paths(self, root: Path, paths: tuple[str, ...], dest: Path) -> bool:
        self.calls.append(f"archive_paths:{dest.name}")
        if not self.fail_tar:
            self.written.add(dest)
        return not self.fail_tar

    def restore_tree_to_head(self, root: Path) -> bool:
        self.calls.append("restore_tree_to_head")
        return not self.fail_restore

    def abort_operation(self, root: Path, kind: InProgressKind) -> bool:
        self.calls.append(f"abort_operation:{kind.value}")
        return not self.fail_abort

    def remove_operation_state(self, root: Path, kind: InProgressKind) -> bool:
        self.calls.append(f"remove_operation_state:{kind.value}")
        return True

    def reset_hard(self, root: Path, revision: str) -> bool:
        self.calls.append(f"reset_hard:{revision}")
        self.fail_reset_count += 1
        failed = self.fail_reset_forever or (self.fail_first_reset and self.fail_reset_count == 1)
        return not failed

    def clean_untracked(self, root: Path, paths: tuple[str, ...]) -> bool:
        self.calls.append("clean_untracked")
        return True

    def remove_stale_locks(self, root: Path) -> tuple[str, ...]:
        self.calls.append("remove_stale_locks")
        return ("index.lock",)

    def recreate_worktree(self, root: Path, branch: str) -> bool:
        self.calls.append(f"recreate_worktree:{branch}")
        return not self.fail_recreate

    def set_safe_directory(self, root: Path) -> bool:
        self.calls.append("set_safe_directory")
        return not self.fail_safe_directory

    def repair_gitdir(self, root: Path) -> bool:
        self.calls.append("repair_gitdir")
        return True

    def create_worktree(self, root: Path, path: Path, target: str) -> bool:
        self.calls.append(f"create_worktree:{target}")
        return not self.fail_create_worktree

    def apply_preserved(self, root: Path, path: Path, preserved_ref: str) -> bool:
        self.calls.append(f"apply_preserved:{preserved_ref}")
        return not self.fail_apply

    def land_in_worktree(
        self, root: Path, path: Path, target: str, expected_sha: str
    ) -> LandResult:
        self.calls.append(f"land_in_worktree:{target}:{expected_sha}")
        return self._land_result

    def repoint_original(self, root: Path, path: Path, observed_sha: str | None) -> RepointResult:
        self.calls.append(f"repoint_original:{observed_sha}")
        return self._repoint_result

    def remote_reachable(self, root: Path, remote: str) -> bool:
        self.calls.append(f"remote_reachable:{remote}")
        return True


class _FakeQuarantineFs:
    """Scripted :class:`FileQuarantine` double over an in-memory map."""

    def __init__(
        self,
        *,
        existing: set[Path] | None = None,
        rename_fails: frozenset[str] = frozenset(),
        pruned: tuple[str, ...] = ("tmp", "stale.staging"),
        alternative: Path | None = None,
    ) -> None:
        self._existing = existing if existing is not None else set()
        self._rename_fails = rename_fails
        self._pruned = pruned
        self._alternative = alternative
        self.renamed: list[tuple[str, str]] = []
        self.calls: list[str] = []

    def rename_into_quarantine(self, root: Path, quarantine_dir: Path, path: str) -> str | None:
        self.calls.append(f"rename:{path}")
        if path in self._rename_fails:
            return None
        dest = str(quarantine_dir / path)
        self.renamed.append((path, dest))
        return dest

    def prune_regenerable_artifacts(self, state_dir: Path) -> tuple[str, ...]:
        self.calls.append("prune_regenerable_artifacts")
        return self._pruned

    def path_is_readable(self, path: Path) -> bool:
        self.calls.append(f"readable:{path.name}")
        return path in self._existing

    def alternative_state_dir(self, root: Path) -> Path | None:
        self.calls.append("alternative_state_dir")
        return self._alternative


class _FakeEnvProbe:
    """Scripted :class:`EnvironmentProbe` double."""

    def __init__(self, failure: EnvironmentFailure | None = None) -> None:
        self._failure = failure

    def failure_class(self, root: Path, *, remote: str) -> EnvironmentFailure | None:
        return self._failure


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _ledger(tmp_path: Path) -> AttemptLedger:
    return AttemptLedger(tmp_path)


def _evidence(tmp_path: Path) -> dict[str, str]:
    return _ledger(tmp_path).read().ladder_evidence


def _record(ledger: AttemptLedger, key: str, value: str) -> None:
    state = ledger.read()
    merged = AttemptLedgerState(
        rung=state.rung,
        repeat_counts=state.repeat_counts,
        unknown_attempts=state.unknown_attempts,
        unknown_budget=state.unknown_budget,
        repeat_threshold=state.repeat_threshold,
        saturated_repeats=state.saturated_repeats,
        global_actions_charged=state.global_actions_charged,
        ladder_evidence={**state.ladder_evidence, key: value},
    )
    ledger.persist(merged)


def _seed_terminal_rungs(ledger: AttemptLedger) -> None:
    """Record every rung 1-5 as tried-and-failed, as the ladder would."""
    outcomes = {
        "save_and_restore": "failed:restore-refused",
        "preserve_via_temp_index": "failed:no-durable-copy",
        "quarantine_and_restore": "impossible:rename-failed:src/locked.py",
        "rebuild_in_fresh_worktree": "failed:land",
        "adapt_environment_and_retry": "waiting:n=6:failure=read_only_fs:delay=60.0",
    }
    for action, outcome in outcomes.items():
        _record(ledger, f"rung:{action}", outcome)


def _seed_salvage(ledger: AttemptLedger) -> None:
    record_salvage_attempt(ledger, "resolver_agent", "failed:no-progress")
    record_salvage_attempt(ledger, "rebase_first_retry", "failed:same-signature")
    record_salvage_attempt(ledger, "fresh_worktree_rebuild", "failed:land")


def _seed_budget(ledger: AttemptLedger, signature: str = "sig") -> None:
    """Charge the whole-ladder budget to the durable ledger."""
    while ladder_charge_count(ledger.read()) < LADDER_ATTEMPT_BUDGET:
        ledger.charge(signature, NextAction.SAVE_AND_RESTORE.value, is_unknown=False)


_SIG = "behind|tracked_changes|none|none|attached"


def _observe_into(sink: list[str], signature: str) -> str:
    """Record the re-observation and return it (a statement, not a value trick)."""
    sink.append(signature)
    return signature
