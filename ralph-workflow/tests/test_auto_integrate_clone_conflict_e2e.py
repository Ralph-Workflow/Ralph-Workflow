"""Fast black-box coverage for shared-ref conflict protection.

The former clone, sibling-worktree, and hook-driven tests all rebuilt real
repositories to reprove the bounded retry state machine.  Retry ordering is
covered deterministically in ``test_auto_integrate_runner_seam.py``; real-Git
endpoint conflict and worktree topology remain represented in
``test_auto_integrate.py`` and ``test_auto_integrate_worktree_sync.py``.

This file retains its distinct observable contract through the injected Git
resolvers: a target checked out in a sibling worktree refuses a stale feature
SHA, classifies the refusal as retryable, and never invokes compare-and-swap.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.git.merge import WORKTREE_FOUND
from ralph.pipeline import auto_integrate_ff

pytestmark = pytest.mark.subprocess_e2e


def test_checked_out_sibling_target_refuses_stale_fast_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production landing decision fails closed with a retryable reason."""
    feature = Path("/workspace/feature")
    sibling = Path("/workspace/main")

    def _observe(_root: Path, _branch: str) -> tuple[str, bool]:
        return "sibling-head", True

    def _is_ancestor(_root: Path, _older: str, _newer: str) -> bool:
        return True

    def _main_root(_root: Path) -> Path:
        return sibling

    def _lookup(_root: Path, _branch: str) -> tuple[str, Path]:
        return WORKTREE_FOUND, sibling

    def _refuse(_root: Path, _sha: str) -> bool:
        return False

    monkeypatch.setattr(
        auto_integrate_ff,
        "observe_branch_sha",
        _observe,
    )
    monkeypatch.setattr(auto_integrate_ff, "is_ancestor", _is_ancestor)
    monkeypatch.setattr(
        auto_integrate_ff, "find_main_worktree_root", _main_root
    )
    monkeypatch.setattr(
        auto_integrate_ff,
        "worktree_lookup",
        _lookup,
    )
    monkeypatch.setattr(
        auto_integrate_ff,
        "fast_forward_via_worktree",
        _refuse,
    )
    compare_and_swap = MagicMock(return_value=True)
    monkeypatch.setattr(
        auto_integrate_ff, "compare_and_swap_branch", compare_and_swap
    )

    landed, reason = auto_integrate_ff.fast_forward_target(
        feature, "main", "stale-feature-head"
    )

    assert landed is False
    assert auto_integrate_ff.is_retryable_fast_forward_failure(reason) is True
    assert "checked out" in reason
    compare_and_swap.assert_not_called()
