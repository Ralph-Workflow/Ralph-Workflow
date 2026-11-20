from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["mechanical_finish"]


def mechanical_finish(root: Path, target: str) -> bool:
    from ralph.git.operation_snapshot import OperationState, observe_operation_snapshot
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.conflict_resolution.rebase_loop import (
        current_rebase_identity,
        finish_resolved_rebase_stops,
    )

    snapshot = observe_operation_snapshot(root, target=target)
    if snapshot.operation_state is not OperationState.REBASE_PAUSED:
        return False
    if not snapshot.unmerged_query_ok or snapshot.unmerged_paths:
        return False
    record = read_record(root)
    if record is not None and record.resolving_rebase:
        owned = current_rebase_identity(root)
        if owned != (record.pre_feature_sha, record.pre_target_sha):
            return False
    try:
        return finish_resolved_rebase_stops(root, target)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("integration resolution: mechanical finish failed: {}", exc)
        return False
