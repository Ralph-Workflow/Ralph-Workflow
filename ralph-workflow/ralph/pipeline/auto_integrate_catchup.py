"""Background fast-forward catch-up for auto-integration.

A daemon worker periodically fast-forwards a clean checkout onto the
configured target. Every mutation is gated by the repository-wide agent
lease, charged write-ahead to the durable attempt ledger, and classified
into a named outcome. Repeated attempts against one unchanged ref state
advance the durable ladder instead of issuing the same merge forever.
"""

from __future__ import annotations

import subprocess
import threading
from contextlib import contextmanager, suppress
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
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline.auto_integrate_backoff import compute_retry_delay
from ralph.pipeline.auto_integrate_catchup_coordination import (
    RemoteSyncLease,
    remote_sync_transaction,
)
from ralph.pipeline.auto_integrate_ff import (
    fast_forward_target,
)
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
from ralph.process.manager import ProcessTerminationError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from git import Head

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_ff import RemoteClient
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_actions import Clock

#: Cadence of the background catch-up probe: a handful of in-process ref
#: reads, so a 30-second cadence is negligible.
DEFAULT_CATCHUP_INTERVAL_SECONDS: float = 30.0

#: Tick outcome tags; only ``fast-forwarded`` means the repository changed.
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

#: Named outcomes for the mutation path: an operator must be able to tell an
#: active agent, a timeout, a killed child and a definite refusal apart.
CATCHUP_REFUSED_AGENT_ACTIVE = "refused: agent is currently active"
CATCHUP_LEASE_UNKNOWN = "failed: agent lease state is unknown"
CATCHUP_LEDGER_UNAVAILABLE = "failed: repository attempt ledger is unavailable"
CATCHUP_TIMEOUT = "failed: fast-forward timed out"
CATCHUP_KILLED = "failed: fast-forward was killed"
CATCHUP_UNKNOWN = "failed: fast-forward outcome is unknown"
CATCHUP_DEFEDITELY_FAILED = "failed: fast-forward definitely failed"
CATCHUP_LADDER_ESCALATED = "ladder: fast-forward quarantined after repeated attempts"

#: Named outcomes for the repository-lock observation that precedes a mutation.
CATCHUP_LOCK_HELD = "refused: a git lock in this repository is held by a live process"
CATCHUP_LOCK_UNKNOWN = "failed: the owner of a git lock in this repository is unknown"

#: Outcomes that mean the ticker is idle or healthy, never stalled: there
#: is nothing to integrate, so the tick does not age.
_CATCHUP_SETTLED_OUTCOMES = (
    CATCHUP_DISABLED,
    CATCHUP_NO_TARGET,
    CATCHUP_NOT_ON_BRANCH,
    CATCHUP_ON_TARGET,
    CATCHUP_UP_TO_DATE,
    CATCHUP_DIRTY,
    CATCHUP_REFUSED_AGENT_ACTIVE,
)

#: Elapsed time without integration progress that becomes a visible stall.
DEFAULT_CATCHUP_STALL_WINDOW_SECONDS: float = 300.0

#: Stable action key used in the durable ledger. The state signature
#: carries the observed refs, so this action is counted independently for
#: every distinct checkout state.
_CATCHUP_LEDGER_ACTION = "catchup_fast_forward"

type _LeaseVerdict = Literal["acquired", "held", "unknown"]


@dataclass(frozen=True)
class _CatchupObservation:
    """The ref facts that define one catch-up attempt's durable state."""

    target: str
    current: str
    target_sha: str
    current_sha: str

    @property
    def signature(self) -> str:
        """Return the durable state signature for the observed checkout."""
        return "|".join(
            (
                "catchup",
                f"target={self.target}@{self.target_sha}",
                f"head={self.current}@{self.current_sha}",
            )
        )

    @property
    def summary(self) -> str:
        """Return a compact description of the CURRENT observation."""
        return f"target={self.target}@{self.target_sha}; head={self.current}@{self.current_sha}"


def _current_branch_name(root: Path) -> str | None:
    """Name of the branch HEAD is on, or ``None`` when detached/unreadable."""
    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.active_branch.name
    except Exception:
        return None
    finally:
        _close_repo(repo)


