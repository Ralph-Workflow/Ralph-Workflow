"""Autonomous fallback-ladder executors for a tree that stays dirty.

The decision table in :mod:`ralph.pipeline.integration_decision` names
the cleanup-ladder rungs (rungs 2-6 of the autonomous fallback ladder in
the product criteria); this module is the MACHINERY behind those names.
Each rung is an executor with a narrow seam (git plumbing, filesystem
quarantine, environment probe, injectable clock) so the ORDER and the
fallback logic of a rung are provable with fakes in milliseconds.

The rungs (see each function for the full contract):

2. :func:`preserve_via_temp_index` — plumbing snapshot commit that
   never touches the working tree or the real index (the subprocess
   runner scrubs ``GIT_INDEX_FILE`` from child environments for the
   D13 fleet-safety rule, so the equivalent ``stash create`` /
   ``commit-tree`` plumbing from
   :mod:`ralph.pipeline.auto_integrate_backup_refs` stands in for the
   temporary-index recipe), else prune regenerable artifacts and
   verified-landed backup refs and write a patch+tar sidecar; proceed
   only once one durable copy is verified readable.
3. :func:`quarantine_and_restore` — rename unrestorable paths into a
   quarantine directory, then force the baseline with
   ``read-tree``/``checkout-index``; impossible rename skips to rung 4.
4. :func:`rebuild_in_fresh_worktree` — rebuild on the current target
   at a new path, apply the preserved work, land from there with
   compare-and-swap (a concurrent target advance names the race and
   overwrites nothing), repoint the original checkout only when it is
   still where the ladder observed it.
5. :func:`adapt_environment_and_retry` — classify the environment
   failure, set the matching safe config for the owned path, then
   capped exponential backoff on the injectable clock, re-observing
   each tick; never exits, one rate-limited reminder.
6. :func:`last_resort_recovery` — hard reset ONLY when all six
   preconditions in :data:`LAST_RESORT_PRECONDITIONS` hold, each
   recorded in the durable ledger; preserves first, aborts the
   operation, resets to the feature tip (or target), cleans blocking
   untracked paths, removes stale locks, recreates the worktree if
   even that fails; never touches preserved refs/sidecar.

Rules for every rung: intent is recorded BEFORE acting and the outcome
AFTER (both in :attr:`AttemptLedgerState.ladder_evidence`); every rung
is idempotent; a rung whose observation shows it cannot succeed is
reported as ``impossible`` and skipped, not repeated; rungs 1-5 never
discard work; preserved copies stay recoverable until landing is
verified. A restart mid-ladder resumes at the recorded rung
(:func:`resume_rung`) without repeating finished rungs or losing
preserved copies. All Git goes through
:func:`ralph.git.subprocess_runner.run_git` (serialized by the
per-repository git command queue); porcelain is parsed with ``-z``;
hooks are disabled where git allows; time goes through the injectable
:class:`~ralph.pipeline.integration_actions.Clock`; large dirt is
streamed (``git diff --output=`` writes the patch straight to disk,
``tarfile`` streams entries).
"""

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

    from ralph.pipeline.integration_attempt_ledger import (
        AttemptLedger,
        AttemptLedgerState,
    )

__all__ = [
    "LADDER_ATTEMPT_BUDGET",
    "LAST_RESORT_DISABLED_NO_SIGNATURE",
    "LAST_RESORT_PRECONDITIONS",
    "EnvironmentFailure",
    "EnvironmentProbe",
    "FileQuarantine",
    "GitPlumbing",
    "LadderOutcome",
    "LadderStepResult",
    "LandResult",
    "RepointResult",
    "adapt_environment_and_retry",
    "evaluate_last_resort_preconditions",
    "ladder_charge_count",
    "last_resort_recovery",
    "preserve_via_temp_index",
    "quarantine_and_restore",
    "rebuild_in_fresh_worktree",
    "record_salvage_attempt",
    "resume_rung",
    "run_ladder_rung",
]


#: Bounded attempt budget for the WHOLE cleanup ladder: the last-resort
#: reset is unreachable until at least this many cleanup-ladder charges
#: are durably recorded, so a transient failure (a lock held for a
#: minute, a remote blip, a writer that stops) has had time to clear and
#: never triggers a reset.
LADDER_ATTEMPT_BUDGET: Final[int] = 16

