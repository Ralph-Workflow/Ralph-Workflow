"""Contract test: BudgetCounterConfig.default_max must be required.

The silent fallback to _DEFAULT_BUDGET_CAP=5 is being removed.
BudgetCounterConfig must require an explicit default_max so the runtime
never invents a hidden cap.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ralph.policy.models import BudgetCounterConfig


def test_budget_counter_config_requires_default_max() -> None:
    """Constructing BudgetCounterConfig without default_max must raise ValidationError."""
    with pytest.raises((ValidationError, TypeError)):
        BudgetCounterConfig(tracks_budget=True, description="test counter")


def test_budget_counter_config_with_explicit_default_max_succeeds() -> None:
    """Constructing BudgetCounterConfig with an explicit default_max succeeds."""
    cfg = BudgetCounterConfig(tracks_budget=True, description="test", default_max=5)
    assert cfg.default_max == 5  # noqa: PLR2004


def test_budget_counter_config_zero_default_max_is_valid_for_untracked() -> None:
    """A default_max of 0 is accepted for non-tracking counters."""
    cfg = BudgetCounterConfig(tracks_budget=False, description="untracked", default_max=0)
    assert cfg.default_max == 0
