"""Regression coverage for suppressed conflict resolver strategy reporting."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity, prior_conflict_count
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_next_strategy_gets_a_fresh_budget_and_is_named(monkeypatch: MonkeyPatch) -> None:
    """Escalation both resets its per-rung budget and tells the operator its name."""
    state = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=4,
        last_conflict_strategy_index=1,
        conflict_strategy_index=2,
    )
    identity = ConflictIdentity(feature_sha="feature", target_sha="main")
    assert prior_conflict_count(state, "main", identity) == 0
    suppressed_state = state.model_copy(update={"last_conflict_strategy_index": 2})

    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_resolve_context",
        lambda *_args: (Path("/workspace"), "feature", "main", None),
    )
    monkeypatch.setattr(
        auto_integrate,
        "_check_early_skips",
        lambda *_args, **_kwargs: (None, (Path("/workspace"), "feature", "main")),
    )
    monkeypatch.setattr(auto_integrate, "observe_conflict_identity", lambda *_args: identity)
    monkeypatch.setattr(
        auto_integrate,
        "_freshen_attempt_target",
        lambda *_args, **_kwargs: (None, None),
    )
    monkeypatch.setattr(
        auto_integrate,
        "_integrate_once",
        lambda *_args, **_kwargs: (RebaseState(last_action="skipped"), False),
    )
    config = UnifiedConfig.model_validate(
        {
            "general": {"auto_integrate_enabled": True, "auto_integrate_target": "main"},
            "conflict_resolution": {"max_consecutive_resolver_attempts": 4},
        }
    )
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING", format="{message}")
    try:
        auto_integrate._auto_integrate_after_commit_inner(
            config,
            WorkspaceScope(Path("/workspace")),
            suppressed_state,
            conflict_resolver=lambda *_args: False,
        )
    finally:
        logger.remove(sink)

    assert any("merge_instead" in message for message in warnings)