def resolve_integration_target(config: UnifiedConfig, root: Path) -> str | None:
    """Return the configured target only when it is a local branch."""
    configured: object = getattr(config.general, "auto_integrate_target", None)
    if not isinstance(configured, str) or not configured:
        return None
    sha, query_ok = observe_branch_sha(root, configured)
    return configured if query_ok and sha is not None else None


def _local_head(repo: Repo, name: str) -> Head | None:
    """Return the local branch named exactly ``name``, or ``None``."""
    try:
        return next((head for head in repo.heads if head.name == name), None)
    except Exception:
        return None


def observe_branch_sha(root: Path, name: str) -> tuple[str | None, bool]:
    """Read ``refs/heads/<name>``'s tip SHA in-process."""
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
    """Whether ``ancestor_sha`` is an ancestor of ``descendant_sha``."""
    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.is_ancestor(repo.commit(ancestor_sha), repo.commit(descendant_sha))
    except Exception:
        return False
    finally:
        _close_repo(repo)


def _worktree_is_clean(root: Path) -> bool:
    """True when no uncommitted tracked modification is present (fails closed)."""
    repo: Repo | None = None
    try:
        repo = Repo(root)
        # ``repo.git.<cmd>`` is GitPython's dynamic dispatch and returns Any.
        status_out = cast(
            "str", repo.git.status("--porcelain", "--untracked-files=no")
        )  # cast-policy: seam: GitPython dynamic dispatch (repo.git.status returns Any)
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
    """Remove git locks whose owner is proven dead; return why a lock was kept.

    Only affirmative evidence removes a lock: the liveness probe must prove
    that no live process holds it. A lock that is held, or whose holder
    cannot be ruled out, is kept and the mutation is refused with the
    reason: a lock left by a force-killed Ralph child must not block the
    next observation, and a live lock must never be stolen.
    """
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
) -> str:
    """One catch-up probe: fast-forward the checkout onto the target if free.

    ``ledger`` and ``clock`` are injection seams for deterministic tests.
    """
    if not _catchup_enabled(config):
        return CATCHUP_DISABLED
    assert config is not None
    current = _current_branch_name(root)
    if current is None:
        return CATCHUP_NOT_ON_BRANCH
    resolved_target = target if target is not None else resolve_integration_target(config, root)
    if resolved_target is None:
        return CATCHUP_NO_TARGET
    effective_clock = clock if clock is not None else default_clock()
    remote_outcome: str | None = None
    if remote_sync_enabled(config):
        remote_outcome = _synchronize_remote_target(
            config,
            root,
            resolved_target,
            remote=remote,
            remote_client=remote_client,
            ledger=ledger,
            clock=effective_clock,
            jitter=jitter,
        )
        if remote_outcome is not None and remote_outcome != CATCHUP_FAST_FORWARDED:
            return remote_outcome
    if current == resolved_target:
        return (
            CATCHUP_FAST_FORWARDED
            if remote_outcome == CATCHUP_FAST_FORWARDED
            else CATCHUP_ON_TARGET
        )
    return _fast_forward_if_strictly_behind(
        root,
        resolved_target,
        current,
        ledger=ledger,
        clock=effective_clock,
        jitter=jitter,
    )


def _catchup_enabled(config: UnifiedConfig | None) -> bool:
    """Return True when catch-up is enabled by the supplied config (or absent)."""
    if config is None:
        return False
    enabled_raw: object = getattr(config.general, "auto_integrate_enabled", True)
    return isinstance(enabled_raw, bool) and enabled_raw


def _repository_ledger(root: Path) -> AttemptLedger:
    """Return the ledger anchored to the repository-wide Git common directory."""
    repo: Repo | None = None
    try:
        repo = Repo(root, search_parent_directories=True)
        common_dir = Path(repo.common_dir).resolve()
        return AttemptLedger(root, common_dir=common_dir)
    finally:
        _close_repo(repo)


@contextmanager
def _mutation_lease(root: Path) -> Iterator[_LeaseVerdict]:
    """Reserve the repository mutation lease, or report why it is unavailable."""
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
    """Return the named outcome for an unavailable mutation lease."""
    if verdict == "held":
        return CATCHUP_REFUSED_AGENT_ACTIVE
    return CATCHUP_LEASE_UNKNOWN


def _classify_fast_forward_result(result: bool) -> GitOutcomeKind:
    """Adapt the current boolean fast-forward result to the canonical outcome union."""
    return GitOutcomeKind.SUCCEEDED if result else GitOutcomeKind.FAILED


