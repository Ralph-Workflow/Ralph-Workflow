from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.pipeline._integration_ladder_evidence import (
    _LADDER_ACTION_VALUES,
    _RUNG_ACTION_VALUE,
    BACKOFF_BASE_SECONDS,
    BACKOFF_CAP_SECONDS,
    _is_terminal,
    _record_evidence,
    _rung_attempt_satisfied,
    _rung_key,
)
from ralph.pipeline._integration_ladder_seams import (
    EnvironmentFailure,
    EnvironmentProbe,
    FileQuarantine,
    GitPlumbing,
    LadderOutcome,
    LandResult,
    RepointResult,
)
from ralph.pipeline.integration_actions import Clock, current_clock
from ralph.pipeline.integration_decision import (
    CLEANUP_LADDER_ORDER,
    CleanupLadderRung,
)
from ralph.pipeline.integration_observation import InProgressKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_attempt_ledger import (
        AttemptLedger,
        AttemptLedgerState,
    )

__all__ = [
    "LADDER_ATTEMPT_BUDGET",
    "LAST_RESORT_DISABLED_NO_SIGNATURE",
    "LAST_RESORT_PRECONDITIONS",
    "LAST_RESORT_REFUSED_NO_PRESERVED_COPY",
    "LAST_RESORT_TERMINAL_NO_SIGNATURE",
    "PRESERVED_COPY_LEDGER_KEY",
    "QUARANTINE_COPY_LEDGER_KEY",
    "EnvironmentFailure",
    "EnvironmentProbe",
    "FileQuarantine",
    "GitPlumbing",
    "LadderOutcome",
    "LadderStepResult",
    "LandResult",
    "RepointResult",
    "adapt_environment_and_retry",
    "ensure_preserved_copy",
    "evaluate_last_resort_preconditions",
    "ladder_charge_count",
    "last_resort_recovery",
    "preserve_via_temp_index",
    "preserved_copy_identity",
    "quarantine_and_restore",
    "reap_worktree_stale_locks",
    "rebuild_in_fresh_worktree",
    "record_salvage_attempt",
    "resume_rung",
    "run_ladder_rung",
    "terminal_operator_message",
]


LADDER_ATTEMPT_BUDGET: Final[int] = 16

LAST_RESORT_PRECONDITIONS: Final[tuple[str, ...]] = (
    "rungs_1_to_5_tried_or_shown_impossible",
    "salvage_resolver_agent_tried",
    "salvage_rebase_first_ladder_retried",
    "salvage_fresh_worktree_rebuild_tried",
    "ladder_attempt_budget_spent",
    "fresh_reobservation_confirms_blocking_state",
)

LAST_RESORT_TERMINAL_NO_SIGNATURE: Final[str] = "terminal:disabled:no-blocking-signature"

LAST_RESORT_DISABLED_NO_SIGNATURE: Final[str] = "failed:disabled:no-blocking-signature"

LAST_RESORT_REFUSED_NO_PRESERVED_COPY: Final[str] = "terminal:refused:no-preserved-copy"

PRESERVED_COPY_LEDGER_KEY: Final[str] = "preserved:alternate_preservation"

QUARANTINE_COPY_LEDGER_KEY: Final[str] = "preserved:quarantine"

SALVAGE_KINDS: Final[tuple[str, ...]] = (
    "resolver_agent",
    "rebase_first_retry",
    "fresh_worktree_rebuild",
)

_RESET_CAPABLE_RUNGS: Final[frozenset[CleanupLadderRung]] = frozenset(
    {
        CleanupLadderRung.QUARANTINE,
        CleanupLadderRung.FRESH_WORKTREE,
        CleanupLadderRung.LAST_RESORT,
    }
)


@dataclass(frozen=True)
class LadderStepResult:
    rung: CleanupLadderRung
    outcome: LadderOutcome
    reason: str
    preserved_ref: str | None = None
    preserved_sidecar: str | None = None
    quarantined_paths: tuple[str, ...] = ()
    next_rung: CleanupLadderRung | None = None
    evidence: dict[str, str] = field(default_factory=dict)


