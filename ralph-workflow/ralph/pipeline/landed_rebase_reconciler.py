from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import branch_sha, is_ancestor
from ralph.git.operations import get_head_sha
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_boundary import record_already_landed

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.rebase_state import RebaseState

_UNLANDED_OUTCOMES = (RebaseOutcomeKind.REBASED, RebaseOutcomeKind.MERGED)


def reconcile_landed_rebase(rebase: RebaseState, root: Path) -> RebaseState:
    if rebase.last_outcome not in _UNLANDED_OUTCOMES or rebase.fast_forwarded:
        return rebase
    target = rebase.last_target
    if not target:
        return rebase
    try:
        head = get_head_sha(root)
        target_sha = branch_sha(root, target)
        landed = bool(head and target_sha and is_ancestor(root, head, target_sha))
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug("landed-rebase reconciliation skipped: {}", exc)
        return rebase
    if not landed:
        return rebase
    logger.info(
        "integration record said {} but HEAD is already in {}; recording the work as landed",
        rebase.last_outcome,
        target,
    )
    return record_already_landed(rebase)


__all__ = ["reconcile_landed_rebase"]
