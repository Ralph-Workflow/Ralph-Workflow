from __future__ import annotations

import subprocess
import threading
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from git import Repo
from loguru import logger

from ralph.git.git_call_policy import classify_git_exception, quarantine_reason
from ralph.git.git_command_queue import (
    WorktreeLeaseHolder,
    end_agent_lease,
    is_lease_held,
    try_acquire_lease,
)
from ralph.git.git_run_result import GitOutcomeKind
from ralph.git.lock_recovery import (
    git_lock_candidates,
    lock_has_no_live_holder,
    lock_identity,
    unlink_if_unchanged,
)
from ralph.git.merge import (
    WORKTREE_FOUND,
    WORKTREE_QUERY_FAILED,
    fast_forward_via_worktree,
    worktree_lookup,
)
from ralph.git.operations import find_main_worktree_root
from ralph.pipeline._auto_integrate_agent_phase import (
    AgentPhaseActivity,
    process_agent_phases,
    same_checkout,
)
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline.auto_integrate_backoff import compute_retry_delay
from ralph.pipeline.auto_integrate_catchup_coordination import (
    RemoteSyncLease,
    remote_sync_interval_seconds,
    remote_sync_transaction,
)
from ralph.pipeline.auto_integrate_ff import fast_forward_target
from ralph.pipeline.auto_integrate_record import read_record
from ralph.pipeline.auto_integrate_remote_sync import (
    FETCH_TIMEOUT_SECONDS,
    remote_sync_enabled,
    remote_target_name,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_ORIGIN_AHEAD,
    REFRESH_UNREACHABLE,
    refresh_target_from_remote,
)
from ralph.pipeline.integration_actions import default_clock
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.process.manager import ProcessTerminationError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from git import Head

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_ff import RemoteClient
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_actions import Clock

DEFAULT_CATCHUP_INTERVAL_SECONDS: float = 30.0

CATCHUP_DISABLED = "disabled"
CATCHUP_NO_TARGET = "no-target"
CATCHUP_NOT_ON_BRANCH = "not-on-branch"
CATCHUP_ON_TARGET = "on-target"
CATCHUP_DIRTY = "worktree-dirty"
CATCHUP_TARGET_UNREADABLE = "target-unreadable"
CATCHUP_HEAD_UNREADABLE = "head-unreadable"
CATCHUP_UP_TO_DATE = "up-to-date"
CATCHUP_DIVERGED = "diverged"
CATCHUP_REFUSED = "refused"
CATCHUP_REMOTE_SKIPPED = "remote-skipped"
CATCHUP_FAST_FORWARDED = "fast-forwarded"

CATCHUP_DEFERRED_AGENT_PHASE = "deferred: an agent phase is active in this checkout"

CATCHUP_REFUSED_AGENT_ACTIVE = "refused: agent is currently active"
CATCHUP_LEASE_UNKNOWN = "failed: agent lease state is unknown"
CATCHUP_LEDGER_UNAVAILABLE = "failed: repository attempt ledger is unavailable"
CATCHUP_TIMEOUT = "failed: fast-forward timed out"
CATCHUP_KILLED = "failed: fast-forward was killed"
CATCHUP_UNKNOWN = "failed: fast-forward outcome is unknown"
CATCHUP_DEFEDITELY_FAILED = "failed: fast-forward definitely failed"
CATCHUP_LADDER_ESCALATED = "ladder: fast-forward quarantined after repeated attempts"

CATCHUP_LOCK_HELD = "refused: a git lock in this repository is held by a live process"
CATCHUP_LOCK_UNKNOWN = "failed: the owner of a git lock in this repository is unknown"

_CATCHUP_SETTLED_OUTCOMES = (
    CATCHUP_DISABLED,
    CATCHUP_NO_TARGET,
    CATCHUP_NOT_ON_BRANCH,
    CATCHUP_ON_TARGET,
    CATCHUP_UP_TO_DATE,
    CATCHUP_DIRTY,
    CATCHUP_REFUSED_AGENT_ACTIVE,
    CATCHUP_DEFERRED_AGENT_PHASE,
)

DEFAULT_CATCHUP_STALL_WINDOW_SECONDS: float = 300.0

_CATCHUP_LEDGER_ACTION = "catchup_fast_forward"

type _LeaseVerdict = Literal["acquired", "held", "unknown"]


@dataclass
class CatchupLedgerFailureBudget:
    consecutive_failures: int = 0

    def record_failure(self) -> int:
        self.consecutive_failures += 1
        return self.consecutive_failures

    def record_success(self) -> None:
        self.consecutive_failures = 0