def ladder_charge_count(state: AttemptLedgerState) -> int:
    total = state.saturated_repeats
    for (_signature, action), count in state.repeat_counts.items():
        if action in _LADDER_ACTION_VALUES:
            total += count
    return total


def record_salvage_attempt(ledger: AttemptLedger, kind: str, outcome: str) -> None:
    if kind not in SALVAGE_KINDS:
        raise ValueError(f"unknown salvage kind: {kind!r}")
    _record_evidence(ledger, f"salvage:{kind}", outcome)


def resume_rung(state: AttemptLedgerState) -> CleanupLadderRung | None:
    for rung in CLEANUP_LADDER_ORDER:
        value = state.ladder_evidence.get(_rung_key(rung))
        if value is None or value == "intent":
            return rung
    return None


def evaluate_last_resort_preconditions(
    state: AttemptLedgerState,
    *,
    decided_signature: str,
    fresh_signature: str,
    budget: int = LADDER_ATTEMPT_BUDGET,
) -> dict[str, str]:
    evidence = state.ladder_evidence
    results: dict[str, str] = {}

    rung_report: list[str] = []
    rungs_met = True
    for rung in CLEANUP_LADDER_ORDER[:-1]:
        value = evidence.get(_rung_key(rung))
        rung_report.append(f"{_RUNG_ACTION_VALUE[rung]}={value or 'never-tried'}")
        if not _rung_attempt_satisfied(value):
            rungs_met = False
    results[LAST_RESORT_PRECONDITIONS[0]] = (
        f"met: rungs 1-5 each terminal ({'; '.join(rung_report)})"
        if rungs_met
        else f"unmet: {'; '.join(rung_report)}"
    )

    for kind, name in (
        ("resolver_agent", LAST_RESORT_PRECONDITIONS[1]),
        ("rebase_first_retry", LAST_RESORT_PRECONDITIONS[2]),
        ("fresh_worktree_rebuild", LAST_RESORT_PRECONDITIONS[3]),
    ):
        value = evidence.get(f"salvage:{kind}")
        results[name] = (
            f"met: salvage:{kind}={value}"
            if _is_terminal(value)
            else f"unmet: salvage:{kind}={value or 'never-tried'}"
        )

    spent = ladder_charge_count(state)
    results[LAST_RESORT_PRECONDITIONS[4]] = (
        f"met: {spent} charges >= budget {budget}"
        if spent >= budget
        else f"unmet: {spent} charges < budget {budget}"
    )

    reconfirmed = bool(decided_signature) and fresh_signature == decided_signature
    results[LAST_RESORT_PRECONDITIONS[5]] = (
        "met: re-observation within the current tick matches the blocking signature"
        if reconfirmed
        else f"unmet: fresh signature {fresh_signature!r} != blocking {decided_signature!r}"
    )
    return results


def preserve_via_temp_index(
    root: Path,
    *,
    git: GitPlumbing,
    fs: FileQuarantine,
    ledger: AttemptLedger,
    state_dir: Path,
    owning_worktree: str | None = None,
    is_landed: Callable[[str], bool] | None = None,
) -> LadderStepResult:
    rung = CleanupLadderRung.ALTERNATE_PRESERVATION
    key = _rung_key(rung)
    _record_evidence(ledger, key, "intent")
    paths = git.changed_paths(root)
    if paths is None:
        value = "failed:changed-paths-unreadable"
        _record_evidence(ledger, key, value)
        return LadderStepResult(rung, "failed", value, evidence={key: value})
    if not paths:
        value = "succeeded:nothing-to-preserve"
        _record_evidence(ledger, key, value)
        return LadderStepResult(rung, "succeeded", value, evidence={key: value})
    head = git.head_sha(root)
    if head is not None:
        ref = git.commit_dirt(root, head, owning_worktree)
        if ref is not None and git.ref_is_readable(root, ref):
            value = f"succeeded:ref={ref}"
            _record_evidence(ledger, key, value)
            _record_evidence(ledger, "preserved:alternate_preservation", ref)
            logger.info("integration ladder rung 2: dirt preserved at {}", ref)
            return LadderStepResult(
                rung, "succeeded", value, preserved_ref=ref, evidence={key: value}
            )

    pruned = fs.prune_regenerable_artifacts(state_dir)
    pruned_refs: tuple[str, ...] = ()
    if is_landed is not None:
        pruned_refs = git.prune_landed_backup_refs(root, is_landed)
    logger.info(
        "integration ladder rung 2: ref route failed; freed {} artifacts and "
        "{} verified-landed backup refs before writing the sidecar",
        len(pruned),
        len(pruned_refs),
    )
    preserved_dir = state_dir / "preserved"
    preserved_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256("\0".join(paths).encode(errors="surrogateescape")).hexdigest()
    token = f"{digest[:12]}-{head[:8] if head else 'nohead'}"
    patch_path = preserved_dir / f"{token}.patch"
    tar_path = preserved_dir / f"{token}.tar"
    patch_ok = git.write_patch(root, paths, patch_path)
    tar_ok = git.archive_paths(root, paths, tar_path)
    verified = (
        patch_ok and tar_ok and fs.path_is_readable(patch_path) and fs.path_is_readable(tar_path)
    )
    if verified:
        value = f"succeeded:sidecar={patch_path}"
        _record_evidence(ledger, key, value)
        _record_evidence(ledger, "preserved:alternate_preservation", str(patch_path))
        return LadderStepResult(
            rung, "succeeded", value, preserved_sidecar=str(patch_path), evidence={key: value}
        )
    value = "failed:no-durable-copy"
    _record_evidence(ledger, key, value)
    return LadderStepResult(rung, "failed", value, evidence={key: value})


