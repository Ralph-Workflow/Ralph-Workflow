"""explain command — render the active policy as a human-readable explanation."""

from __future__ import annotations

import sys
from pathlib import Path

_BUNDLED_DEFAULTS_DIR: Path = Path(__file__).parent.parent.parent / "policy" / "defaults"


def _resolve_policy_dir() -> tuple[Path, bool]:
    """Resolve the default policy directory to describe to the user.

    Linked worktrees inherit from the main checkout unless the current worktree
    has an explicit local override file.
    """
    try:
        from ralph.workspace.scope import resolve_workspace_scope  # noqa: PLC0415

        scope = resolve_workspace_scope()
        policy_dir = scope.resolve_agent_file("pipeline.toml").parent
        if policy_dir.is_dir() and any(policy_dir.glob("*.toml")):
            return policy_dir, False
    except Exception:
        pass
    return _BUNDLED_DEFAULTS_DIR, True


def explain_command(policy_dir: Path | None = None) -> int:
    """Print a human-readable explanation of the active policy to stdout.

    The output starts with the policy source directory, then a WORKFLOW DIAGRAM
    section showing a deterministic pure-ASCII diagram of the pipeline, followed
    by a RALPH WORKFLOW section with the structured policy breakdown.

    Args:
        policy_dir: Directory containing policy TOML files. Defaults to the
            workspace-local .agent directory (if it contains TOML files),
            then the bundled defaults.

    Returns:
        Exit code: 0 on success, 1 on general error, 2 on policy validation error.
    """
    from ralph.config.loader import load_config  # noqa: PLC0415
    from ralph.policy.explain import explain_policy  # noqa: PLC0415
    from ralph.policy.loader import load_policy, load_policy_for_workspace_scope  # noqa: PLC0415
    from ralph.policy.render import (  # noqa: PLC0415
        render_explanation_ascii,
        render_explanation_text,
    )
    from ralph.policy.validation import PolicyValidationError  # noqa: PLC0415

    try:
        if policy_dir is not None:
            resolved_dir = policy_dir
            is_bundled = False
            if not resolved_dir.is_dir():
                print(f"Policy directory not found: {resolved_dir}", file=sys.stderr)
                return 1
            bundle = load_policy(resolved_dir)
        else:
            from ralph.workspace.scope import resolve_workspace_scope  # noqa: PLC0415

            scope = resolve_workspace_scope()
            config = load_config(None, {}, workspace_scope=scope)
            resolved_dir, is_bundled = _resolve_policy_dir()
            bundle = load_policy_for_workspace_scope(scope, config=config)
        if is_bundled:
            print(
                "INFO: Using bundled default policy — "
                "no project-local .agent/*.toml files found"
            )
        print(f"Policy source: {resolved_dir}")
        explanation = explain_policy(bundle)

        print("\n\nWORKFLOW DIAGRAM")
        print("=" * 70)
        print(render_explanation_ascii(explanation))
        print("\n")
        print(render_explanation_text(explanation))
        return 0
    except PolicyValidationError as exc:
        print(f"Policy validation error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error loading policy: {exc}", file=sys.stderr)
        return 1