@dataclass(frozen=True)
class _CatchupObservation:
    target: str
    current: str
    target_sha: str
    current_sha: str

    @property
    def signature(self) -> str:
        t, h = self.target, self.current
        return f"catchup|target={t}@{self.target_sha}|head={h}@{self.current_sha}"

    @property
    def summary(self) -> str:
        return f"target={self.target}@{self.target_sha}; head={self.current}@{self.current_sha}"


def _current_branch_name(root: Path) -> str | None:
    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.active_branch.name
    except Exception:
        return None
    finally:
        _close_repo(repo)


def resolve_integration_target(config: UnifiedConfig, root: Path) -> str | None:
    configured: object = getattr(config.general, "auto_integrate_target", None)
    if not isinstance(configured, str) or not configured:
        return None
    sha, query_ok = observe_branch_sha(root, configured)
    return configured if query_ok and sha is not None else None


def _local_head(repo: Repo, name: str) -> Head | None:
    try:
        return next((head for head in repo.heads if head.name == name), None)
    except Exception:
        return None


def observe_branch_sha(root: Path, name: str) -> tuple[str | None, bool]:
    repo: Repo | None = None
    try:
        repo = Repo(root)
        head = _local_head(repo, name)
        if head is None:
            return None, True
        return head.commit.hexsha, True
    except Exception:
        return None, False
    finally:
        _close_repo(repo)


def is_ancestor(root: Path, ancestor_sha: str, descendant_sha: str) -> bool:
    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.is_ancestor(repo.commit(ancestor_sha), repo.commit(descendant_sha))
    except Exception:
        return False
    finally:
        _close_repo(repo)


def _worktree_is_clean(root: Path) -> bool:
    repo: Repo | None = None
    try:
        repo = Repo(root)
        status_out = cast("str", repo.git.status("--porcelain", "--untracked-files=no"))
        return not status_out.strip()
    except Exception:
        return False
    finally:
        _close_repo(repo)


def _close_repo(repo: Repo | None) -> None:
    if repo is not None:
        with suppress(Exception):
            repo.close()


def _recover_stale_locks(root: Path) -> str:
    repo: Repo | None = None
    try:
        repo = Repo(root)
        git_dir = Path(repo.git_dir)
        common_dir = Path(repo.common_dir)
    except Exception:
        return ""
    finally:
        _close_repo(repo)
    removed: list[Path] = []
    for lock_path in git_lock_candidates(git_dir, common_dir):
        try:
            verdict = lock_has_no_live_holder(lock_path, git_dir, common_dir)
        except Exception:
            verdict = "unknown"
        if verdict != "dead":
            reason = CATCHUP_LOCK_HELD if verdict == "live" else CATCHUP_LOCK_UNKNOWN
            return f"{reason}; lock={lock_path.name}"
        identity = lock_identity(lock_path)
        if identity is None or not unlink_if_unchanged(lock_path, identity):
            return f"{CATCHUP_LOCK_UNKNOWN}; lock={lock_path.name}"
        removed.append(lock_path)
    if removed:
        logger.warning(
            "auto_integrate catch-up: recovered a stale git lock; locks={}",
            ",".join(path.name for path in removed),
        )
    return ""


def attempt_catchup_fast_forward(
    config: UnifiedConfig | None,
    root: Path,
    *,
    target: str | None = None,
    remote: str | None = None,
    remote_client: RemoteClient | None = None,
    ledger: AttemptLedger | None = None,
    clock: Clock | None = None,
    jitter: Callable[[], float] | None = None,
    ledger_failure_budget: CatchupLedgerFailureBudget | None = None,
    agent_phases: AgentPhaseActivity | None = None,
) -> str:
    if not _catchup_enabled(config):
        return CATCHUP_DISABLED
    assert config is not None
    remote_enabled = remote_sync_enabled(config)
    current = _current_branch_name(root)
    if current is None and not remote_enabled:
        return CATCHUP_NOT_ON_BRANCH
    resolved_target = target if target is not None else resolve_integration_target(config, root)
    if resolved_target is None:
        return CATCHUP_NO_TARGET
    effective_clock = clock if clock is not None else default_clock()
    phases = agent_phases if agent_phases is not None else process_agent_phases()
    remote_outcome: str | None = None
    if remote_enabled:
        remote_outcome = _synchronize_remote_target(
            config,
            root,
            resolved_target,
            remote=remote,
            remote_client=remote_client,
            ledger=ledger,
            clock=effective_clock,
            jitter=jitter,
            ledger_failure_budget=ledger_failure_budget,
            agent_phases=phases,
        )
    remote_failed = remote_outcome not in (None, CATCHUP_FAST_FORWARDED)
    if remote_failed or current is None or current == resolved_target:
        return remote_outcome or (CATCHUP_NOT_ON_BRANCH if current is None else CATCHUP_ON_TARGET)
    if phases.is_active(root):
        return CATCHUP_DEFERRED_AGENT_PHASE
    seam_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)
    return _fast_forward_if_strictly_behind(
        root,
        resolved_target,
        current,
        remote=seam_remote,
        ledger=ledger,
        clock=effective_clock,
        jitter=jitter,
        ledger_failure_budget=ledger_failure_budget,
        agent_phases=phases,
    )


