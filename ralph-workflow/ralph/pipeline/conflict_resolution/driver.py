"""Drives the bounded conflict-resolution loop behind a deterministic gate.

Executes what :mod:`ralph.pipeline.conflict_resolution.graph` decides:
render a conflict-only prompt, run one agent round inside a real MCP
session, then RECOMPUTE the surviving conflict markers over exactly the
paths that were unmerged before the round. That recomputation is the
verdict. The agent's own success claim is an input to it, never a
substitute for it -- the same rule
:mod:`ralph.project_policy.pipeline_driver` applies to its analysis
agent.

The driver never aborts the surrounding run and never touches git state
itself: on exhaustion it returns ``False`` and
:func:`ralph.pipeline.auto_integrate_resolve.endpoint_merge_with_resolution`
performs the single abort-and-record it already owns. Duplicating the
abort here would double-report the same failure.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import paths_with_conflict_markers, unmerged_paths
from ralph.pipeline.conflict_resolution.graph import (
    MAX_RESOLUTION_ROUNDS,
    MAX_ROUNDS_PER_STOP,
    TERMINAL_RESOLVED,
    route_after_round,
)
from ralph.pipeline.conflict_resolution.prompt import render_conflict_prompt
from ralph.pipeline.conflict_resolution.session import (
    invoke_resolution_agent,
    resolution_chain_agents,
)
from ralph.pipeline.conflict_resolution.status import (
    capture_status_bar_model,
    clear_conflict_status_bar,
    emit_conflict_phase_line,
    push_conflict_status_bar,
    restore_status_bar,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

#: One resolution attempt: ``(agent_name, prompt_path, round_index) -> ok``.
#: Injected so the default-suite tests never launch a process.
type ResolutionInvoker = Callable[[str, "Path", int], bool]

#: Reads a monotonic wall clock. Injected so the whole-pipeline deadline
#: can be proven without sleeping through it.
type MonotonicClock = Callable[[], float]

#: Sentinel :func:`ralph.git.merge.unmerged_paths` returns when the query
#: itself failed. A repository that cannot be read is not a repository a
#: resolver can repair.
_QUERY_FAILED_SENTINEL = "<unmerged-path-query-failed>"

#: How many agents of the drain's chain may be tried on one conflicted
#: merge. Bounded because each attempt costs a full agent invocation at an
#: integration seam; two is enough to survive a single unavailable agent.
_MAX_RESOLVER_AGENTS = 2

#: Fallback wall-clock ceiling for the whole pipeline when the config does
#: not carry the key (partially-constructed configs in tests).
_DEFAULT_RESOLVE_TIMEOUT_SECONDS = 900.0

#: Shortest share worth spending on one attempt. Below it the remaining
#: budget is declined rather than used to start an agent that would be
#: force-cut before it could read its own prompt.
_MIN_ATTEMPT_SECONDS = 1.0

__all__ = [
    "MonotonicClock",
    "ResolutionInvoker",
    "resolution_deadline",
    "run_conflict_resolution_pipeline",
    "run_rebase_conflict_resolution_pipeline",
]


def resolution_deadline(
    config: UnifiedConfig, clock: MonotonicClock | None = None
) -> float:
    """Absolute monotonic instant the whole resolution must finish by.

    Public because a REBASE resolution spans several stops and every one
    of them must be bounded by ONE ceiling. A per-stop deadline would
    multiply ``auto_integrate_resolve_timeout_seconds`` by
    :data:`~ralph.pipeline.conflict_resolution.graph.MAX_REBASE_CONFLICT_STOPS`,
    turning a 15-minute budget into a 2.5-hour one. The caller takes this
    once before the loop and hands the same value to every stop.
    """
    return (clock or time.monotonic)() + _resolve_ceiling(config)


def run_rebase_conflict_resolution_pipeline(
    *,
    root: Path,
    target: str,
    stop: RebaseStop,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    deadline: float | None = None,
    invoke: ResolutionInvoker | None = None,
    clock: MonotonicClock | None = None,
) -> bool:
    """Resolve ONE commit a rebase has stopped on, or decline.

    Runs the same rounds, through the same MCP-backed session, under the
    same deterministic marker gate as
    :func:`run_conflict_resolution_pipeline`. Only three things differ:
    the footer and prompt name the commit being replayed, the round cap
    is :data:`~ralph.pipeline.conflict_resolution.graph.MAX_ROUNDS_PER_STOP`,
    and the footer is NOT captured/restored here -- the caller owns the
    footer for the whole multi-stop loop via
    :func:`~ralph.pipeline.conflict_resolution.status.conflict_status_bar_session`,
    because capturing per stop would capture the previous stop's own
    conflict bar and leave it pinned when the loop ends.

    Args:
        root: Repository root holding the paused rebase.
        target: Branch being rebased onto.
        stop: The commit this call must resolve.
        config: Run configuration, supplying the wall-clock ceiling.
        pipeline_deps: Pipeline dependency bundle for the agent session.
        workspace_scope: Workspace scope for the agent session.
        policy_bundle: Resolved policy supplying the drain's agent chain.
        display: Active display, when there is one.
        display_context: Display context, when there is one.
        deadline: Shared monotonic ceiling for the WHOLE rebase
            resolution, from :func:`resolution_deadline`. Defaults to a
            fresh per-stop ceiling, which is correct only for a
            single-stop rebase.
        invoke: Injected round runner; defaults to a real MCP-backed
            session.
        clock: Injected monotonic clock; defaults to
            :func:`time.monotonic`.

    Returns:
        ``True`` only when every path that conflicted on this stop is
        marker-free. Never raises.
    """
    try:
        return _run_rounds(
            root=root,
            target=target,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            display=display,
            display_context=display_context,
            invoke=invoke,
            clock=clock or time.monotonic,
            deadline=deadline,
            stop=stop,
        )
    except Exception as exc:
        logger.warning(
            "conflict_resolution: rebase stop {} failed: {}", stop.stop_index, exc
        )
        emit_conflict_phase_line(
            display, f"rebase conflict resolution failed: {exc}"
        )
        return False


def run_conflict_resolution_pipeline(
    *,
    root: Path,
    target: str,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    invoke: ResolutionInvoker | None = None,
    clock: MonotonicClock | None = None,
) -> bool:
    """Resolve the in-progress merge's conflicts, or decline.

    Args:
        root: Repository root holding the in-progress merge.
        target: Mainline branch being merged in.
        config: Run configuration, supplying the wall-clock ceiling.
        pipeline_deps: Pipeline dependency bundle for the agent session.
        workspace_scope: Workspace scope for the agent session.
        policy_bundle: Resolved policy supplying the drain's agent chain.
        display: Active display, when there is one.
        display_context: Display context, when there is one.
        invoke: Injected round runner; defaults to a real MCP-backed
            session.
        clock: Injected monotonic clock the whole-pipeline deadline is
            measured against; defaults to :func:`time.monotonic`.

    Returns:
        ``True`` only when every previously-conflicted path is
        marker-free. Never raises: any failure is reported as ``False``
        so the caller aborts the merge and records a resolution failure.
    """
    previous_model = capture_status_bar_model(display)
    try:
        return _run_rounds(
            root=root,
            target=target,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            display=display,
            display_context=display_context,
            invoke=invoke,
            clock=clock or time.monotonic,
            deadline=None,
            stop=None,
        )
    except Exception as exc:
        logger.warning("conflict_resolution: pipeline failed: {}", exc)
        emit_conflict_phase_line(display, f"conflict resolution failed: {exc}")
        return False
    finally:
        # A captured model is restored verbatim. When there was none to
        # capture, the footer would otherwise keep claiming a running
        # resolution until the run loop's next push -- which, at the
        # startup seam, can be a whole phase away.
        if previous_model is None:
            clear_conflict_status_bar(display, root)
        else:
            restore_status_bar(display, previous_model)


def _run_rounds(
    *,
    root: Path,
    target: str,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    invoke: ResolutionInvoker | None,
    clock: MonotonicClock,
    deadline: float | None,
    stop: RebaseStop | None,
) -> bool:
    """Body of the bounded loop, shared by both entry points.

    ``stop`` selects the mode. ``None`` is the endpoint-merge conflict
    this pipeline has always handled; a :class:`RebaseStop` is one commit
    of an in-progress rebase, which names the replayed commit in the
    prompt and the footer and reads its conflicted paths from the rebase
    stop rather than from the merge.

    Sharing the body rather than duplicating it is what guarantees the
    rebase path gets the SAME MCP session, the SAME exec-policy git
    denial, the SAME ``declare_complete`` contract and the SAME
    marker-scan gate -- the properties that make resolution safe at all.
    """
    round_cap = MAX_ROUNDS_PER_STOP if stop is not None else MAX_RESOLUTION_ROUNDS
    conflicted = (
        stop.conflicted_files if stop is not None else tuple(unmerged_paths(root))
    )
    if not conflicted or _QUERY_FAILED_SENTINEL in conflicted:
        emit_conflict_phase_line(
            display, "no readable conflicted paths; nothing a resolver can repair"
        )
        return False

    candidates = resolution_chain_agents(policy_bundle)[:_MAX_RESOLVER_AGENTS]
    if not candidates:
        emit_conflict_phase_line(
            display, "no agent bound to the rebase-conflict-resolution drain"
        )
        return False

    runner = invoke or _default_invoker(
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
        display=display,
        display_context=display_context,
        clock=clock,
        deadline=deadline,
        round_cap=round_cap,
    )
    emit_conflict_phase_line(
        display,
        f"entering rebase conflict resolution for '{target}' "
        f"({len(conflicted)} conflicted file(s))"
        + (
            f" replaying {stop.sha[:8]} {stop.subject}"
            if stop is not None
            else ""
        ),
    )

    surviving: tuple[str, ...] = ()
    prompt_path: Path | None = None
    try:
        for round_index in range(1, round_cap + 1):
            push_conflict_status_bar(
                display,
                root,
                target=target,
                round_index=round_index,
                round_cap=round_cap,
                stop_index=stop.stop_index if stop is not None else None,
                stop_cap=stop.stop_cap if stop is not None else None,
            )
            prompt_path = render_conflict_prompt(
                root=root,
                target=target,
                conflicted_paths=conflicted,
                round_index=round_index,
                round_cap=round_cap,
                surviving_marker_paths=surviving,
                replaying_commit_sha=stop.sha if stop is not None else None,
                replaying_commit_subject=(
                    stop.subject if stop is not None else None
                ),
                stop_index=stop.stop_index if stop is not None else None,
                stop_cap=stop.stop_cap if stop is not None else None,
            )
            if prompt_path is None:
                emit_conflict_phase_line(
                    display, "could not materialize the resolution prompt"
                )
                return False

            succeeded = _run_one_round(
                runner, candidates, prompt_path, round_index, display
            )
            # The hard gate: what the repository says, not what the agent
            # says. ``git add`` clears a file's unmerged bit even with
            # markers intact, so this textual re-scan is the only proof.
            surviving = tuple(paths_with_conflict_markers(root, conflicted))
            route = route_after_round(
                invocation_succeeded=succeeded,
                surviving_marker_paths=surviving,
                round_index=round_index,
                cap=round_cap,
            )
            if route == TERMINAL_RESOLVED:
                # Phase feedback has to name the operation actually in
                # flight: the merge path commits, the rebase path stages
                # the paths and hands back to ``git rebase --continue``.
                emit_conflict_phase_line(
                    display,
                    f"conflicts resolved in round {round_index}; "
                    + (
                        "verifying and continuing the rebase"
                        if stop is not None
                        else "verifying and committing the merge"
                    ),
                )
                return True
            emit_conflict_phase_line(
                display,
                f"round {round_index} did not resolve "
                f"{len(surviving)} file(s); "
                + (
                    "retrying with the surviving paths"
                    if round_index < round_cap
                    else "budget exhausted"
                ),
            )
    finally:
        if prompt_path is not None:
            with contextlib.suppress(OSError):
                prompt_path.unlink()

    emit_conflict_phase_line(
        display,
        "abandoning conflict resolution; conflict markers survive in: "
        + ", ".join(surviving or conflicted),
    )
    return False


def _run_one_round(
    runner: ResolutionInvoker,
    candidates: tuple[str, ...],
    prompt_path: Path,
    round_index: int,
    display: ParallelDisplay | None,
) -> bool:
    """Walk the chain for one round; ``True`` when a candidate succeeded."""
    for agent_name in candidates:
        emit_conflict_phase_line(
            display,
            f"round {round_index}: invoking {agent_name} to resolve the conflicts",
        )
        try:
            if runner(agent_name, prompt_path, round_index):
                return True
        except Exception as exc:
            logger.warning(
                "conflict_resolution: round {} with '{}' raised: {}",
                round_index,
                agent_name,
                exc,
            )
    return False


def _default_invoker(
    *,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    clock: MonotonicClock,
    deadline: float | None,
    round_cap: int,
) -> ResolutionInvoker:
    """Build the real MCP-backed round runner.

    ONE monotonic deadline is taken here and every attempt is bounded by
    what is LEFT of it. That is what keeps the pipeline inside
    ``auto_integrate_resolve_timeout_seconds``: a per-round division
    alone cannot, because :func:`_run_one_round` may run up to
    ``_MAX_RESOLVER_AGENTS`` agents SEQUENTIALLY within a single round,
    so ``ceiling / MAX_RESOLUTION_ROUNDS`` handed out unconditionally
    would permit ``_MAX_RESOLVER_AGENTS`` times the configured ceiling.

    A caller-supplied ``deadline`` extends that guarantee across a whole
    multi-stop rebase: every stop shares one instant, so ten stops cost
    the configured ceiling in total rather than ten times over.
    """
    effective_deadline = (
        deadline if deadline is not None else clock() + _resolve_ceiling(config)
    )

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        budget = _attempt_budget(
            effective_deadline - clock(), round_index, round_cap
        )
        if budget < _MIN_ATTEMPT_SECONDS:
            logger.warning(
                "conflict_resolution: the auto_integrate_resolve_timeout_seconds "
                "budget is spent; declining to invoke '{}' for round {}",
                agent_name,
                round_index,
            )
            return False
        return invoke_resolution_agent(
            agent_name=agent_name,
            prompt_path=prompt_path,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            display=display,
            display_context=display_context,
            max_session_seconds=budget,
        )

    return _invoke


def _attempt_budget(
    remaining_seconds: float,
    round_index: int,
    round_cap: int = MAX_RESOLUTION_ROUNDS,
) -> float:
    """Wall-clock share ONE attempt may consume.

    Bounding every attempt by the remainder of a single whole-pipeline
    deadline is what makes the cumulative cost of all rounds and of every
    sequential chain candidate inside them provably no greater than the
    configured ceiling. The remainder is then spread over the rounds
    still to come so a first round that burns everything cannot starve
    the retries that give the bounded loop its value.

    Args:
        remaining_seconds: Seconds left before the pipeline deadline.
        round_index: 1-based index of the round about to be attempted.
        round_cap: Rounds this mode allows.

    Returns:
        A non-negative share, never larger than what is left.
    """
    rounds_left = max(1, round_cap - round_index + 1)
    return max(0.0, remaining_seconds) / rounds_left


def _resolve_ceiling(config: UnifiedConfig) -> float:
    """Wall-clock ceiling for the whole pipeline, with a safe fallback."""
    raw: object = getattr(
        config.general, "auto_integrate_resolve_timeout_seconds", None
    )
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    return _DEFAULT_RESOLVE_TIMEOUT_SECONDS
