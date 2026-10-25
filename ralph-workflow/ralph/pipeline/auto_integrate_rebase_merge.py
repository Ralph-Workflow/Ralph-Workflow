"""Rebase-then-endpoint-merge engine driver for auto-integration.

This module owns one integration phase: replay the feature, resolve a
real conflict in place, and only then fall back to one endpoint merge.
The caller owns the durable record, landing, and retry loop.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import MERGE_STATE_NONE, merge_state
from ralph.git.merge_obstructions import clear_untracked_merge_obstructions
from ralph.git.rebase._rebase_kind import RebaseKind
from ralph.git.rebase.rebase import (
    RebaseConflicts,
    RebaseFailed,
    RebaseNoOp,
    RebaseSuccess,
    rebase_in_progress,
    rebase_onto,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_outcome import (
    record_conflict,
    record_rebase_outcome,
    record_skip,
)
from ralph.pipeline.auto_integrate_record import clear_record, read_record, set_resolving_rebase
from ralph.pipeline.auto_integrate_recovery import (
    TerminalStateViolationError,
    post_attempt_verify,
)
from ralph.pipeline.auto_integrate_resolve import (
    RESOLUTION_FAILED,
    endpoint_merge_with_resolution,
)
from ralph.pipeline.conflict_resolution import resolve_rebase_in_progress
from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress
from ralph.pipeline.conflict_resolution.attempt_fault import (
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.status import conflict_status_bar_session
from ralph.pipeline.rebase_state import RebaseState, ResolutionFailureCause, StartupRetentionReason

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.git.merge import MergeResult
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_actions import (
        AgentRunner,
        GitOps,
    )
    from ralph.pipeline.rebase_state import RebaseState

# ---------------------------------------------------------------------------
# THE CLOSED REGISTRY OF REBASE-SKIP REASONS.
#
# Automatic rebase is ALWAYS the first attempt of an integration. The
# five slugs below are the ONLY ways an attempt may go straight to the
# endpoint merge, and each one names the CURRENT observation it is
# derived from -- never a policy constant, never a bare "skip".
#
# The registry is closed on purpose. A sixth reason is a product
# decision, not an implementation detail: adding one requires
# registering it here (slug + observation + description) AND updating
# the drift guard in ``tests/test_auto_integrate_skip_reasons.py``,
# which fails when a reason is used without being registered.
# ---------------------------------------------------------------------------

#: Slug: a merge commit sits in the replay range (B3).
SKIP_MERGE_COMMITS_IN_RANGE = "merge_commits_in_range"
#: Slug: a root commit sits in the replay range (B7).
SKIP_ROOT_COMMITS_IN_RANGE = "root_commits_in_range"
#: Slug: the replay has nothing to replay (B4/B6).
SKIP_ALL_EMPTY_REPLAY = "all_empty_replay"
#: Slug: two distinct replay paths collide under case folding.
SKIP_CASE_FOLDING_COLLISION = "case_folding_collision"
#: Slug: the previous attempt of this seam produced a merge commit.
SKIP_RETRY_AFTER_MERGE_COMMIT = "retry_after_merge_commit"

#: Operator-facing description of the merge-commits skip.
#:
#: B3 -- merge commits in the replay range are flattened and dropped
#: by a plain ``git rebase``; routing to the endpoint merge
#: preserves them. ``rebase --rebase-merges`` exists, but its
#: side-effects on history topology and the documented edge cases
#: make it unsuitable for the always-on auto-integration path.
_REASON_MERGE_COMMITS = "branch contains merge commits; routing to endpoint merge"
#: Operator-facing description of the root-commits skip.
#:
#: B7 -- root commits have no parent to rebase onto. ``rebase
#: --root`` exists, but the spec scopes it out of automation
#: (B7) because of the operator-clarity cost of replaying
#: history.
_REASON_ROOT_COMMITS = "branch contains root commits; routing to endpoint merge"
#: Operator-facing description of the all-empty-replay skip.
#:
#: B4/B6 -- an all-empty replay (every commit became empty on
#: replay because the change is already upstream, or the replay
#: range is empty altogether) is the case the rebase ``--empty=drop``
#: flag was supposed to absorb. Older gits that ignore the flag
#: abandon to endpoint merge anyway; the routing here short-circuits
#: the loop and still lands the branch via the merge fallback.
_REASON_ALL_EMPTY = "branch has no replayable commits; routing to endpoint merge"
#: Operator-facing description of the case-folding-collision skip.
_REASON_CASE_COLLISION = "branch range has case-folding path collision; routing to endpoint merge"

#: ``RebaseNoOp`` reason recorded when the bounded retry deliberately
#: skips ``rebase_onto`` to preserve the previous attempt's merge commit.
REBASE_SKIPPED_FOR_MERGE = "rebase skipped on retry to preserve the integration merge commit"

#: The closed registry: slug -> (observation that justifies the skip,
#: operator-facing description). Every entry names the observation the
#: skip is derived from, so an operator can re-derive it from the same
#: repository state the attempt saw.
SKIP_REASON_REGISTRY: dict[str, tuple[str, str]] = {
    SKIP_MERGE_COMMITS_IN_RANGE: (
        "git rev-list --merges <target>..HEAD is non-empty",
        _REASON_MERGE_COMMITS,
    ),
    SKIP_ROOT_COMMITS_IN_RANGE: (
        "git rev-list --max-parents=0 <target>..HEAD is non-empty",
        _REASON_ROOT_COMMITS,
    ),
    SKIP_ALL_EMPTY_REPLAY: (
        "the replay range is empty, or every replayed commit is already upstream",
        _REASON_ALL_EMPTY,
    ),
    SKIP_CASE_FOLDING_COLLISION: (
        "two distinct replay paths fold to the same case-insensitive name",
        _REASON_CASE_COLLISION,
    ),
    SKIP_RETRY_AFTER_MERGE_COMMIT: (
        "the immediately preceding attempt of this seam produced a merge commit",
        REBASE_SKIPPED_FOR_MERGE,
    ),
}

__all__ = [
    "REBASE_SKIPPED_FOR_MERGE",
    "SKIP_ALL_EMPTY_REPLAY",
    "SKIP_CASE_FOLDING_COLLISION",
    "SKIP_MERGE_COMMITS_IN_RANGE",
    "SKIP_REASON_REGISTRY",
    "SKIP_RETRY_AFTER_MERGE_COMMIT",
    "SKIP_ROOT_COMMITS_IN_RANGE",
    "RebaseRunResult",
    "is_registered_skip_reason",
    "registered_skip_slugs",
    "run_rebase_or_merge",
    "skip_reason_description",
    "skip_reason_observation",
    "skip_reason_slug",
    "skip_reason_text",
    "with_skip_reason",
]


def registered_skip_slugs() -> frozenset[str]:
    """Return the closed set of registered auto-rebase skip reason slugs."""
    return frozenset(SKIP_REASON_REGISTRY)


def skip_reason_observation(slug: str) -> str:
    """Return the observation that justifies a registered skip reason."""
    return _registered_spec(slug)[0]


def skip_reason_description(slug: str) -> str:
    """Return the operator-facing description of a registered skip reason."""
    return _registered_spec(slug)[1]


def skip_reason_text(slug: str) -> str:
    """Return the recorded reason text of a registered skip reason.

    The text is ``"<slug>: <description>"`` so the slug survives into
    the durable record: a reader (and the drift guard) can tell WHICH
    registered reason applied without parsing prose.
    """
    return f"{slug}: {skip_reason_description(slug)}"


def skip_reason_slug(text: str) -> str | None:
    """Return the registered slug a recorded reason text names, if any.

    The slug may sit beside other recorded facts (the publication
    annotation leads the text, for example), so every ``"; "``-separated
    part is inspected.
    """
    for part in text.split("; "):
        head, separator, _tail = part.partition(": ")
        if separator and head in SKIP_REASON_REGISTRY:
            return head
    return None


def is_registered_skip_reason(text: str) -> bool:
    """Whether a recorded reason text names a registered skip reason."""
    return skip_reason_slug(text) is not None


def with_skip_reason(record: RebaseState, skip_reason: str | None) -> RebaseState:
    """Return ``record`` carrying the named rebase-skip reason.

    The skip reason is APPENDED to the reason the attempt already
    recorded, so the headline the operator must act on stays first and
    the reason the rebase was skipped stays visible next to it. The
    append is idempotent: a reason already present is not repeated.
    """
    if skip_reason is None:
        return record
    existing = record.last_reason or ""
    if skip_reason in existing:
        return record
    combined = f"{existing}; {skip_reason}" if existing else skip_reason
    return record.model_copy(update={"last_reason": combined})


def _registered_spec(slug: str) -> tuple[str, str]:
    """Return the registry entry for ``slug`` or fail loudly.

    A slug that is not registered is a DRIFT bug: the closed set is the
    product's contract, so an unregistered reason must never reach the
    operator log or the durable record.
    """
    spec = SKIP_REASON_REGISTRY.get(slug)
    if spec is None:
        raise ValueError(
            f"unregistered auto-rebase skip reason: {slug!r}; "
            f"the closed set is {sorted(SKIP_REASON_REGISTRY)}"
        )
    return spec


@dataclass(frozen=True)
class RebaseRunResult:
    """Outcome of :func:`run_rebase_or_merge`.

    ``short_circuit`` carries the state to return when the attempt stopped
    early; otherwise the engine and merge outcomes describe the path taken.
    ``skip_reason`` names the registered reason when the rebase was skipped.
    """

    rebase_outcome: RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed
    merge_attempted: bool
    merge_outcome: MergeResult | None
    short_circuit: RebaseState | None
    skip_reason: str | None = None


_ENVIRONMENTAL_REBASE_KINDS = frozenset(
    {
        RebaseKind.CONCURRENT_OPERATION,
        RebaseKind.ENVIRONMENT_FAILURE,
        RebaseKind.REFERENCE_UPDATE_FAILED,
        RebaseKind.REPOSITORY_CORRUPT,
    }
)
_ENVIRONMENTAL_IO_MARKERS = (
    "disk full",
    "filesystem",
    "i/o error",
    "io error",
    "no space left",
    "permission denied",
    "read-only",
    "too many open files",
    "worktree directory was deleted",
)


def _rebase_failure_detail(failure: RebaseFailed) -> str:
    """Return the current observation carried by a classified rebase failure."""
    metadata = failure.kind.metadata
    for key in ("reason", "details", "operation"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return failure.kind.kind.value


def _environmental_rebase_reason(failure: RebaseFailed) -> str | None:
    """Return an environmental reason, or ``None`` for another failed rebase."""
    kind = failure.kind.kind
    detail = _rebase_failure_detail(failure)
    if kind in _ENVIRONMENTAL_REBASE_KINDS:
        category = kind.value
    elif kind is RebaseKind.UNKNOWN:
        lowered = detail.lower()
        if "timed out" in lowered or "timeout" in lowered:
            category = "timeout"
        elif "killed" in lowered or "sigkill" in lowered:
            category = "killed"
        elif "lock" in lowered or "concurrent" in lowered:
            category = "concurrent_operation"
        elif any(marker in lowered for marker in _ENVIRONMENTAL_IO_MARKERS):
            category = "io_error"
        else:
            return None
    else:
        return None
    return f"rebase failed for an environmental reason ({category}): {detail}"


def _failed_rebase_reason(failure: RebaseFailed) -> str:
    """Return the operator-visible reason for any non-conflict rebase failure."""
    environmental = _environmental_rebase_reason(failure)
    if environmental is not None:
        return environmental
    return f"rebase failed ({failure.kind.kind.value}): {_rebase_failure_detail(failure)}"


def _failed_rebase_state(failure: RebaseFailed, target: str) -> RebaseState:
    """Record a failed rebase without presenting it as a content conflict."""
    return record_skip(
        reason=_failed_rebase_reason(failure),
        target=target,
        failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
    )


def _environmental_rebase_result(
    root: Path,
    target: str,
    failure: RebaseFailed,
    reason: str,
    git_ops: GitOps | None,
) -> RebaseRunResult:
    """Stop after an environmental rebase failure without attempting a merge."""
    _abort_rebase_after_conflict(root, git_ops=git_ops)
    state = record_skip(
        reason=reason,
        target=target,
        failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
    )
    if rebase_in_progress(root):
        post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
        state = state.set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT)
    else:
        _verify_terminal_probe(root, expected_head_sha=None, owns_resolution=False)
    return RebaseRunResult(
        rebase_outcome=failure,
        merge_attempted=False,
        merge_outcome=None,
        short_circuit=state,
    )


def run_rebase_or_merge(
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    prefer_merge: bool = False,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    conflict_resolution_config: ConflictResolutionConfig | None = None,
    git_ops: GitOps | None = None,
    agent_runner: AgentRunner | None = None,
) -> RebaseRunResult:
    """Drive rebase_onto, resolving conflicts in place before any fallback.

    On success returns a ``RebaseRunResult`` with ``short_circuit``
    ``None`` and the ``rebase_outcome`` / ``merge_outcome`` the
    caller uses to build the final :class:`RebaseState`. A rebase that
    FAILS, and a conflicted rebase whose in-place resolution does not
    land, both fall back to the endpoint merge —
    a rebase that fails for any reason must never end the integration
    attempt while a single three-way merge could still land it. When
    aborting a conflicted rebase leaves it in progress, the durable crash
    record is retained so startup recovery can restore the repository.

    The automatic rebase is ALWAYS the first attempt. The ONLY ways
    this function skips it are the five registered reasons in
    :data:`SKIP_REASON_REGISTRY`, and each one is derived from the
    CURRENT observation of the repository:

    * ``prefer_merge`` -- the immediately preceding attempt of this
      seam produced a merge commit. A plain ``git rebase`` (no
      ``--rebase-merges``) would drop that merge and replay the raw
      feature commits back into the conflict the resolver just
      settled. Registered as
      :data:`SKIP_RETRY_AFTER_MERGE_COMMIT`.
    * the pre-flight range routing below -- B3 merge commits, B7 root
      commits, B4/B6 all-empty replays, and a case-folding path
      collision, each named by its own registered slug.

    Every skip records its registered reason text on the returned
    :class:`RebaseRunResult` so the caller can put it in the durable
    state; no skip is silent and no skip is unnamed.

    Before ``rebase_onto`` is called the function also detects ranges
    a plain rebase would mishandle (B3 merge commits, B7 root commits,
    B4/B6 all-empty replays) and routes them straight to the endpoint
    merge so the rebase engine never silently flattens a merge
    resolution, replays an already-upstream patch into a conflict, or
    drops a root commit. The rebase ``--empty=drop`` flag from step 2
    still lands the all-empty case on modern git; this routing is the
    backstop for older git that ignores the flag.

    With a ``rebase_stop_resolver`` a CONFLICTED rebase is resolved in
    place -- commit by commit, through ``git rebase --continue`` -- before
    the abort-and-endpoint-merge fallback is considered at all.
    ``display`` is used only to own the resolution footer for the whole
    loop; it is optional and never required for correctness.

    ``git_ops`` and ``agent_runner`` are the injected MUTATION seams:
    the rebase, its abort and the resolver dispatch run through them,
    so a fault-injecting double can drive the whole attempt without a
    repository. The routing decisions above stay here -- they are
    decisions, not mutations.
    """
    if prefer_merge:
        retry_reason = skip_reason_text(SKIP_RETRY_AFTER_MERGE_COMMIT)
        return replace(
            _endpoint_merge_result(
                root,
                target,
                RebaseNoOp(retry_reason),
                conflict_resolver,
            ),
            skip_reason=retry_reason,
        )
    # Pre-flight routing: detect ranges a plain ``rebase --onto``
    # would silently mishandle and skip the rebase phase entirely.
    # The rebase result is replaced with a ``RebaseNoOp`` carrying
    # the routing reason so the operator log line tells the operator
    # WHY the merge was chosen; the headline ``merged`` comes from
    # the endpoint merge that follows. The same registered reason text
    # rides back on the result so the durable state records it too.
    routing_reason = _range_routing_probe(root, target)
    if routing_reason is not None:
        logger.info("auto_integrate: {} (target '{}')", routing_reason, target)
        return replace(
            _endpoint_merge_result(
                root,
                target,
                RebaseNoOp(routing_reason),
                conflict_resolver,
            ),
            skip_reason=routing_reason,
        )
    # Untracked leftovers identical to the target would make the rebase
    # refuse its checkout; clear them so the rebase is actually tried.
    clear_untracked_merge_obstructions(root, target)
    rebase_outcome = (
        git_ops.rebase_onto(root, target)
        if git_ops is not None
        else rebase_onto(target, repo_root=root)
    )
    if isinstance(rebase_outcome, RebaseFailed):
        environmental_reason = _environmental_rebase_reason(rebase_outcome)
        if environmental_reason is not None:
            return _environmental_rebase_result(
                root,
                target,
                rebase_outcome,
                environmental_reason,
                git_ops,
            )
    if not isinstance(rebase_outcome, (RebaseConflicts, RebaseFailed)):
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=None,
        )

    if isinstance(rebase_outcome, RebaseConflicts) and rebase_stop_resolver is not None:
        resolved = _resolve_conflicted_rebase(
            root,
            target,
            rebase_stop_resolver,
            display,
            conflict_resolution_config,
            agent_runner,
        )
        if resolved is not None:
            return resolved
        # The endpoint merge is a different integration shape (one merge
        # of the whole branch, not a commit-by-commit replay), and it is
        # the last way to land the target.

    return _fallback_to_endpoint_merge(root, target, rebase_outcome, conflict_resolver, git_ops)


def range_routing_reason(root: Path, target: str) -> str | None:
    """Decide whether the feature range must skip ``rebase_onto``.

    Returns the routing reason text (``"<slug>: <description>"``) the
    operator log line should carry, or ``None`` to run the rebase. The
    check is index-/ref-queries only; no git mutation, so a routing
    decision that turns out wrong is reversible by retrying with
    ``prefer_merge=False`` next seam.

    Each predicate is a cheap ``rev-list`` over ``<target>..HEAD``:

    * **B4/B6** — an all-empty replay. Two shapes reach it: the replay
      range is EMPTY (the branch holds no commit the rebase could
      replay, so the rebase would be a no-op that cannot move the
      branch onto the target), or every commit becomes empty on replay
      because the change is already upstream. Both are the case the
      rebase ``--empty=drop`` flag was supposed to absorb (B4).
      Modern git honors the flag; older git abandons. Detection:
      ``rev-list --count <target>..HEAD`` is the total number of
      feature commits; ``rev-list --cherry-pick --no-merges
      <target>..HEAD`` is the number of commits the replay would
      actually need to apply. When the range is empty, or the
      cherry-picked range is empty while the range is not, the rebase
      would walk through zero meaningful stops and the merge is the
      only path that moves the ref. The endpoint merge is the B4/B6
      backstop.
    * **B3** — ``rev-list --merges``: nonzero means a merge commit
      sits in the range; a plain ``rebase`` would drop it. Route to
      endpoint merge so the resolution the agent was paid to produce
      survives the integration.
    * **B7** — ``rev-list --max-parents=0``: nonzero means a root
      commit sits in the range; ``rebase --root`` is out of scope
      (B7). Route to endpoint merge.
    """
    # Total feature commits in the range. ``None`` (the topology query
    # failed) falls through so the rebase engine's own classification
    # is the final answer — never a routing guess based on an
    # unanswerable query. A count of ZERO is a REAL observation, not an
    # unanswerable one: the branch holds nothing the rebase could
    # replay, so the rebase would be a no-op that leaves the branch
    # behind the target. The endpoint merge fast-forwards it, which is
    # the only operation that can move that ref.
    total = _rev_list_count(root, target, extra_args=())
    if total is None:
        return None
    if total == 0:
        return skip_reason_text(SKIP_ALL_EMPTY_REPLAY)
    return _range_topology_reason(root, target, total)


def _range_topology_reason(root: Path, target: str, total: int) -> str | None:
    """Return the registered reason the range topology forces, if any.

    ``total`` is the commit count already proven by the caller, so the
    empty-replay probe does not re-query it.
    """
    merges = _rev_list_count(root, target, extra_args=("--merges",))
    if merges is not None and merges > 0:
        return skip_reason_text(SKIP_MERGE_COMMITS_IN_RANGE)
    roots = _rev_list_count(root, target, extra_args=("--max-parents=0",))
    if roots is not None and roots > 0:
        return skip_reason_text(SKIP_ROOT_COMMITS_IN_RANGE)
    if _all_empty_replay(root, target, total):
        return skip_reason_text(SKIP_ALL_EMPTY_REPLAY)
    if _range_has_case_collision(root, target):
        return skip_reason_text(SKIP_CASE_FOLDING_COLLISION)
    return None


_DEFAULT_RANGE_ROUTING = range_routing_reason
_range_routing_reason = range_routing_reason


def _range_routing_probe(root: Path, target: str) -> str | None:
    fn = (
        range_routing_reason
        if range_routing_reason is not _DEFAULT_RANGE_ROUTING
        else (
            _range_routing_reason
            if _range_routing_reason is not _DEFAULT_RANGE_ROUTING
            else _DEFAULT_RANGE_ROUTING
        )
    )
    return fn(root, target)


def _range_has_case_collision(root: Path, target: str) -> bool:
    """True only for distinct replay paths that collide on case-folding filesystems."""
    result = run_git(
        ("diff", "--name-only", "-z", f"{target}..HEAD"),
        cwd=root,
        label="auto-integrate:case-collision-paths",
    )
    if result.returncode != 0:
        return False
    seen: set[str] = set()
    for path in (item for item in result.stdout.split("\0") if item):
        folded = path.casefold()
        if folded in seen:
            return True
        seen.add(folded)
    return False


def _rev_list_count(root: Path, target: str, *, extra_args: tuple[str, ...]) -> int | None:
    """Run ``git rev-list <args> <target>..HEAD`` and return the count.

    Returns ``None`` when the query failed (non-zero exit or
    malformed output) so the caller can treat the topology as
    UNKNOWN rather than the literal "zero commits" case the old
    implementation returned. Topology queries are guarded by the
    universal per-call timeout and the non-interactive env baseline
    (D1/D15), so a non-zero exit is genuine: a deleted target ref
    (E4), a corrupt object store (H5), or a sibling agent holding a
    lock (A10/E9). Failing closed by treating those as
    "unanswerable" routes through the rebase engine's own
    classification rather than silently merging when the rebase
    would have been the correct path -- the exact bug class that
    would silently flatten a merge commit (B3) or replay an
    already-upstream patch into a conflict (B6).

    The revision range ``<target>..HEAD`` MUST come BEFORE any
    ``--`` separator. ``--`` tells ``git rev-list`` that the
    following arguments are paths to limit the walk to, and a
    ``<ref>..<ref>`` token placed after ``--`` is interpreted as a
    filesystem path; git then exits 129 with usage text and the
    pipeline logs the topology as unknown. There are no paths to
    limit to here, so no ``--`` separator is needed at all.
    """
    result = run_git(
        ("rev-list", "--count", *extra_args, f"{target}..HEAD"),
        cwd=root,
        label="auto-integrate:rev-list-count",
    )
    if result.returncode != 0:
        logger.warning(
            "auto_integrate: rev-list {} for {}..HEAD exited {}: {}; "
            "treating topology as unknown (will run the rebase)",
            extra_args,
            target,
            result.returncode,
            (result.stderr or result.stdout).strip()[:200],
        )
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        logger.warning(
            "auto_integrate: rev-list {} for {}..HEAD returned non-integer "
            "stdout {!r}; treating topology as unknown",
            extra_args,
            target,
            result.stdout.strip()[:200],
        )
        return None


def _all_empty_replay(root: Path, target: str, feature_count: int) -> bool:
    """True when every feature commit is already on ``target`` upstream.

    The cherry-pick walker in ``rev-list`` drops commits whose
    patch-id matches one already in the upstream history (B6). A
    non-empty feature range with an EMPTY cherry-picked range is
    therefore the B4 all-empty-replay case: every commit was
    already applied, so the rebase will walk through zero
    meaningful stops regardless of ``--empty=drop``. The endpoint
    merge is the only path that lands the branch.

    ``feature_count`` is the total commit count already proven by
    the caller; a separate re-query is redundant and would
    re-introduce the topology-error hole the ``_rev_list_count``
    return-type change closed. ``None`` from the cherry-pick walker
    means the topology query failed: routing on an unanswerable
    question is exactly the bug this whole change is about, so the
    walker returning ``None`` is treated as "not all-empty" and
    the rebase engine takes over.
    """
    if feature_count == 0:
        return False
    cherry_count = _rev_list_count(root, target, extra_args=("--cherry-pick", "--no-merges"))
    if cherry_count is None:
        return False
    return cherry_count == 0


def _resolve_conflicted_rebase(
    root: Path,
    target: str,
    rebase_stop_resolver: RebaseStopResolver,
    display: ParallelDisplay | None,
    conflict_resolution_config: ConflictResolutionConfig | None,
    agent_runner: AgentRunner | None = None,
) -> RebaseRunResult | None:
    """Resolve the paused rebase and retain unfinished agent work for continuation.

    Persist resolution ownership before launching an agent. A completed replay
    returns the clean rebase result; a verified blocked continuation or partial
    agent result returns a retained record so no endpoint fallback discards it.
    If ownership cannot be recorded before the agent starts, return control to
    the existing fallback without launching an unowned resolution session.
    """
    if not set_resolving_rebase(root, True):
        logger.warning(
            "auto_integrate: could not record the in-flight rebase resolution "
            "for '{}'; falling back to the endpoint merge",
            target,
        )
        return None
    with conflict_status_bar_session(display, root):
        resolved, exhaustion_reason = _resolve_rebase_with_config(
            root,
            target,
            rebase_stop_resolver,
            conflict_resolution_config,
            agent_runner,
        )
    if resolved and not set_resolving_rebase(root, False):
        logger.warning(
            "auto_integrate: could not clear completed rebase resolution flag for '{}'", target
        )
    record = read_record(root)
    if record is not None and record.rebase_continue_pending:
        return RebaseRunResult(
            rebase_outcome=RebaseConflicts(files=[]),
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=record_conflict(
                reason=record.rebase_continue_error or "verified rebase continuation pending",
                target=target,
                failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
            ).set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT),
        )
    if not resolved:
        return RebaseRunResult(
            rebase_outcome=RebaseConflicts(files=[]),
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=record_conflict(
                reason=exhaustion_reason
                or "rebase resolution incomplete; progress retained for next agent",
                target=target,
                failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
            ).set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT),
        )
    logger.info("auto_integrate: resolved the conflicted rebase onto '{}'", target)
    return RebaseRunResult(
        rebase_outcome=RebaseSuccess(),
        merge_attempted=False,
        merge_outcome=None,
        short_circuit=None,
    )


def _resolve_rebase_with_config(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    config: ConflictResolutionConfig | None,
    agent_runner: AgentRunner | None = None,
) -> tuple[bool, str | None]:
    """Return rebase completion and terminal resolver-chain evidence."""
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

    session = (
        ResolutionSession()
        if config is None
        else ResolutionSession(
            inactivity_timeout_seconds=config.inactivity_timeout_seconds,
            max_rounds_per_stop=config.max_rounds_per_stop,
            max_rebase_conflict_stops=config.max_rebase_conflict_stops,
            max_fallback_agents=config.max_fallback_agents,
            total_resolution_cap_seconds=config.total_resolution_cap_seconds,
        )
    )
    resolved = (
        agent_runner.resolve_rebase(root, target, resolver, session=session)
        if agent_runner is not None
        else resolve_rebase_in_progress(root, target, resolver, session=session)
    )
    if resolved:
        return True, None
    if (
        not session.charge_conflict_budget
        or session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
    ):
        # Something OTHER than the resolver failed: Ralph's own plumbing
        # (a dead tool surface, a transport loop, an unanswered relay), a
        # launch that never happened, a name the registry could not
        # produce, or an operator's wall-clock cap. Recording any of
        # those as an exhausted chain wrote durable terminal evidence
        # that the AGENTS had given up -- and the next seam then refused
        # to try, for a conflict none of them ever got to attempt.
        logger.warning(
            "auto_integrate: rebase conflict resolution for '{}' ended on {} "
            "without the resolver chain being spent; not recording it as exhausted",
            target,
            session.terminal_reason.value if session.terminal_reason else "no terminal evidence",
        )
        return False, None
    return False, session.exhaustion_reason


def _fallback_to_endpoint_merge(
    root: Path,
    target: str,
    rebase_outcome: RebaseConflicts | RebaseFailed,
    conflict_resolver: ConflictResolver | None,
    git_ops: GitOps | None = None,
) -> RebaseRunResult:
    """Abort the unfinished rebase and attempt the endpoint merge (AC-06/AC-07).

    With a ``conflict_resolver``, a conflicted endpoint merge is
    handed to the resolver and — on full resolution — committed
    deterministically, so the integration continues to the
    fast-forward phase instead of giving up (see
    :mod:`ralph.pipeline.auto_integrate_resolve`).

    R6/AC-06: runs :func:`post_attempt_verify` BEFORE the endpoint
    merge so a rebase abort that left in-progress markers is
    surfaced loudly before the merge starts. The merge itself
    runs through the same invariant via :func:`_endpoint_merge_result`
    on its conflict / resolution paths. The ``owns_resolution``
    flag is ``True`` when the conflict resolver is mid-edit.
    """
    _abort_rebase_after_conflict(root, git_ops=git_ops)
    if rebase_in_progress(root):
        # Keep the pre-mutation record: abort_rebase can fail after the
        # rebase engine has created state, and recovery needs that record
        # to prove ownership and retry the abort/reset on the next run.
        post_attempt_verify(
            root, expected_head_sha=None, owns_resolution=conflict_resolver is not None
        )
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=(
                _failed_rebase_state(rebase_outcome, target)
                if isinstance(rebase_outcome, RebaseFailed)
                else record_conflict(
                    reason="rebase in-progress after abort",
                    target=target,
                    failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
                )
            ),
        )
    _verify_terminal_probe(
        root, expected_head_sha=None, owns_resolution=conflict_resolver is not None
    )

    return _endpoint_merge_result(root, target, rebase_outcome, conflict_resolver)


def verify_terminal_state(
    root: Path,
    *,
    expected_head_sha: str | None,
    owns_resolution: bool,
) -> None:
    """Run :func:`post_attempt_verify` on a merge / fallback exit path.

    Catches :class:`TerminalStateViolationError` and logs a loud
    ``ERROR`` -- the caller continues to record its outcome (a
    merge / fallback can legitimately finish with a feature that
    already moved, so the only invariant we can check here is the
    absence of stale in-progress markers). Extracted into its
    own helper so the three call sites in this module share one
    diagnostic shape.
    """
    try:
        post_attempt_verify(
            root,
            expected_head_sha=expected_head_sha,
            owns_resolution=owns_resolution,
        )
    except TerminalStateViolationError as exc:
        logger.error(
            "auto_integrate_rebase_merge: terminal-state invariant "
            "violation: {}; the recovery preamble will reclaim this on "
            "the next run, and the durable record is RETAINED so the "
            "next recovery can still restore the pre-attempt state. "
            "The operator should be aware the merge / fallback path "
            "leaked state.",
            exc,
        )


_DEFAULT_VERIFY_TERMINAL = verify_terminal_state
_verify_terminal_state = verify_terminal_state


def _verify_terminal_probe(
    root: Path,
    *,
    expected_head_sha: str | None,
    owns_resolution: bool,
) -> None:
    fn = (
        verify_terminal_state
        if verify_terminal_state is not _DEFAULT_VERIFY_TERMINAL
        else (
            _verify_terminal_state
            if _verify_terminal_state is not _DEFAULT_VERIFY_TERMINAL
            else _DEFAULT_VERIFY_TERMINAL
        )
    )
    fn(root, expected_head_sha=expected_head_sha, owns_resolution=owns_resolution)


def _endpoint_merge_result(
    root: Path,
    target: str,
    rebase_outcome: RebaseConflicts | RebaseFailed | RebaseNoOp,
    conflict_resolver: ConflictResolver | None,
) -> RebaseRunResult:
    """Run the endpoint merge and map its outcome onto one branch table.

    Shared by the rebase-conflict fallback and the merge-only retry so
    both paths keep exactly one branch table; ``rebase_outcome`` is the
    marker each caller wants preserved for ``_classify_rebase_outcome``.
    """
    merge_result = endpoint_merge_with_resolution(root, target, conflict_resolver)
    if merge_result is None:
        # The merge attempt raised; surface that as the headline
        # conflict state (the merge-attempt reason is more
        # informative than the generic "both conflicted" message).
        _clear_record_if_no_inflight_op(root)
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=None,
            short_circuit=(
                _failed_rebase_state(rebase_outcome, target)
                if isinstance(rebase_outcome, RebaseFailed)
                else record_conflict(
                    reason="rebase conflict followed by merge attempt exception",
                    target=target,
                    failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
                )
            ),
        )
    if merge_result.outcome == "merge_commit_pending":
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=merge_result,
            short_circuit=record_conflict(
                reason=merge_result.reason or "verified merge commit pending",
                target=target,
                failure_cause=merge_result.failure_cause,
            ).set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT),
        )
    if merge_result.outcome in ("conflict", RESOLUTION_FAILED):
        _clear_record_if_no_inflight_op(root)
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=merge_result,
            short_circuit=record_rebase_outcome(
                rebase_outcome=rebase_outcome,
                merge_attempted=True,
                merge_outcome=merge_result,
                target=target,
            ),
        )

    # The RebaseConflicts / RebaseFailed marker is preserved so
    # _classify_rebase_outcome can distinguish the AC-06
    # rebase-conflicted-then-cleanly-merged path (headline: merged)
    # from the AC-05 plain-rebase path (headline: rebased). Both
    # conflicted paths return earlier, so this tail is reached only
    # when merge_outcome.outcome is in {"success", "noop"} and the
    # classifier returns the "merged" headline, not "conflict".
    return RebaseRunResult(
        rebase_outcome=rebase_outcome,
        merge_attempted=True,
        merge_outcome=merge_result,
        short_circuit=None,
    )


def _clear_record_if_no_inflight_op(root: Path) -> None:
    """Clear the durable record ONLY when no in-progress operation remains.

    Terminal-state invariant: clearing the record while ``MERGE_HEAD``
    or rebase bookkeeping is still on disk ORPHANS that state --
    startup recovery reconciles only operations whose record it holds,
    so the leftover markers fail ``check_rebase_preconditions`` at
    every later seam with nothing ever able to clean them. When the
    proof of cleanliness cannot be obtained the record is retained:
    a spurious retention costs one redundant recovery pass at next
    startup, a spurious clear costs the worktree its integration
    forever.
    """
    try:
        clean = merge_state(root) == MERGE_STATE_NONE and not rebase_in_progress(root)
    except Exception as state_exc:
        logger.warning(
            "auto_integrate: could not prove no in-flight operation remains"
            " in {} ({}); retaining the durable record for startup recovery",
            root,
            state_exc,
        )
        return
    if not clean:
        logger.warning(
            "auto_integrate: in-progress merge/rebase state remains in {}"
            " after a failed integration; retaining the durable record so"
            " startup recovery can reconcile it",
            root,
        )
        return
    clear_record(root)


def _abort_rebase_after_conflict(root: Path, git_ops: GitOps | None = None) -> None:
    """Abort a conflicted rebase; never raises."""
    try:
        if git_ops is not None and type(git_ops).__name__ != "_ProductionGitOps":
            git_ops.abort_rebase(root)
        elif rebase_in_progress(root):
            abort_rebase_discarding_progress(root)
    except Exception as abort_exc:
        logger.warning("auto_integrate: abort_rebase failed: {}", abort_exc)


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: B3
# ladder rung: 3
# AC-14 rationale: B6
# ladder rung: 3
# AC-14 rationale: B7
# ladder rung: 3
# AC-14 rationale: D11
# ladder rung: 3
# ----- end AC-14 catalog evidence -----