def _catchup_enabled(config: UnifiedConfig | None) -> bool:
    if config is None:
        return False
    enabled_raw: object = getattr(config.general, "auto_integrate_enabled", True)
    return isinstance(enabled_raw, bool) and enabled_raw


def _repository_ledger(root: Path) -> AttemptLedger:
    repo: Repo | None = None
    try:
        repo = Repo(root, search_parent_directories=True)
        common_dir = Path(repo.common_dir).resolve()
        return AttemptLedger(root, common_dir=common_dir)
    finally:
        _close_repo(repo)


@contextmanager
def _mutation_lease(root: Path) -> Iterator[_LeaseVerdict]:
    try:
        if is_lease_held(root):
            yield "held"
            return
        lease = try_acquire_lease(
            root,
            holder=WorktreeLeaseHolder(name="auto-integrate-catchup"),
        )
    except Exception:
        yield "unknown"
        return
    if lease is None:
        yield "held"
        return
    try:
        yield "acquired"
    finally:
        try:
            end_agent_lease(lease)
        except Exception as exc:
            logger.warning(
                "auto_integrate catch-up lease release failed: {}",
                exc,
            )


def _lease_failure(verdict: _LeaseVerdict) -> str:
    if verdict == "held":
        return CATCHUP_REFUSED_AGENT_ACTIVE
    return CATCHUP_LEASE_UNKNOWN


def _classify_fast_forward_result(result: bool) -> GitOutcomeKind:
    return GitOutcomeKind.SUCCEEDED if result else GitOutcomeKind.FAILED


def _classify_git_exception(exc: Exception) -> GitOutcomeKind:
    return classify_git_exception(exc).kind


_FAILURE_REASONS: dict[GitOutcomeKind, str] = {
    GitOutcomeKind.SUCCEEDED: CATCHUP_FAST_FORWARDED,
    GitOutcomeKind.FAILED: CATCHUP_DEFEDITELY_FAILED,
    GitOutcomeKind.TIMED_OUT: CATCHUP_TIMEOUT,
    GitOutcomeKind.KILLED: CATCHUP_KILLED,
    GitOutcomeKind.UNKNOWN: CATCHUP_UNKNOWN,
}


def _failure_reason(kind: GitOutcomeKind, observation: str) -> str:
    reason = _FAILURE_REASONS[kind]
    return reason if kind == GitOutcomeKind.SUCCEEDED else f"{reason}; {observation}"


def _backoff_after_failure(
    clock: Clock,
    attempt_count: int,
    *,
    jitter: Callable[[], float] | None,
) -> None:
    try:
        delay = compute_retry_delay(attempt_count, jitter=jitter)
        clock.sleep(delay)
    except Exception as exc:
        logger.warning(
            "auto_integrate catch-up backoff failed (attempt {}): {}",
            attempt_count,
            exc,
        )


def _ledger_failure_outcome(
    signature: str,
    observation: str,
    failure_budget: CatchupLedgerFailureBudget | None,
    clock: Clock | None,
    jitter: Callable[[], float] | None,
) -> tuple[int, str]:
    count = failure_budget.record_failure() if failure_budget is not None else 1
    if clock is not None:
        _backoff_after_failure(clock, count, jitter=jitter)
    if quarantine_reason("mutation", signature, same_state_attempts=count) is not None:
        return count, (
            f"{CATCHUP_LADDER_ESCALATED}; cause={CATCHUP_LEDGER_UNAVAILABLE}; "
            f"attempts={count}; {observation}"
        )
    return count, f"{CATCHUP_LEDGER_UNAVAILABLE}; attempts={count}; {observation}"