def _classify_git_exception(exc: Exception) -> GitOutcomeKind:
    """Classify the exception shape into the closed GitOutcomeKind union."""
    return classify_git_exception(exc).kind


def _failure_reason(kind: GitOutcomeKind, observation: str) -> str:
    """Render a typed Git outcome as a named, observation-backed reason."""
    reasons: dict[GitOutcomeKind, str] = {
        GitOutcomeKind.SUCCEEDED: CATCHUP_FAST_FORWARDED,
        GitOutcomeKind.FAILED: CATCHUP_DEFEDITELY_FAILED,
        GitOutcomeKind.TIMED_OUT: CATCHUP_TIMEOUT,
        GitOutcomeKind.KILLED: CATCHUP_KILLED,
        GitOutcomeKind.UNKNOWN: CATCHUP_UNKNOWN,
    }
    reason = reasons[kind]
    if kind == GitOutcomeKind.SUCCEEDED:
        return reason
    return f"{reason}; {observation}"


def _backoff_after_failure(
    clock: Clock,
    attempt_count: int,
    *,
    jitter: Callable[[], float] | None,
) -> None:
    """Wait through the injected monotonic clock after an unknown failure."""
    try:
        delay = compute_retry_delay(attempt_count, jitter=jitter)
        clock.sleep(delay)
    except Exception as exc:
        logger.warning(
            "auto_integrate catch-up backoff failed (attempt {}): {}",
            attempt_count,
            exc,
        )


def _charge_attempt(
    root: Path,
    ledger: AttemptLedger | None,
    signature: str,
    observation: str,
) -> tuple[int, str]:
    """Charge one attempt, or return why the same-state action is quarantined."""
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
        return 0, f"{CATCHUP_LEDGER_UNAVAILABLE}; {observation}"
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
) -> str | None:
    """Fetch and advance the target without mutating while an agent is active."""

    chosen_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)
    effective_clock = clock if clock is not None else default_clock()
    with remote_sync_transaction(root, chosen_remote, target) as lease:
        if lease is None:
            return CATCHUP_REMOTE_SKIPPED
        if not lease.fetch_allowed:
            return None
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
) -> str | None:
    """Refresh the remote target and advance it when the fetch proves it ahead."""

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
    )


def _observe_remote_target(
    root: Path,
    remote: str,
    target: str,
    *,
    remote_client: RemoteClient | None,
) -> tuple[str | None, str]:
    """Observe the remote tip and prove that its target worktree is safe."""

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
    if verdict == WORKTREE_FOUND and owner is not None and not _worktree_is_clean(owner):
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
) -> str | None:
    """Advance the target to the observed remote tip under the durable budget."""

    remote_sha, observe_reason = _observe_remote_target(
        root,
        remote,
        target,
        remote_client=remote_client,
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
    )
    if charge_reason:
        return charge_reason
    effective_clock = clock if clock is not None else default_clock()
    try:
        advanced, refusal_reason = fast_forward_target(
            root,
            target,
            remote_sha,
            reclaim_target_worktree=False,
        )
    except Exception as exc:
        kind = _classify_git_exception(exc)
        if kind != GitOutcomeKind.FAILED:
            _backoff_after_failure(effective_clock, attempt_count, jitter=jitter)
        return _failure_reason(kind, observation)
    if advanced:
        return CATCHUP_FAST_FORWARDED
    # A False result always carries a definite refusal cause, so the cause
    # is appended to the named outcome.
    return f"{CATCHUP_DEFEDITELY_FAILED}; {refusal_reason}"


def observe_remote_target_sha(root: Path, remote: str, target: str) -> str | None:
    """Return the configured remote target SHA when its ref is readable."""

    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.commit(f"refs/remotes/{remote}/{target}").hexsha
    except Exception:
        return None
    finally:
        _close_repo(repo)


def _mutation_refusal(root: Path, target: str, current: str) -> str:
    """Return why the checkout must not be mutated, or ``""`` when it is safe."""

    if not _worktree_is_clean(root):
        return CATCHUP_DIRTY
    if not _still_safe_to_merge(root, current):
        return CATCHUP_REFUSED
    if (root / ".git").exists():
        action = decide_seam_action(root, target=target)
        if action in (
            IntegrationAction.REQUIRE_INTERVENTION,
            IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        ):
            return f"{CATCHUP_REFUSED}; owner required {action.value}"
    return ""


