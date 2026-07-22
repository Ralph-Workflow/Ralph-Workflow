"""The hardcoded conflict-resolution pipeline: a single phase with a bounded loop.

This module is the conflict-resolution pipeline's single source of truth
for ROUTING. It is pure: no I/O, no agents, no workspace.
:mod:`ralph.pipeline.conflict_resolution.driver` executes what this module
decides.

The graph::

    rebase_conflict_resolution
              |
      resolved | -> resolved    (no surviving markers, invocation succeeded)
    unresolved | -> rebase_conflict_resolution  (round += 1, markers fed back)
      exhausted| -> abandoned   (budget spent; caller aborts the merge)

One rule bounds it, and it is load-bearing: **the deterministic marker scan
is a hard gate.** ``resolved`` requires BOTH a successful agent invocation
AND an empty surviving-marker list recomputed by Ralph over exactly the
paths that were unmerged before the round. An agent that reports success
over a file still carrying ``<<<<<<<`` has not resolved anything, and its
claim must never be able to launder a failing scan. Anything that is not
an unambiguous success routes back into another round or to
``abandoned`` -- never to ``resolved``.
"""

from __future__ import annotations

from typing import Final

#: The resolution phase. The phase name IS the drain name declared in
#: ``ralph/policy/defaults/agents.toml``, so there is no mapping table to
#: keep in sync (the convention :mod:`ralph.project_policy.pipeline_graph`
#: also follows).
PHASE_RESOLUTION: Final[str] = "rebase_conflict_resolution"

#: Terminal: every conflicted path is marker-free and the merge may be
#: staged and committed by Ralph.
TERMINAL_RESOLVED: Final[str] = "resolved"

#: Terminal: the budget is spent with markers still surviving. The caller
#: aborts the merge and records a resolution failure.
TERMINAL_ABANDONED: Final[str] = "abandoned"

#: Rounds allowed within ONE integration seam. Three rounds is enough to
#: converge a conflict or to prove it will not converge; a fourth is a
#: display flood, not a strategy. Deliberately a module constant rather
#: than a config key: a key would pull in the general config model, the
#: loader allowlist and the configuration docs for marginal benefit.
MAX_RESOLUTION_ROUNDS: Final[int] = 3


def rounds_spent(round_index: int, cap: int = MAX_RESOLUTION_ROUNDS) -> bool:
    """True when the resolution round budget is exhausted.

    Args:
        round_index: 1-based index of the round that just finished.
        cap: Round budget.

    Returns:
        Whether the driver must stop looping and route to
        :data:`TERMINAL_ABANDONED`.
    """
    return round_index >= cap


def route_after_round(
    *,
    invocation_succeeded: bool,
    surviving_marker_paths: tuple[str, ...],
    round_index: int,
    cap: int = MAX_RESOLUTION_ROUNDS,
) -> str:
    """Decide what happens after one resolution round.

    Fails closed: only an invocation that succeeded AND left no surviving
    conflict marker reaches :data:`TERMINAL_RESOLVED`. Every other
    combination either buys another round or abandons.

    Args:
        invocation_succeeded: Whether the agent invocation reported success.
        surviving_marker_paths: Paths that STILL contain conflict markers,
            recomputed deterministically by Ralph after the round.
        round_index: 1-based index of the round that just finished.
        cap: Round budget.

    Returns:
        :data:`TERMINAL_RESOLVED`, :data:`TERMINAL_ABANDONED`, or
        :data:`PHASE_RESOLUTION` to run another round.
    """
    if invocation_succeeded and not surviving_marker_paths:
        return TERMINAL_RESOLVED
    if rounds_spent(round_index, cap):
        return TERMINAL_ABANDONED
    return PHASE_RESOLUTION


__all__ = [
    "MAX_RESOLUTION_ROUNDS",
    "PHASE_RESOLUTION",
    "TERMINAL_ABANDONED",
    "TERMINAL_RESOLVED",
    "rounds_spent",
    "route_after_round",
]