def _charge_attempt(
    root: Path,
    ledger: AttemptLedger | None,
    signature: str,
    observation: str,
    *,
    failure_budget: CatchupLedgerFailureBudget | None = None,
    clock: Clock | None = None,
    jitter: Callable[[], float] | None = None,
) -> tuple[int, str]:
    try:
        effective_ledger = ledger if ledger is not None else _repository_ledger(root)
        state = effective_ledger.read()
        prior_count = state.repeat_counts.get((signature, _CATCHUP_LEDGER_ACTION), 0)
        if quarantine_reason("mutation", signature, same_state_attempts=prior_count) is not None:
            return (
                prior_count,
                f"{CATCHUP_LADDER_ESCALATED}; rung={state.rung.value}; {observation}",
            )
        charged = effective_ledger.charge(
            signature,
            _CATCHUP_LEDGER_ACTION,
            is_unknown=False,
        )
    except Exception:
        return _ledger_failure_outcome(signature, observation, failure_budget, clock, jitter)
    if failure_budget is not None:
        failure_budget.record_success()
    return charged.repeat_counts.get((signature, _CATCHUP_LEDGER_ACTION), 0), ""


def _synchronize_remote_target(
    config: UnifiedConfig,
    root: Path,
    target: str,
    *,
    remote: str | None = None,
    remote_client: RemoteClient | None = None,
    ledger: AttemptLedger | None = None,
    clock: Clock | None = None,
    jitter: Callable[[], float] | None = None,
    ledger_failure_budget: CatchupLedgerFailureBudget | None = None,
    agent_phases: AgentPhaseActivity | None = None,
) -> str | None:

    chosen_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)
    effective_clock = clock if clock is not None else default_clock()
    with remote_sync_transaction(
        root, chosen_remote, target, interval_seconds=remote_sync_interval_seconds(config)
    ) as lease:
        if lease is None:
            return CATCHUP_REMOTE_SKIPPED
        with _mutation_lease(root) as lease_verdict:
            if lease_verdict != "acquired":
                return _lease_failure(lease_verdict)
            return _refresh_remote_target(
                root,
                target,
                chosen_remote,
                lease=lease,
                remote_client=remote_client,
                ledger=ledger,
                clock=effective_clock,
                jitter=jitter,
                ledger_failure_budget=ledger_failure_budget,
                agent_phases=agent_phases,
            )


def _refresh_remote_target(
    root: Path,
    target: str,
    remote: str,
    *,
    lease: RemoteSyncLease,
    remote_client: RemoteClient | None,
    ledger: AttemptLedger | None,
    clock: Clock,
    jitter: Callable[[], float] | None,
    ledger_failure_budget: CatchupLedgerFailureBudget | None = None,
    agent_phases: AgentPhaseActivity | None = None,
) -> str | None:

    if not lease.fetch_allowed:
        if not _cached_remote_strictly_ahead(root, remote, target, remote_client=remote_client):
            return None
    else:
        if remote_client is not None:
            outcome = remote_client.refresh_target(
                root,
                target,
                timeout_seconds=FETCH_TIMEOUT_SECONDS,
                remote=remote,
            )
        else:
            outcome = refresh_target_from_remote(
                root,
                target,
                timeout_seconds=FETCH_TIMEOUT_SECONDS,
                remote=remote,
            )
        if outcome == REFRESH_UNREACHABLE:
            return CATCHUP_REMOTE_SKIPPED
        try:
            lease.record_fetch()
        except OSError:
            return CATCHUP_REMOTE_SKIPPED
        if outcome != REFRESH_ORIGIN_AHEAD:
            return None
    return _advance_target_to_remote(
        root,
        remote,
        target,
        remote_client=remote_client,
        ledger=ledger,
        clock=clock,
        jitter=jitter,
        ledger_failure_budget=ledger_failure_budget,
        agent_phases=agent_phases,
    )


def _cached_remote_strictly_ahead(
    root: Path,
    remote: str,
    target: str,
    *,
    remote_client: RemoteClient | None,
) -> bool:
    if remote_client is not None and hasattr(remote_client, "observe_remote_sha"):
        remote_sha = remote_client.observe_remote_sha(root, target, remote=remote)
    else:
        remote_sha = observe_remote_target_sha(root, remote, target)
    local_sha, local_ok = observe_branch_sha(root, target)
    if remote_sha is None or not local_ok or local_sha is None or local_sha == remote_sha:
        return False
    return is_ancestor(root, local_sha, remote_sha)


