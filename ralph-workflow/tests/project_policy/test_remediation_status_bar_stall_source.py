from __future__ import annotations

from pathlib import Path

from ralph.display.status_bar import StatusBarModel
from ralph.project_policy.status_bar import (
    push_remediation_status_bar,
    remediation_status_bar_session,
)
from ralph.workspace.scope import WorkspaceScope

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_remediation_footer_regression_keeps_watchdog_attention_live": (
        "same assertion, now made inside the remediation scope production always uses: a "
        "remediation bar is a scoped overlay owned by the status bar authority, so a push outside "
        "any remediation is ignored instead of leaving a stale footer behind"
    ),
}


class _Display:
    def __init__(self) -> None:
        self.models: list[StatusBarModel] = []

    def update_status_bar(self, model: object) -> None:
        assert isinstance(model, StatusBarModel)
        self.models.append(model)


def test_remediation_footer_regression_keeps_watchdog_attention_live() -> None:
    display = _Display()
    scope = WorkspaceScope(root=Path("/workspace"), allowed_roots=frozenset({Path("/workspace")}))

    with remediation_status_bar_session(display, scope):
        push_remediation_status_bar(display, scope, max_attempts=3)
        pushed = display.models[-1]

    assert pushed.attention is None