#: The six last-resort preconditions, in evaluation order. Each name is
#: recorded in the ledger under ``precondition:<name>`` with its
#: evidence; the reset runs only when every value starts with ``met``.
LAST_RESORT_PRECONDITIONS: Final[tuple[str, ...]] = (
    "rungs_1_to_5_tried_or_shown_impossible",
    "salvage_resolver_agent_tried",
    "salvage_rebase_first_ladder_retried",
    "salvage_fresh_worktree_rebuild_tried",
    "ladder_attempt_budget_spent",
    "fresh_reobservation_confirms_blocking_state",
)

#: Rung-6 verdict for a caller that supplies no re-observed blocking
#: signature: the hard reset is explicitly disabled for it (see
#: :func:`last_resort_recovery`).
LAST_RESORT_DISABLED_NO_SIGNATURE: Final[str] = "failed:disabled:no-blocking-signature"

#: Salvage-attempt kinds the last-resort precondition (b) waits for,
#: recorded under ``salvage:<kind>`` by the owning seams through
#: :func:`record_salvage_attempt`.
SALVAGE_KINDS: Final[tuple[str, ...]] = (
    "resolver_agent",
    "rebase_first_retry",
    "fresh_worktree_rebuild",
)


@dataclass(frozen=True)
class LadderStepResult:
    """The typed outcome of one ladder-rung step.

    ``outcome`` is the closed :data:`LadderOutcome` vocabulary;
    ``reason`` names what happened from the CURRENT observation only;
    ``preserved_ref`` / ``preserved_sidecar`` locate the durable copy
    the rung created (at most one is set); ``quarantined_paths`` lists
    what rung 3 moved aside; ``next_rung`` is the escalation hint for
    ``impossible`` outcomes (quarantine names rung 4); ``evidence`` is
    what was recorded into the durable ledger for this step.
    """

    rung: CleanupLadderRung
    outcome: LadderOutcome
    reason: str
    preserved_ref: str | None = None
    preserved_sidecar: str | None = None
    quarantined_paths: tuple[str, ...] = ()
    next_rung: CleanupLadderRung | None = None
    evidence: dict[str, str] = field(default_factory=dict)


# Ledger evidence helpers (intent before, outcome after; restart resume).


def ladder_charge_count(state: AttemptLedgerState) -> int:
    """Return the durable cleanup-ladder attempt count recorded in ``state``.

    Every charged repeat for a cleanup-ladder action counts, plus the
    saturating aggregate that absorbed evicted entries, so the budget
    can never be restored by eviction, restart or reload.
    """
    total = state.saturated_repeats
    for (_signature, action), count in state.repeat_counts.items():
        if action in _LADDER_ACTION_VALUES:
            total += count
    return total


def record_salvage_attempt(ledger: AttemptLedger, kind: str, outcome: str) -> None:
    """Record one salvage attempt for the last-resort precondition (b).

    The owning seams call this after trying a salvage means on the
    leftover state: ``resolver_agent``, ``rebase_first_retry`` (the
    rebase-first ladder re-attempted after a rung) and
    ``fresh_worktree_rebuild`` (rung 4 from the preserved copy).
    ``outcome`` (``succeeded:...`` / ``failed:...``) is terminal.
    """
    if kind not in SALVAGE_KINDS:
        raise ValueError(f"unknown salvage kind: {kind!r}")
    _record_evidence(ledger, f"salvage:{kind}", outcome)


def resume_rung(state: AttemptLedgerState) -> CleanupLadderRung | None:
    """Return the rung a restart resumes at, or ``None`` when the ladder is spent.

    The first rung without a finished recorded outcome is where the run
    resumes: finished rungs are never repeated, a rung left at ``intent``
    (crash between intent and outcome) is re-run idempotently, and
    preserved-copy locations survive the restart verbatim.
    """
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
    """Evaluate the six last-resort preconditions against the durable ledger.

    Every value is ``met: <evidence>`` or ``unmet: <reason>``; the hard
    reset is allowed only when all six are ``met``. This function is
    pure: it reads ``state`` and the two signatures, and the caller
    persists what it returns.
    """
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


# Rung 2 — alternate preservation.


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
    """Rung 2: preserve the dirt with plumbing; fall back to a patch+tar sidecar.

    Tries the durable ref first; when the ref cannot be written, frees
    space (Ralph's regenerable artifacts, then verified-landed backup
    refs) and writes a binary patch plus a tar of the changed paths
    into the state directory. Proceeds only once one durable copy is
    verified readable — never before."""
    rung = CleanupLadderRung.ALTERNATE_PRESERVATION
    key = _rung_key(rung)
    _record_evidence(ledger, key, "intent")
    paths = git.changed_paths(root)
    if paths is None:
        # The probe failed: unknown is NEVER clean. Reporting
        # "nothing-to-preserve" here would let a destructive later rung
        # proceed as if the tree were known-clean — fail closed instead
        # and let the caller re-observe and escalate.
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

    # The ref route failed: free space, then write the patch+tar sidecar.
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
    preserved_dir.mkdir(
        parents=True, exist_ok=True
    )  # filesystem-write-ok: ladder sidecar dir under the Ralph .agent state dir
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