def _observe_remote_target(
    root: Path,
    remote: str,
    target: str,
    *,
    remote_client: RemoteClient | None,
    agent_phases: AgentPhaseActivity,
) -> tuple[str | None, str]:

    if remote_client is not None and hasattr(remote_client, "observe_remote_sha"):
        remote_sha = remote_client.observe_remote_sha(root, target, remote=remote)
    else:
        remote_sha = observe_remote_target_sha(root, remote, target)
    if remote_sha is None:
        return None, f"{CATCHUP_DEFEDITELY_FAILED}; target={target}@{remote}"
    primary_root = find_main_worktree_root(root)
    verdict, owner = worktree_lookup(primary_root, target)
    if verdict == WORKTREE_QUERY_FAILED:
        return None, f"{CATCHUP_UNKNOWN}; target={target}@{remote}"
    if verdict == WORKTREE_FOUND and owner is not None:
        if agent_phases.is_active(owner):
            return None, CATCHUP_DEFERRED_AGENT_PHASE
        if not _worktree_is_clean(owner):
            return None, CATCHUP_REFUSED
    return remote_sha, ""


def _advance_target_to_remote(
    root: Path,
    remote: str,
    target: str,
    *,
    remote_client: RemoteClient | None = None,
    ledger: AttemptLedger | None = None,
    clock: Clock | None = None,
    jitter: Callable[[], float] | None = None,
    ledger_failure_budget: CatchupLedgerFailureBudget | None = None,
    agent_phases: AgentPhaseActivity | None = None,
) -> str | None:

    phases = agent_phases if agent_phases is not None else process_agent_phases()
    with phases.mutation_window():
        return _advance_observed_target(
            root,
            remote,
            target,
            remote_client=remote_client,
            ledger=ledger,
            clock=clock,
            jitter=jitter,
            ledger_failure_budget=ledger_failure_budget,
            agent_phases=phases,
        )


def _advance_observed_target(
    root: Path,
    remote: str,
    target: str,
    *,
    remote_client: RemoteClient | None,
    ledger: AttemptLedger | None,
    clock: Clock | None,
    jitter: Callable[[], float] | None,
    ledger_failure_budget: CatchupLedgerFailureBudget | None,
    agent_phases: AgentPhaseActivity,
) -> str | None:
    remote_sha, observe_reason = _observe_remote_target(
        root,
        remote,
        target,
        remote_client=remote_client,
        agent_phases=agent_phases,
    )
    if remote_sha is None:
        return observe_reason
    signature = f"catchup-target|remote={remote}|target={target}@{remote_sha}"
    observation = f"target={target}@{remote_sha}; remote={remote}@{remote_sha}"
    attempt_count, charge_reason = _charge_attempt(
        root,
        ledger,
        signature,
        observation,
        failure_budget=ledger_failure_budget,
        clock=clock if clock is not None else default_clock(),
        jitter=jitter,
    )
    if charge_reason:
        return charge_reason
    effective_clock = clock if clock is not None else default_clock()
    try:
        from ralph.pipeline.resolution_outcome import (
            ConfigFailureOutcome,
            NextStrategyOutcome,
            ResolvedOutcome,
            RetryLaterOutcome,
            UnknownOutcome,
            WaitingOutcome,
            stop_resolution_outcome_reason,
        )
        outcome: object = fast_forward_target(
            root,
            target,
            remote_sha,
            reclaim_target_worktree=False,
        )
        if isinstance(outcome, tuple):
            advanced = bool(outcome[0])
            refusal_reason = str(outcome[1]) if len(outcome) > 1 and not advanced else ""
        elif isinstance(outcome, bool):
            advanced = outcome
            refusal_reason = ""
        elif isinstance(
            outcome,
            (
                ResolvedOutcome,
                WaitingOutcome,
                RetryLaterOutcome,
                NextStrategyOutcome,
                ConfigFailureOutcome,
                UnknownOutcome,
            ),
        ):
            advanced = isinstance(outcome, ResolvedOutcome)
            refusal_reason = "" if advanced else stop_resolution_outcome_reason(outcome)
        else:
            advanced = False
            refusal_reason = str(outcome)
    except Exception as exc:
        kind = _classify_git_exception(exc)
        if kind != GitOutcomeKind.FAILED:
            _backoff_after_failure(effective_clock, attempt_count, jitter=jitter)
        return _failure_reason(kind, observation)
    if advanced:
        return CATCHUP_FAST_FORWARDED
    return f"{CATCHUP_DEFEDITELY_FAILED}; {refusal_reason or outcome}"


