from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.proposal_seal import seal_baseline
from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.state import PipelineState


def _resolved(root: Path, revision: str) -> str | None:
    result = run_git(
        ("rev-parse", "--verify", "--quiet", revision),
        cwd=root,
        label="rollback-baseline:rev-parse",
    )
    sha = result.stdout.strip() if result.returncode == 0 else ""
    return sha or None


def refresh_rollback_baseline_after_landing(state: PipelineState, root: Path) -> PipelineState:
    baseline = state.rollback_baseline_ref
    if not baseline or state.rollback_pending:
        return state
    head = _resolved(root, "HEAD")
    if head is None or _resolved(root, f"{baseline}^") == head:
        return state
    try:
        resealed = seal_baseline(root, uuid.uuid4().hex, force=False)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("Could not reseal the rollback baseline after a landing: {}", exc)
        return state
    logger.info(
        "HEAD moved past the rollback baseline {}; resealed it at the landed commit as {}",
        baseline,
        resealed.ref if resealed is not None else "HEAD",
    )
    return state.copy_with(rollback_baseline_ref=resealed.ref if resealed is not None else None)


__all__ = ["refresh_rollback_baseline_after_landing"]
