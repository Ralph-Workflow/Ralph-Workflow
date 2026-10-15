"""AgentBudgetRegistry and RecoveryController durability tests.

Split from :mod:`tests.test_retry_budget_durability` so neither module
carries more than one top-level test class (repo-structure policy).
"""

from __future__ import annotations

from ralph.recovery.agent_budget_registry import AgentBudgetRegistry
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory


def _make_failure(
    *,
    category: FailureCategory = FailureCategory.AGENT,
    reason: str = "transient error",
    agent: str = "claude",
    phase: str = "development",
    counts: bool = True,
) -> ClassifiedFailure:
    return ClassifiedFailure(
        category,
        reason,
        agent,
        phase,
        counts,
        None,
        reason,
    )


class TestAgentBudgetRegistryDurability:
    """AgentBudgetRegistry serialization, deserialization, and durability."""

    def test_to_dict_and_from_dict(self) -> None:
        reg = AgentBudgetRegistry()
        reg = reg.set_budget("development", "claude", max_retries=3, consumed=1)
        failure = _make_failure(counts=True)
        reg = reg.debit("development", "claude", failure)
        state = reg.get("development", "claude")
        assert state is not None
        assert state.consumed == 2

        serialized = reg.to_dict()
        assert "development:claude" in serialized
        assert serialized["development:claude"]["consumed"] == 2
        assert serialized["development:claude"]["max_retries"] == 3

        restored = AgentBudgetRegistry.from_dict(serialized)
        restored_state = restored.get("development", "claude")
        assert restored_state is not None
        assert restored_state.consumed == 2
        assert restored_state.max_retries == 3
        assert restored_state.remaining == 1
        assert not restored.is_exhausted("development", "claude")

    def test_exhaustion_persists_across_restart(self) -> None:
        reg = AgentBudgetRegistry()
        reg = reg.set_budget("development", "claude", max_retries=1)
        failure = _make_failure(counts=True)
        reg = reg.debit("development", "claude", failure)
        assert reg.is_exhausted("development", "claude")

        serialized = reg.to_dict()
        restored = AgentBudgetRegistry.from_dict(serialized)
        # Restart must NOT replenish attempts
        assert restored.is_exhausted("development", "claude")
        res_state = restored.get("development", "claude")
        assert res_state is not None
        assert res_state.remaining == 0