def observe_remote_target_sha(root: Path, remote: str, target: str) -> str | None:

    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.commit(f"refs/remotes/{remote}/{target}").hexsha
    except Exception:
        return None
    finally:
        _close_repo(repo)


def _mutation_refusal(
    root: Path, target: str, current: str, *, remote: str | None, agent_active: bool
) -> str:

    if agent_active:
        return CATCHUP_DEFERRED_AGENT_PHASE
    if not _worktree_is_clean(root):
        return CATCHUP_DIRTY
    if not _still_safe_to_merge(root, current):
        return CATCHUP_REFUSED
    if (root / ".git").exists():
        action = decide_seam_action(
            root, record=read_record(root), agent_active=agent_active, target=target, remote=remote
        )
        if isinstance(action, RecordFactOutcome):
            return f"{CATCHUP_REFUSED}; owner escalated {action.value} through the cleanup ladder"
        if action is IntegrationAction.RESOLVE_ACTIVE_OPERATION:
            return f"{CATCHUP_REFUSED}; owner required {action.value}"
    return ""


def _fast_forward_if_strictly_behind(
    root: Path,
    target: str,
    current: str,
    *,
    remote: str | None = None,
    ledger: AttemptLedger | None = None,
    clock: Clock | None = None,
    jitter: Callable[[], float] | None = None,
    ledger_failure_budget: CatchupLedgerFailureBudget | None = None,
    agent_phases: AgentPhaseActivity | None = None,
) -> str:

    phases = agent_phases if agent_phases is not None else process_agent_phases()
    observation, skip_reason = _observe_strictly_behind(root, target, current)
    if observation is None:
        return skip_reason
    effective_clock = clock if clock is not None else default_clock()
    with _mutation_lease(root) as lease_verdict, phases.mutation_window():
        if lease_verdict != "acquired":
            logger.debug(
                "auto_integrate catch-up tick deferred: {}; {}",
                _lease_failure(lease_verdict),
                observation.summary,
            )
            return _lease_failure(lease_verdict)
        refusal = _mutation_refusal(
            root, target, current, remote=remote, agent_active=phases.is_active(root)
        )
        if refusal:
            return refusal
        lock_reason = _recover_stale_locks(root)
        if lock_reason:
            _backoff_after_failure(effective_clock, 1, jitter=jitter)
            return lock_reason
        attempt_count, charge_reason = _charge_attempt(
            root,
            ledger,
            observation.signature,
            observation.summary,
            failure_budget=ledger_failure_budget,
            clock=effective_clock,
            jitter=jitter,
        )
        if charge_reason:
            return charge_reason
        return _execute_fast_forward(
            root,
            observation,
            attempt_count=attempt_count,
            clock=effective_clock,
            jitter=jitter,
        )


def _execute_fast_forward(
    root: Path,
    observation: _CatchupObservation,
    *,
    attempt_count: int,
    clock: Clock,
    jitter: Callable[[], float] | None,
) -> str:
    try:
        merged = fast_forward_via_worktree(root, observation.target_sha)
    except Exception as exc:
        kind = _classify_git_exception(exc)
        if kind != GitOutcomeKind.FAILED:
            _backoff_after_failure(clock, attempt_count, jitter=jitter)
        return _failure_reason(kind, observation.summary)
    kind = _classify_fast_forward_result(merged)
    if kind == GitOutcomeKind.SUCCEEDED:
        return CATCHUP_FAST_FORWARDED
    return _failure_reason(kind, observation.summary)


def _still_safe_to_merge(root: Path, expected_branch: str) -> bool:
    repo: Repo | None = None
    try:
        repo = Repo(root)
        git_dir = Path(repo.git_dir)
        if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
            return False
        return repo.active_branch.name == expected_branch
    except Exception:
        return False
    finally:
        _close_repo(repo)


def _observe_strictly_behind(
    root: Path,
    target: str,
    current: str,
) -> tuple[_CatchupObservation | None, str]:

    target_sha, target_ok = observe_branch_sha(root, target)
    if not target_ok or target_sha is None:
        return None, CATCHUP_TARGET_UNREADABLE
    current_sha, current_ok = observe_branch_sha(root, current)
    if not current_ok or current_sha is None:
        return None, CATCHUP_HEAD_UNREADABLE
    if current_sha == target_sha:
        return None, CATCHUP_UP_TO_DATE
    if not is_ancestor(root, current_sha, target_sha):
        return None, CATCHUP_DIVERGED
    obs = _CatchupObservation(target, current, target_sha, current_sha)
    return obs, ""