def quarantine_and_restore(
    root: Path,
    *,
    paths: tuple[str, ...],
    git: GitPlumbing,
    fs: FileQuarantine,
    ledger: AttemptLedger,
    state_dir: Path,
    clock: Clock,
) -> LadderStepResult:
    rung = CleanupLadderRung.QUARANTINE
    key = _rung_key(rung)
    _record_evidence(ledger, key, "intent")
    quarantine_dir = state_dir / "quarantine" / f"tick-{clock.now():.0f}"
    quarantined: list[str] = []
    for path in paths:
        dest = fs.rename_into_quarantine(root, quarantine_dir, path)
        if dest is None:
            value = f"impossible:rename-failed:{path}"
            _record_evidence(ledger, key, value)
            return LadderStepResult(
                rung,
                "impossible",
                value,
                quarantined_paths=tuple(quarantined),
                next_rung=CleanupLadderRung.FRESH_WORKTREE,
                evidence={key: value},
            )
        quarantined.append(dest)
    if not git.restore_tree_to_head(root):
        value = "failed:restore-tree-to-head"
        _record_evidence(ledger, key, value)
        return LadderStepResult(
            rung, "failed", value, quarantined_paths=tuple(quarantined), evidence={key: value}
        )
    value = f"succeeded:quarantined={len(quarantined)}-paths"
    _record_evidence(ledger, key, value)
    _record_evidence(ledger, "preserved:quarantine", str(quarantine_dir))
    logger.info(
        "integration ladder rung 3: quarantined {} path(s) under {}; tree restored",
        len(quarantined),
        quarantine_dir,
    )
    return LadderStepResult(
        rung,
        "succeeded",
        value,
        quarantined_paths=tuple(quarantined),
        evidence={key: value},
    )


