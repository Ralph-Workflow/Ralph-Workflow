"""Regression coverage for remediation-footer watchdog attention ownership (S-1)."""

from __future__ import annotations

from pathlib import Path

from ralph.display.status_bar import StatusBarModel
from ralph.project_policy.status_bar import push_remediation_status_bar
from ralph.workspace.scope import WorkspaceScope


class _Display:
    def __init__(self) -> None:
        self.models: list[StatusBarModel] = []

    def update_status_bar(self, model: object) -> None:
        assert isinstance(model, StatusBarModel)
        self.models.append(model)


def test_remediation_footer_regression_keeps_watchdog_attention_live() -> None:
    """S-1: remediation pushes leave attention unset for Live-tick substitution."""
    display = _Display()
    scope = WorkspaceScope(root=Path("/workspace"), allowed_roots=frozenset({Path("/workspace")}))

    push_remediation_status_bar(display, scope, max_attempts=3)

    assert display.models[-1].attention is None