@dataclass(frozen=True)
class _CatchupStallState:
    cause: str
    elapsed_seconds: float
    window_seconds: float


class AutoIntegrateCatchupWorker:
    def __init__(
        self,
        config: UnifiedConfig,
        root: Path,
        *,
        interval_seconds: float = DEFAULT_CATCHUP_INTERVAL_SECONDS,
        tick: Callable[[], str] | None = None,
        wait: Callable[[float], bool] | None = None,
        remote_client: RemoteClient | None = None,
        rebase_stop_resolver: RebaseStopResolver | None = None,
        ledger: AttemptLedger | None = None,
        clock: Clock | None = None,
        jitter: Callable[[], float] | None = None,
        stall_window_seconds: float = DEFAULT_CATCHUP_STALL_WINDOW_SECONDS,
        agent_phases: AgentPhaseActivity | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if stall_window_seconds <= 0:
            raise ValueError("stall_window_seconds must be positive")
        self._config = config
        self._root = root
        self._interval = float(interval_seconds)
        self._remote_client = remote_client
        self._rebase_stop_resolver = rebase_stop_resolver
        self._ledger = ledger
        self._ledger_failure_budget = CatchupLedgerFailureBudget()
        self._clock = clock
        self._effective_clock = clock if clock is not None else default_clock()
        self._jitter = jitter
        self._agent_phases = agent_phases if agent_phases is not None else process_agent_phases()
        self._stall_window = float(stall_window_seconds)
        self._last_progress_at = self._effective_clock.now()
        self._stall_state: _CatchupStallState | None = None
        self._tick: Callable[[], str] = tick if tick is not None else self._default_tick
        self._stop_event = threading.Event()
        self._wait = wait if wait is not None else self._stop_event.wait
        self._thread: threading.Thread | None = None

    def _default_tick(self) -> str:
        return attempt_catchup_fast_forward(
            self._config,
            self._root,
            remote_client=self._remote_client,
            ledger=self._ledger,
            clock=self._clock,
            jitter=self._jitter,
            ledger_failure_budget=self._ledger_failure_budget,
            agent_phases=self._agent_phases,
        )

    @property
    def agent_phases(self) -> AgentPhaseActivity:
        return self._agent_phases

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        thread = threading.Thread(
            target=self.run,
            name="ralph-auto-integrate-catchup",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stall_state(self) -> _CatchupStallState | None:
        return self._stall_state

    def _record_progress(self, outcome: str) -> None:
        now = self._effective_clock.now()
        if outcome == CATCHUP_FAST_FORWARDED or outcome in _CATCHUP_SETTLED_OUTCOMES:
            self._last_progress_at = now
            self._stall_state = None
            return
        elapsed = max(0.0, now - self._last_progress_at)
        if elapsed < self._stall_window:
            return
        previous = self._stall_state
        self._stall_state = _CatchupStallState(outcome, elapsed, self._stall_window)
        if previous is not None and previous.cause == outcome:
            return
        logger.warning(
            "auto_integrate catch-up STALLED: no progress for {:.1f}s (window {:.1f}s); cause: {}",
            elapsed,
            self._stall_window,
            outcome,
        )

    def run(self) -> None:
        while not self._wait(self._interval):
            self._tick_once()

    def _tick_once(self) -> None:
        try:
            outcome = self._tick()
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            logger.warning("auto_integrate catch-up tick failed: {}: {}", CATCHUP_TIMEOUT, exc)
            self._record_progress(CATCHUP_TIMEOUT)
            return
        except ProcessTerminationError as exc:
            logger.warning("auto_integrate catch-up tick failed: {}: {}", CATCHUP_KILLED, exc)
            self._record_progress(CATCHUP_KILLED)
            return
        except Exception as exc:
            logger.warning("auto_integrate catch-up tick failed: {}: {}", CATCHUP_UNKNOWN, exc)
            self._record_progress(CATCHUP_UNKNOWN)
            return
        self._record_progress(outcome)
        if (
            self._config is not None
            and outcome is not None
            and outcome
            not in {
                CATCHUP_DISABLED,
                CATCHUP_NO_TARGET,
                CATCHUP_NOT_ON_BRANCH,
                CATCHUP_REFUSED_AGENT_ACTIVE,
                CATCHUP_REFUSED,
            }
            and not outcome.startswith(("failed:", "ladder:"))
        ):
            self._publish_after_tick()
        if outcome == CATCHUP_FAST_FORWARDED:
            logger.info("auto_integrate catch-up: fast-forwarded the checkout onto the target")
        elif outcome == CATCHUP_REFUSED_AGENT_ACTIVE:
            logger.debug("auto_integrate catch-up tick deferred: {}", outcome)
        elif outcome.startswith(("failed:", "ladder:")) or outcome == CATCHUP_REFUSED:
            logger.warning("auto_integrate catch-up tick failed: {}", outcome)

    def _publication_window(self, target: str) -> AbstractContextManager[None] | None:
        try:
            verdict, owner = worktree_lookup(find_main_worktree_root(self._root), target)
        except Exception:
            verdict, owner = WORKTREE_QUERY_FAILED, None
        if verdict == WORKTREE_FOUND and owner is not None:
            if self._agent_phases.is_active(owner):
                return None
            if same_checkout(owner, self._root):
                return self._agent_phases.mutation_window()
        elif verdict == WORKTREE_QUERY_FAILED:
            if self._agent_phases.is_active(self._root):
                return None
            return self._agent_phases.mutation_window()
        return nullcontext()

    def _publish_after_tick(self) -> None:
        with _mutation_lease(self._root) as lease_verdict:
            if lease_verdict != "acquired":
                logger.warning(
                    "auto_integrate catch-up publication deferred: {}",
                    _lease_failure(lease_verdict),
                )
                return
            try:
                from ralph.pipeline.auto_integrate_remote_sync import (
                    reconcile_remote_publication,
                )

                target = resolve_integration_target(self._config, self._root)
                if target is None:
                    return
                window = self._publication_window(target)
                if window is None:
                    logger.debug(
                        "auto_integrate catch-up publication deferred: {}",
                        CATCHUP_DEFERRED_AGENT_PHASE,
                    )
                    return
                with window:
                    reconcile_remote_publication(
                        self._config,
                        self._root,
                        target,
                        remote=remote_target_name(self._config),
                        remote_client=self._remote_client,
                        rebase_stop_resolver=self._rebase_stop_resolver,
                    )
            except Exception as tick_exc:
                logger.warning(
                    "auto_integrate catch-up publication tick failed: {}: {}",
                    CATCHUP_UNKNOWN,
                    tick_exc,
                )


def start_catchup_worker_if_enabled(
    config: UnifiedConfig,
    root: Path,
    *,
    interval_seconds: float = DEFAULT_CATCHUP_INTERVAL_SECONDS,
    remote_client: RemoteClient | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> AutoIntegrateCatchupWorker | None:
    enabled_raw: object = getattr(config.general, "auto_integrate_enabled", True)
    if not (isinstance(enabled_raw, bool) and enabled_raw):
        return None
    worker = AutoIntegrateCatchupWorker(
        config,
        root,
        interval_seconds=interval_seconds,
        remote_client=remote_client,
        rebase_stop_resolver=rebase_stop_resolver,
    )
    worker.start()
    return worker


__all__ = [
    "CATCHUP_DEFEDITELY_FAILED",
    "CATCHUP_DEFERRED_AGENT_PHASE",
    "CATCHUP_DIRTY",
    "CATCHUP_DISABLED",
    "CATCHUP_DIVERGED",
    "CATCHUP_FAST_FORWARDED",
    "CATCHUP_HEAD_UNREADABLE",
    "CATCHUP_KILLED",
    "CATCHUP_LADDER_ESCALATED",
    "CATCHUP_LEASE_UNKNOWN",
    "CATCHUP_LEDGER_UNAVAILABLE",
    "CATCHUP_LOCK_HELD",
    "CATCHUP_LOCK_UNKNOWN",
    "CATCHUP_NOT_ON_BRANCH",
    "CATCHUP_NO_TARGET",
    "CATCHUP_ON_TARGET",
    "CATCHUP_REFUSED",
    "CATCHUP_REFUSED_AGENT_ACTIVE",
    "CATCHUP_REMOTE_SKIPPED",
    "CATCHUP_TARGET_UNREADABLE",
    "CATCHUP_TIMEOUT",
    "CATCHUP_UNKNOWN",
    "CATCHUP_UP_TO_DATE",
    "DEFAULT_CATCHUP_INTERVAL_SECONDS",
    "DEFAULT_CATCHUP_STALL_WINDOW_SECONDS",
    "AgentPhaseActivity",
    "AutoIntegrateCatchupWorker",
    "CatchupLedgerFailureBudget",
    "attempt_catchup_fast_forward",
    "process_agent_phases",
    "resolve_integration_target",
    "start_catchup_worker_if_enabled",
]
