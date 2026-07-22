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

__all__ = ["MonotonicClock", "ResolutionInvoker", "run_conflict_resolution_pipeline"]


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
) -> bool:
    """Body of the bounded loop; see :func:`run_conflict_resolution_pipeline`."""
    conflicted = tuple(unmerged_paths(root))
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
    )
    emit_conflict_phase_line(
        display,
        f"entering rebase conflict resolution for '{target}' "
        f"({len(conflicted)} conflicted file(s))",
    )

    surviving: tuple[str, ...] = ()
    prompt_path: Path | None = None
    try:
        for round_index in range(1, MAX_RESOLUTION_ROUNDS + 1):
            push_conflict_status_bar(
                display,
                root,
                target=target,
                round_index=round_index,
                round_cap=MAX_RESOLUTION_ROUNDS,
            )
            prompt_path = render_conflict_prompt(
                root=root,
                target=target,
                conflicted_paths=conflicted,
                round_index=round_index,
                round_cap=MAX_RESOLUTION_ROUNDS,
                surviving_marker_paths=surviving,
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
            )
            if route == TERMINAL_RESOLVED:
                emit_conflict_phase_line(
                    display,
                    f"conflicts resolved in round {round_index}; "
                    "verifying and committing the merge",
                )
                return True
            emit_conflict_phase_line(
                display,
                f"round {round_index} did not resolve "
                f"{len(surviving)} file(s); "
                + (
                    "retrying with the surviving paths"
                    if round_index < MAX_RESOLUTION_ROUNDS
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
) -> ResolutionInvoker:
    """Build the real MCP-backed round runner.

    ONE monotonic deadline is taken here and every attempt is bounded by
    what is LEFT of it. That is what keeps the pipeline inside
    ``auto_integrate_resolve_timeout_seconds``: a per-round division
    alone cannot, because :func:`_run_one_round` may run up to
    ``_MAX_RESOLVER_AGENTS`` agents SEQUENTIALLY within a single round,
    so ``ceiling / MAX_RESOLUTION_ROUNDS`` handed out unconditionally
    would permit ``_MAX_RESOLVER_AGENTS`` times the configured ceiling.
    """
    deadline = clock() + _resolve_ceiling(config)

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        budget = _attempt_budget(deadline - clock(), round_index)
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


def _attempt_budget(remaining_seconds: float, round_index: int) -> float:
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

    Returns:
        A non-negative share, never larger than what is left.
    """
    rounds_left = max(1, MAX_RESOLUTION_ROUNDS - round_index + 1)
    return max(0.0, remaining_seconds) / rounds_left


def _resolve_ceiling(config: UnifiedConfig) -> float:
    """Wall-clock ceiling for the whole pipeline, with a safe fallback."""
    raw: object = getattr(
        config.general, "auto_integrate_resolve_timeout_seconds", None
    )
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    return _DEFAULT_RESOLVE_TIMEOUT_SECONDS