def _fast_forward_if_strictly_behind(
    root: Path,
    target: str,
    current: str,
    *,
    ledger: AttemptLedger | None = None,
    clock: Clock | None = None,
    jitter: Callable[[], float] | None = None,
) -> str:
    """SHA-level half of the tick: land only a genuine fast-forward."""

    observation, skip_reason = _observe_strictly_behind(root, target, current)
    if observation is None:
        return skip_reason
    effective_clock = clock if clock is not None else default_clock()
    with _mutation_lease(root) as lease_verdict:
        if lease_verdict != "acquired":
            logger.warning(
                "auto_integrate catch-up tick deferred: {}; {}",
                _lease_failure(lease_verdict),
                observation.summary,
            )
            return _lease_failure(lease_verdict)
        refusal = _mutation_refusal(root, target, current)
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
    """Execute the worktree fast-forward and classify the outcome."""
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
    """Re-check the attached branch and absence of a rebase before merging."""
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
    """Observe both tips; return the state and a skip reason."""

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
    return (
        _CatchupObservation(
            target=target,
            current=current,
            target_sha=target_sha,
            current_sha=current_sha,
        ),
        "",
    )


@dataclass(frozen=True)
class _CatchupStallState:
    """The visible stall a catch-up ticker reports when it stops progressing."""

    cause: str
    elapsed_seconds: float
    window_seconds: float


class AutoIntegrateCatchupWorker:
    """Bounded daemon-threaded catch-up loop over the catch-up tick."""

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
        self._clock = clock
        self._effective_clock = clock if clock is not None else default_clock()
        self._jitter = jitter
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
        )

    def start(self) -> None:
        """Spawn the daemon worker thread. Idempotent: a second call is a no-op."""
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
        """Signal the worker to exit at its next wakeup. Idempotent.

        Does NOT ``join()``: the worker is daemonic and a join could block
        process shutdown behind a git subprocess.
        """
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stall_state(self) -> _CatchupStallState | None:
        """Return the current stall, or ``None`` while the ticker progresses."""
        return self._stall_state

    def _record_progress(self, outcome: str) -> None:
        """Age the no-progress clock and publish an accurate stall state."""
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
        """Run cadence ticks until the injected wait reports a stop."""
        # ``Event.wait`` doubles as the interval sleep AND the stop signal,
        # so the first tick fires one full interval AFTER start().
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
        # Post-tick publication: the reconciler runs on every tick that is
        # not a quiet skip, so a prior remote failure is retried from the
        # current refs once the remote recovers.
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
            with _mutation_lease(self._root) as lease_verdict:
                if lease_verdict != "acquired":
                    logger.warning(
                        "auto_integrate catch-up publication deferred: {}",
                        _lease_failure(lease_verdict),
                    )
                else:
                    try:
                        from ralph.pipeline.auto_integrate_remote_sync import (
                            reconcile_remote_publication,
                        )

                        target = resolve_integration_target(self._config, self._root)
                        if target is not None:
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
        # Ordinary skips stay quiet. A refusal, escalation, or failed
        # external call is NOT a quiet skip: it names the current reason.
        if outcome == CATCHUP_FAST_FORWARDED:
            logger.info("auto_integrate catch-up: fast-forwarded the checkout onto the target")
        elif outcome == CATCHUP_REFUSED_AGENT_ACTIVE:
            logger.warning("auto_integrate catch-up tick deferred: {}", outcome)
        elif outcome.startswith(("failed:", "ladder:")) or outcome == CATCHUP_REFUSED:
            logger.warning("auto_integrate catch-up tick failed: {}", outcome)


def start_catchup_worker_if_enabled(
    config: UnifiedConfig,
    root: Path,
    *,
    interval_seconds: float = DEFAULT_CATCHUP_INTERVAL_SECONDS,
    remote_client: RemoteClient | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> AutoIntegrateCatchupWorker | None:
    """Construct and start the catch-up worker when auto-integration is on.

    Returns ``None`` (and spawns nothing) when
    ``config.general.auto_integrate_enabled`` is not a true boolean, so a
    disabled run never pays the background git probes.
    """
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
    "AutoIntegrateCatchupWorker",
    "attempt_catchup_fast_forward",
    "resolve_integration_target",
    "start_catchup_worker_if_enabled",
]
