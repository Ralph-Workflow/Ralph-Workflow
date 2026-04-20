"""Tests for drain identity handling in capability mapping."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    DrainClass,
    PolicyMode,
    SessionDrain,
    drain_class_for_session,
    drain_to_policy_mode,
)


def test_drain_class_preserves_analysis_identity() -> None:
    """Analysis must not collapse to planning drain class."""

    assert drain_class_for_session(SessionDrain.PLANNING) is DrainClass.PLANNING
    assert drain_class_for_session(SessionDrain.ANALYSIS) is DrainClass.ANALYSIS


def test_drain_policy_mode_preserves_read_only_drain_identity() -> None:
    """Read-only drains keep distinct policy identities."""

    assert drain_to_policy_mode(SessionDrain.PLANNING) is PolicyMode.PLANNING
    assert drain_to_policy_mode(SessionDrain.ANALYSIS) is PolicyMode.ANALYSIS
    assert drain_to_policy_mode(SessionDrain.REVIEW) is PolicyMode.REVIEW


@pytest.mark.parametrize("lossy_alias", ["dev", "fixer", "read_only"])
def test_lossy_role_aliases_are_rejected(lossy_alias: str) -> None:
    """Role aliases should not be silently normalized into a drain."""

    with pytest.raises(ValueError, match="Unknown session drain"):
        drain_class_for_session(lossy_alias)