# Rung 3 — quarantine the obstacle.


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
    """Rung 3: move unrestorable paths aside by rename, then force the baseline.

    Paths Git cannot restore (locked, wrong owner, directory-for-file,
    symlink out of tree) are renamed into the quarantine directory; the
    tree is then forced to the baseline with ``read-tree`` +
    ``checkout-index -f``. An impossible rename reports ``impossible``
    and names rung 4 — the rung is skipped, never repeated.
    """
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


# Rung 4 — fresh isolated worktree.


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
    """Rung 4: rebuild on the current target in a fresh isolated worktree.

    Creates a new worktree from the current target, applies the
    preserved work there (cherry-pick; a real conflict is the resolver
    agent's job, decided upstream), then lands the target from the fresh
    worktree with compare-and-swap semantics: the expected sha is the
    one observed at rung start (passed in, or probed fresh — an
    unreadable probe fails closed), and a concurrent advance of the
    target ref is a typed ``failed:land:target-moved-concurrently``
    outcome that names the race and issues no overwrite, so the caller
    re-observes. The original checkout is repointed only when it is
    still where the ladder observed it (or detached and clean per a
    fresh probe); a not-now repoint defers without failing the rung —
    the durable outcome is the landed target. A successful rebuild also
    records the ``salvage:fresh_worktree_rebuild`` evidence
    precondition (b) waits for.
    """
    rung = CleanupLadderRung.FRESH_WORKTREE
    key = _rung_key(rung)
    _record_evidence(ledger, key, "intent")
    # Observe BOTH shas BEFORE any mutation: the expected target sha the
    # CAS will be checked against, and the original worktree's HEAD the
    # safe-repoint precondition will be checked against.
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


# Rung 5 — degrade, don't die.

#: Rate-limited reminder registry: the failure class -> last reminder
#: timestamp. Static key universe (the five EnvironmentFailure members).
_REMINDED_AT: dict[
    str, float
] = {}  # bounded-accumulator-ok: static key universe of the five EnvironmentFailure classes; every entry is overwritten in place, never appended


def _remind_once(
    failure: str,
    clock: Clock,
    remind: Callable[[str], None] | None,
) -> None:
    """Log one rate-limited reminder per failure class, naming the next action."""
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
    """Return how many degrade ticks the ledger has recorded for this rung."""
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
    """Rung 5: adapt the broken environment, then keep going with capped backoff.

    Classifies the environment failure, sets the matching safe config
    for the owned path (``safe.directory`` addition,
    regenerable-artifact pruning, an alternative writable work location,
    gitdir repair), then sleeps a capped exponential backoff on the
    INJECTABLE clock and re-observes, so the moment the environment
    heals the run proceeds. The rung never exits; the reminder is
    logged once per failure class."""
    rung = CleanupLadderRung.ENVIRONMENT_DEGRADE
    key = _rung_key(rung)
    # Read the tick counter BEFORE recording intent: the intent record
    # overwrites the previous ``waiting:n=...`` value, and the backoff
    # must still grow exponentially across ticks.
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
    # ``1 << n`` is the capped exponential step (2 ** n); a shift stays
    # typed ``int`` where ``int ** int`` reads as ``Any`` under mypy here.
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


