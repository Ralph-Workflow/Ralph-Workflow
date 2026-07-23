"""Status-bar push helper for the project-policy-readiness pipeline.

Lives in its own module so :mod:stays under the 1000-line repository cap while still letting the
remediation phase surface a live Remediation N/Max label in the
persistent footer (the operator-grade fix the wt-028-display consolidation
introduced for policy-remediation). The helper is intentionally
defensive: any display failure is swallowed -- presentation must NEVER
block remediation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.display.parallel_display import phase_style_for_phase
from ralph.display.status_bar import StatusBarModel

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.workspace.scope import WorkspaceScope


def push_remediation_status_bar(
    display: object,
    workspace_scope: WorkspaceScope,
    max_attempts: int,
    *,
    attempt: int = 1,
    elapsed_seconds: float | None = None,
    agent_name: str | None = None,
) -> None:
    """Seed the persistent status bar for the remediation phase.

    Mirrors the run loop phase push so the footer shows the working
    directory and the active phase during remediation instead of nothing.
    attempt is the 1-indexed live attempt (1 on the first try, 2 on
    the first re-try, etc.) so the bar surfaces real progress instead
    of a hardcoded Dev 1/N placeholder. Defensive: any display
    failure is swallowed -- presentation must never block remediation.
    """
    try:
        model = StatusBarModel(
            workspace_root=str(workspace_scope.root),
            phase_label="Policy Remediation",
            phase_style=phase_style_for_phase("policy_remediation"),
            outer_dev_iteration=attempt,
            outer_dev_cap=max_attempts,
            outer_label="Remediation",
            elapsed_seconds=elapsed_seconds,
            agent_name=agent_name,
        )
        update_raw: object = getattr(display, "update_status_bar", None)
        update = cast("Callable[[object], None] | None", update_raw)
        if update is not None:
            update(model)
    except Exception as exc:  # defensive: presentation must never block remediation
        logger.debug("remediation status-bar push failed (non-fatal): {}", exc)