def rebuild_in_fresh_worktree(
    root: Path,
    *,
    target: str,
    branch: str,
    git: GitPlumbing,
    ledger: AttemptLedger,
    state_dir: Path,
    clock: Clock,
    preserved_ref: str | None = None,
    expected_target_sha: str | None = None,
) -> LadderStepResult:
    rung = CleanupLadderRung.FRESH_WORKTREE
    key = _rung_key(rung)
    _record_evidence(ledger, key, "intent")
    expected = (
        expected_target_sha if expected_target_sha is not None else git.target_sha(root, target)
    )
    original_head = git.head_sha(root)
    if expected is None:
        value = "failed:land:target-sha-unreadable"
        _record_evidence(ledger, key, value)
        return LadderStepResult(rung, "failed", value, evidence={key: value})
    digest = hashlib.sha256(f"{root}-{clock.now():.0f}".encode()).hexdigest()
    fresh_path = state_dir.parent / f"fresh-worktree-{digest[:8]}"
    steps: tuple[tuple[str, Callable[[], bool]], ...] = (
        ("create-worktree", lambda: git.create_worktree(root, fresh_path, target)),
        (
            "apply-preserved",
            lambda: (
                True
                if preserved_ref is None
                else git.apply_preserved(root, fresh_path, preserved_ref)
            ),
        ),
    )
    for step_name, step in steps:
        if step():
            continue
        value = f"failed:{step_name}"
        _record_evidence(ledger, key, value)
        return LadderStepResult(rung, "failed", value, evidence={key: value})
    land = git.land_in_worktree(root, fresh_path, target, expected_sha=expected)
    if land == "target-moved":
        value = "failed:land:target-moved-concurrently"
        _record_evidence(ledger, key, value)
        return LadderStepResult(rung, "failed", value, evidence={key: value})
    if land not in ("landed", "landed-already"):
        value = f"failed:land:{land}"
        _record_evidence(ledger, key, value)
        return LadderStepResult(rung, "failed", value, evidence={key: value})
    repoint = git.repoint_original(root, fresh_path, observed_sha=original_head)
    if repoint == "reset-failed":
        value = "failed:repoint-original"
        _record_evidence(ledger, key, value)
        return LadderStepResult(rung, "failed", value, evidence={key: value})
    not_now = "" if repoint == "repointed" else f":repoint-not-now:{repoint}"
    value = f"succeeded:fresh-worktree={fresh_path}{not_now}"
    _record_evidence(ledger, key, value)
    _record_evidence(ledger, "salvage:fresh_worktree_rebuild", "succeeded")
    _record_evidence(ledger, "preserved:fresh_worktree", str(fresh_path))
    logger.info(
        "integration ladder rung 4: rebuilt on {} at {}; target landed from there",
        target,
        fresh_path,
    )
    return LadderStepResult(rung, "succeeded", value, evidence={key: value})


_REMINDED_AT: dict[str, float] = {}


def _remind_once(
    failure: str,
    clock: Clock,
    remind: Callable[[str], None] | None,
) -> None:
    now = clock.now()
    last = _REMINDED_AT.get(failure)
    if last is not None and now - last < BACKOFF_CAP_SECONDS:
        return
    _REMINDED_AT[failure] = now
    message = (
        f"integration ladder rung 5: environment failure {failure}; adapted what "
        "is adaptable, backing off with capped exponential backoff and "
        "re-observing each tick; the run continues and never waits on a person"
    )
    if remind is not None:
        remind(message)
    else:
        logger.warning(message)


def _waiting_count(state: AttemptLedgerState, key: str) -> int:
    value = state.ladder_evidence.get(key, "")
    prefix = "waiting:n="
    if not value.startswith(prefix):
        return 0
    raw = value[len(prefix) :].split(":", 1)[0]
    return int(raw) if raw.isdecimal() else 0


def adapt_environment_and_retry(
    root: Path,
    *,
    git: GitPlumbing,
    fs: FileQuarantine,
    env: EnvironmentProbe,
    ledger: AttemptLedger,
    clock: Clock,
    state_dir: Path,
    remote: str = "origin",
    observe: Callable[[], str] | None = None,
    remind: Callable[[str], None] | None = None,
) -> LadderStepResult:
    rung = CleanupLadderRung.ENVIRONMENT_DEGRADE
    key = _rung_key(rung)
    attempts = _waiting_count(ledger.read(), key)
    _record_evidence(ledger, key, "intent")
    failure = env.failure_class(root, remote=remote)
    adapted: list[str] = []
    if failure == "safe_directory":
        if git.set_safe_directory(root):
            adapted.append("safe.directory")
    elif failure in ("read_only_fs", "disk_full"):
        pruned = fs.prune_regenerable_artifacts(state_dir)
        adapted.append(f"pruned-{len(pruned)}-artifacts")
        alternative = fs.alternative_state_dir(root)
        if alternative is not None:
            adapted.append(f"alt-state-dir={alternative}")
    elif failure == "missing_gitdir" and git.repair_gitdir(root):
        adapted.append("gitdir-repaired")
    delay = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (1 << min(attempts, 10)))
    clock.sleep(delay)
    fresh = observe() if observe is not None else ""
    value = (
        f"waiting:n={attempts + 1}:failure={failure or 'none'}:"
        f"delay={delay}:adapted={','.join(adapted) or 'nothing'}:observed={fresh}"
    )
    _record_evidence(ledger, key, value)
    _remind_once(failure or "none", clock, remind)
    return LadderStepResult(rung, "waiting", value, evidence={key: value})