# Rung 6 — the last of the last resorts: hard reset.


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
) -> LadderStepResult:
    """Rung 6: hard reset, ONLY when every last-resort precondition is met.

    The six preconditions in :data:`LAST_RESORT_PRECONDITIONS` are
    evaluated against the durable ledger and the fresh re-observation;
    each result is recorded under ``precondition:<name>``. When ANY is
    unmet the rung returns ``failed`` naming it and NEVER touches the
    tree — the reset never runs early, never because an attempt merely
    failed once, and never when the state healed by itself. When all
    six hold: preservation runs first, the operation is aborted (or its
    state deleted), the tree resets to the feature tip (or the current
    target when the tip is unusable), blocking untracked paths are
    cleaned, stale locks removed, the worktree recreated if even the
    reset fails. Preserved refs/sidecars are never touched.

    **Disabled without a blocking signature.** The reset is only safe for
    a caller that re-observed the blocking state within the current tick
    and passes BOTH the decided and the fresh signature (plus the real
    feature tip and branch). A caller that passes no ``decided_signature``
    -- today the planning seam
    (:func:`ralph.pipeline._integration_owner_seam.execute_planning_ladder_rung`),
    which holds neither a re-observation signature nor the feature tip --
    gets the explicit verdict ``failed:disabled:no-blocking-signature``
    instead of a misleading precondition miss, and the tree is never
    touched. Stale locks are removed through the shared age + liveness +
    identity reaper and only from this worktree's own git dir plus the
    common dir's shared top-level and ref locks.
    """
    rung = CleanupLadderRung.LAST_RESORT
    key = _rung_key(rung)
    _record_evidence(ledger, key, "intent")
    if not decided_signature:
        value = LAST_RESORT_DISABLED_NO_SIGNATURE
        _record_evidence(ledger, key, value)
        logger.warning(
            "integration ladder rung 6 (last resort) is disabled for this caller: no "
            "re-observed blocking signature was supplied, so the hard reset never runs"
        )
        return LadderStepResult(rung, "failed", value, evidence={key: value})
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

    # All six preconditions hold. Any preservation that can still run
    # runs immediately before the reset — the reset loses only what no
    # rung could save.
    state = ledger.read()
    if "preserved:alternate_preservation" not in state.ladder_evidence and paths:
        preserved = preserve_via_temp_index(
            root,
            git=git,
            fs=fs,
            ledger=ledger,
            state_dir=state_dir,
            is_landed=is_landed,
        )
        if preserved.preserved_ref is None and preserved.preserved_sidecar is None:
            logger.warning(
                "integration ladder rung 6: final preservation also failed; "
                "the reset loses only what nothing could save"
            )

    if in_progress_kind is not InProgressKind.NONE and not git.abort_operation(
        root, in_progress_kind
    ):
        git.remove_operation_state(root, in_progress_kind)

    if feature_tip is not None and git.ref_is_readable(root, feature_tip):
        tip: str = feature_tip  # the feature tip is usable.
    else:
        tip = target  # the tip is unusable: reset to the current target.
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


# Dispatcher.


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
) -> LadderStepResult:
    """Execute one cleanup-ladder rung through the owner-routed projection.

    Unspecified seams resolve to the production adapters and the
    installed clock, so a boundary holding the owner's verdict runs
    any rung with one call.
    """
    plumbing = git if git is not None else default_git_plumbing()
    quarantine = fs if fs is not None else default_file_quarantine()
    probe = env if env is not None else default_environment_probe()
    ladder_clock = clock if clock is not None else current_clock()
    ralph_state = state_dir if state_dir is not None else Path(root) / ".agent"
    if rung is CleanupLadderRung.NORMAL_CLEANUP:
        value = "failed:not-owned-here"
        _record_evidence(ledger, _rung_key(rung), value)
        return LadderStepResult(rung, "failed", value, evidence={_rung_key(rung): value})
    if rung is CleanupLadderRung.ALTERNATE_PRESERVATION:
        return preserve_via_temp_index(
            root,
            git=plumbing,
            fs=quarantine,
            ledger=ledger,
            state_dir=ralph_state,
            owning_worktree=owning_worktree,
            is_landed=is_landed,
        )
    if rung is CleanupLadderRung.QUARANTINE:
        return quarantine_and_restore(
            root,
            paths=paths,
            git=plumbing,
            fs=quarantine,
            ledger=ledger,
            state_dir=ralph_state,
            clock=ladder_clock,
        )
    if rung is CleanupLadderRung.FRESH_WORKTREE:
        return rebuild_in_fresh_worktree(
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
    if rung is CleanupLadderRung.ENVIRONMENT_DEGRADE:
        return adapt_environment_and_retry(
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
    return last_resort_recovery(
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
    )


# ---------------------------------------------------------------------------
# Default seam resolvers (lazy: the adapters live in the private companion
# module so this file stays under the repo-structure size cap).
# ---------------------------------------------------------------------------


def default_git_plumbing() -> GitPlumbing:
    """Return the production git-plumbing adapter (queue-routed git runner)."""
    from ralph.pipeline._integration_ladder_adapters import _ProductionGitPlumbing

    return _ProductionGitPlumbing()


def default_file_quarantine() -> FileQuarantine:
    """Return the production filesystem-quarantine adapter."""
    from ralph.pipeline._integration_ladder_adapters import _ProductionFileQuarantine

    return _ProductionFileQuarantine()


def default_environment_probe() -> EnvironmentProbe:
    """Return the production environment-probe adapter."""
    from ralph.pipeline._integration_ladder_adapters import _ProductionEnvironmentProbe

    return _ProductionEnvironmentProbe()
