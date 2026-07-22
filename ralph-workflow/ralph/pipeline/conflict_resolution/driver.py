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

__all__ = ["ResolutionInvoker", "run_conflict_resolution_pipeline"]


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
        )
    except Exception as exc:
        logger.warning("conflict_resolution: pipeline failed: {}", exc)
        emit_conflict_phase_line(display, f"conflict resolution failed: {exc}")
        return False
    finally:
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
) -> ResolutionInvoker:
    """Build the real MCP-backed round runner.

    The configured ceiling is divided by the round cap so the whole
    pipeline stays inside ``auto_integrate_resolve_timeout_seconds``
    rather than multiplying it by the number of rounds.
    """
    per_round_seconds = _resolve_ceiling(config) / MAX_RESOLUTION_ROUNDS

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        return invoke_resolution_agent(
            agent_name=agent_name,
            prompt_path=prompt_path,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            display=display,
            display_context=display_context,
            max_session_seconds=per_round_seconds,
        )

    return _invoke


def _resolve_ceiling(config: UnifiedConfig) -> float:
    """Wall-clock ceiling for the whole pipeline, with a safe fallback."""
    raw: object = getattr(
        config.general, "auto_integrate_resolve_timeout_seconds", None
    )
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    return _DEFAULT_RESOLVE_TIMEOUT_SECONDS