def preserved_copy_identity(
    step: LadderStepResult,
    state: AttemptLedgerState,
) -> str | None:
    identity = step.preserved_ref or step.preserved_sidecar
    if identity:
        return identity
    evidence = state.ladder_evidence
    return evidence.get(PRESERVED_COPY_LEDGER_KEY) or evidence.get(QUARANTINE_COPY_LEDGER_KEY)


def ensure_preserved_copy(
    root: Path,
    *,
    ledger: AttemptLedger,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    state_dir: Path | None = None,
    is_landed: Callable[[str], bool] | None = None,
) -> LadderStepResult | None:
    if PRESERVED_COPY_LEDGER_KEY in ledger.read().ladder_evidence:
        return None
    return preserve_via_temp_index(
        root,
        git=git if git is not None else default_git_plumbing(),
        fs=fs if fs is not None else default_file_quarantine(),
        ledger=ledger,
        state_dir=state_dir if state_dir is not None else Path(root) / ".agent",
        is_landed=is_landed,
    )


def terminal_operator_message(
    record: IntegrationRecord,
    *,
    reason: str,
    observed_head: str | None,
    preserved_copy: str,
) -> str:
    expected = [
        f"integrated_feature_sha={record.integrated_feature_sha or 'unset'}",
        f"pre_target_sha={record.pre_target_sha or 'unset'}",
        f"pre_feature_sha={record.pre_feature_sha}",
    ]
    if record.rebase_continue_stop:
        expected.append(f"rebase_continue_stop={record.rebase_continue_stop}")
    if record.rebase_continue_head:
        expected.append(f"rebase_continue_head={record.rebase_continue_head}")
    if record.rebase_continue_tree:
        expected.append(f"rebase_continue_tree={record.rebase_continue_tree}")
    if record.merge_commit_parent:
        expected.append(f"merge_commit_parent={record.merge_commit_parent}")
    return (
        f"Integration ladder terminal state: {reason}; "
        f"observed HEAD {observed_head or 'unreadable'}; "
        f"expected {'; '.join(expected)}; "
        f"preserved copy: {preserved_copy}; "
        "operator action required: land the expected commit and run 'ralph --resume'"
    )


