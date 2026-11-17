from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.display.context import make_display_context
from ralph.display.parallel_display import resolve_active_display
from ralph.policy.explain import explain_policy
from ralph.policy.loader import load_policy
from ralph.policy.render import render_explanation_ascii, render_explanation_text
from ralph.policy.validation import PolicyValidationError
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext

_BUNDLED_DEFAULTS_DIR: Path = Path(__file__).parent.parent.parent / "policy" / "defaults"


def _resolve_policy_dir() -> tuple[Path, bool]:
    with contextlib.suppress(OSError, RuntimeError, ValueError, KeyError, AttributeError):
        scope = resolve_workspace_scope()
        policy_dir = scope.resolve_agent_file("pipeline.toml").parent
        if policy_dir.is_dir() and any(policy_dir.glob("*.toml")):
            return policy_dir, False
    return _BUNDLED_DEFAULTS_DIR, True


def explain_command(
    policy_dir: Path | None = None,
    *,
    display_context: DisplayContext | None = None,
) -> int:
    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)

    try:
        if policy_dir is not None:
            resolved_dir = policy_dir
            is_bundled = False
            if not resolved_dir.is_dir():
                display.emit_warning(f"Policy directory not found: {resolved_dir}")
                return 1
            bundle = load_policy(resolved_dir)
        else:
            resolved_dir, is_bundled = _resolve_policy_dir()
            bundle = load_policy(resolved_dir)
        if is_bundled:
            display.emit_status(
                "INFO: Using bundled default policy — no project-local .agent/*.toml files found"
            )
        display.emit_status(f"Policy source: {resolved_dir}")
        explanation = explain_policy(bundle)

        display.emit_status("")
        display.emit_status("WORKFLOW DIAGRAM")
        display.emit_status("=" * 70)
        display.emit_status(render_explanation_ascii(explanation))
        display.emit_status("")
        display.emit_status(render_explanation_text(explanation))
    except PolicyValidationError as exc:
        display.emit_warning(f"Policy validation error: {exc}")
        return 2
    except (OSError, RuntimeError, ValueError, KeyError, AttributeError, TypeError) as exc:
        display.emit_warning(f"Error loading policy: {exc}")
        return 1
    else:
        return 0
