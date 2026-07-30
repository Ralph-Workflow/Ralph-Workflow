"""Regression coverage for conflict-footer watchdog attention ownership (S-1)."""

from __future__ import annotations

from pathlib import Path

from ralph.display.status_bar import StatusBarModel
from ralph.pipeline.conflict_resolution.status import push_conflict_status_bar


class _Display:
    def __init__(self) -> None:
        self.models: list[StatusBarModel] = []

    def update_status_bar(self, model: object) -> None:
        assert isinstance(model, StatusBarModel)
        self.models.append(model)


def test_conflict_footer_regression_keeps_watchdog_attention_live() -> None:
    """S-1: conflict pushes leave attention unset for Live-tick substitution."""
    display = _Display()

    push_conflict_status_bar(
        display,
        Path("/workspace"),
        target="main",
        round_index=1,
        round_cap=3,
    )

    assert display.models[-1].attention is None