def last_resort_recovery(
    root: Path,
    *,
    git: GitPlumbing,
    fs: FileQuarantine,
    ledger: AttemptLedger,
    state_dir: Path,
    clock: Clock,
    feature_tip: str | None,
    target: str,
    branch: str,
    decided_signature: str,
    fresh_signature: str,
    paths: tuple[str, ...] = (),
    untracked_paths: tuple[str, ...] = (),
    in_progress_kind: InProgressKind = InProgressKind.NONE,
    is_landed: Callable[[str], bool] | None = None,
    budget: int = LADDER_ATTEMPT_BUDGET,
    terminal_when_disabled: bool = True,
) -> LadderStepResult:
    rung = CleanupLadderRung.LAST_RESORT
    key = _rung_key(rung)
    _record_evidence(ledger, key, "intent")
    if not decided_signature:
        value = (
            LAST_RESORT_TERMINAL_NO_SIGNATURE
            if terminal_when_disabled
            else LAST_RESORT_DISABLED_NO_SIGNATURE
        )
        _record_evidence(ledger, key, value)
        logger.warning(
            "integration ladder rung 6 (last resort) is disabled for this caller: no "
            "re-observed blocking signature was supplied, so the hard reset never runs "
            "and the ladder ends in a terminal state that needs an operator"
        )
        outcome: LadderOutcome = "terminal" if terminal_when_disabled else "failed"
        return LadderStepResult(rung, outcome, value, evidence={key: value})
    state = ledger.read()
    preconditions = evaluate_last_resort_preconditions(
        state, decided_signature=decided_signature, fresh_signature=fresh_signature, budget=budget
    )
    for name, evidence_value in preconditions.items():
        _record_evidence(ledger, f"precondition:{name}", evidence_value)
    unmet = next((name for name, value in preconditions.items() if value.startswith("unmet")), None)
    if unmet is not None:
        value = f"failed:precondition-unmet:{unmet}"
        _record_evidence(ledger, key, value)
        return LadderStepResult(rung, "failed", value, evidence=preconditions)

    state = ledger.read()
    if PRESERVED_COPY_LEDGER_KEY not in state.ladder_evidence:
        preserved = preserve_via_temp_index(
            root,
            git=git,
            fs=fs,
            ledger=ledger,
            state_dir=state_dir,
            is_landed=is_landed,
        )
        if preserved.outcome == "failed":
            logger.warning(
                "integration ladder rung 6: final preservation failed ({}); "
                "the reset may discard work that nothing could save",
                preserved.reason,
            )

    if in_progress_kind is not InProgressKind.NONE and not git.abort_operation(
        root, in_progress_kind
    ):
        git.remove_operation_state(root, in_progress_kind)

    if feature_tip is not None and git.ref_is_readable(root, feature_tip):
        tip: str = feature_tip
    else:
        tip = target
    if not git.reset_hard(root, tip) and not (
        git.recreate_worktree(root, branch) and git.reset_hard(root, tip)
    ):
        value = f"failed:reset-hard:{tip}"
        _record_evidence(ledger, key, value)
        return LadderStepResult(rung, "failed", value, evidence=preconditions)
    git.clean_untracked(root, untracked_paths)
    removed_locks = git.remove_stale_locks(root)
    value = f"succeeded:reset-hard-to={tip}:locks-removed={len(removed_locks)}"
    _record_evidence(ledger, key, value)
    logger.warning(
        "integration ladder rung 6 (last resort): reset --hard to {}; blocking "
        "untracked paths cleaned; preserved refs and sidecars untouched",
        tip,
    )
    return LadderStepResult(rung, "succeeded", value, evidence=preconditions)


def _refuse_without_preserved_copy(
    root: Path,
    *,
    rung: CleanupLadderRung,
    ledger: AttemptLedger,
    git: GitPlumbing,
    fs: FileQuarantine,
    state_dir: Path,
    is_landed: Callable[[str], bool] | None,
) -> LadderStepResult | None:
    preserved = ensure_preserved_copy(
        root,
        ledger=ledger,
        git=git,
        fs=fs,
        state_dir=state_dir,
        is_landed=is_landed,
    )
    if preserved is None or preserved.outcome != "failed":
        return None
    value = f"failed:no-preserved-copy:{preserved.reason}"
    _record_evidence(ledger, _rung_key(rung), value)
    logger.warning(
        "integration ladder rung {}: refused; no durable preserved copy could be "
        "created ({}), so no destructive cleanup runs",
        rung.value,
        preserved.reason,
    )
    return LadderStepResult(rung, "failed", value, evidence={_rung_key(rung): value})


def run_ladder_rung(
    rung: CleanupLadderRung,
    *,
    root: Path,
    ledger: AttemptLedger,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
    state_dir: Path | None = None,
    paths: tuple[str, ...] = (),
    untracked_paths: tuple[str, ...] = (),
    target: str = "main",
    branch: str = "feature",
    feature_tip: str | None = None,
    in_progress_kind: InProgressKind = InProgressKind.NONE,
    decided_signature: str = "",
    fresh_signature: str = "",
    is_landed: Callable[[str], bool] | None = None,
    observe: Callable[[], str] | None = None,
    remind: Callable[[str], None] | None = None,
    owning_worktree: str | None = None,
    remote: str = "origin",
    budget: int = LADDER_ATTEMPT_BUDGET,
    expected_target_sha: str | None = None,
    terminal_when_disabled: bool = False,
) -> LadderStepResult:
    plumbing = git if git is not None else default_git_plumbing()
    quarantine = fs if fs is not None else default_file_quarantine()
    probe = env if env is not None else default_environment_probe()
    ladder_clock = clock if clock is not None else current_clock()
    ralph_state = state_dir if state_dir is not None else Path(root) / ".agent"
    result: LadderStepResult
    if rung is CleanupLadderRung.NORMAL_CLEANUP:
        value = "failed:not-owned-here"
        _record_evidence(ledger, _rung_key(rung), value)
        result = LadderStepResult(rung, "failed", value, evidence={_rung_key(rung): value})
    elif rung is CleanupLadderRung.ALTERNATE_PRESERVATION:
        result = preserve_via_temp_index(
            root,
            git=plumbing,
            fs=quarantine,
            ledger=ledger,
            state_dir=ralph_state,
            owning_worktree=owning_worktree,
            is_landed=is_landed,
        )
    elif rung is CleanupLadderRung.QUARANTINE:
        refusal = _refuse_without_preserved_copy(
            root,
            rung=rung,
            ledger=ledger,
            git=plumbing,
            fs=quarantine,
            state_dir=ralph_state,
            is_landed=is_landed,
        )
        if refusal is not None:
            result = refusal
        else:
            result = quarantine_and_restore(
                root,
                paths=paths,
                git=plumbing,
                fs=quarantine,
                ledger=ledger,
                state_dir=ralph_state,
                clock=ladder_clock,
            )
    elif rung is CleanupLadderRung.FRESH_WORKTREE:
        refusal = _refuse_without_preserved_copy(
            root,
            rung=rung,
            ledger=ledger,
            git=plumbing,
            fs=quarantine,
            state_dir=ralph_state,
            is_landed=is_landed,
        )
        if refusal is not None:
            result = refusal
        else:
            result = rebuild_in_fresh_worktree(
                root,
                target=target,
                branch=branch,
                git=plumbing,
                ledger=ledger,
                state_dir=ralph_state,
                clock=ladder_clock,
                preserved_ref=feature_tip,
                expected_target_sha=expected_target_sha,
            )
    elif rung is CleanupLadderRung.ENVIRONMENT_DEGRADE:
        result = adapt_environment_and_retry(
            root,
            git=plumbing,
            fs=quarantine,
            env=probe,
            ledger=ledger,
            clock=ladder_clock,
            state_dir=ralph_state,
            remote=remote,
            observe=observe,
            remind=remind,
        )
    else:
        preserved = (
            ensure_preserved_copy(
                root,
                ledger=ledger,
                git=plumbing,
                fs=quarantine,
                state_dir=ralph_state,
                is_landed=is_landed,
            )
            if decided_signature
            else None
        )
        if preserved is not None and preserved.outcome == "failed":
            value = LAST_RESORT_REFUSED_NO_PRESERVED_COPY
            _record_evidence(ledger, _rung_key(rung), value)
            logger.warning(
                "integration ladder rung 6 (last resort): refused; no durable preserved "
                "copy could be created ({}), so the hard reset never runs",
                preserved.reason,
            )
            result = LadderStepResult(rung, "terminal", value, evidence={_rung_key(rung): value})
        else:
            result = last_resort_recovery(
                root,
                git=plumbing,
                fs=quarantine,
                ledger=ledger,
                state_dir=ralph_state,
                clock=ladder_clock,
                feature_tip=feature_tip,
                target=target,
                branch=branch,
                decided_signature=decided_signature,
                fresh_signature=fresh_signature,
                paths=paths,
                untracked_paths=untracked_paths,
                in_progress_kind=in_progress_kind,
                is_landed=is_landed,
                budget=budget,
                terminal_when_disabled=terminal_when_disabled,
            )
    return result


def default_git_plumbing() -> GitPlumbing:
    from ralph.pipeline._integration_ladder_adapters import _ProductionGitPlumbing

    return _ProductionGitPlumbing()


def reap_worktree_stale_locks(git_dir: Path, common_dir: Path) -> tuple[str, ...]:
    from ralph.pipeline._integration_ladder_adapters import reap_ladder_stale_locks

    return reap_ladder_stale_locks(git_dir, common_dir)


def default_file_quarantine() -> FileQuarantine:
    from ralph.pipeline._integration_ladder_adapters import _ProductionFileQuarantine

    return _ProductionFileQuarantine()


def default_environment_probe() -> EnvironmentProbe:
    from ralph.pipeline._integration_ladder_adapters import _ProductionEnvironmentProbe

    return _ProductionEnvironmentProbe()
