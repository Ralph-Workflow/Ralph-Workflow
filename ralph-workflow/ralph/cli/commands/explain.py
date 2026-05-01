"""explain command — render the active policy as a human-readable explanation."""

from __future__ import annotations

import sys
from pathlib import Path

_BUNDLED_DEFAULTS_DIR: Path = Path(__file__).parent.parent.parent / "policy" / "defaults"


def _resolve_policy_dir() -> tuple[Path, bool]:
    """Resolve the policy directory to use when none is explicitly provided.

    Prefers the project-local .agent directory if it contains TOML files,
    then falls back to the bundled defaults.

    Returns:
        Tuple of (policy_dir, is_bundled_default). is_bundled_default is True
        when no project-local policy was found and the bundled defaults are used.
    """
    try:
        from ralph.workspace.scope import resolve_workspace_scope  # noqa: PLC0415

        scope = resolve_workspace_scope()
        agent_dir = scope.root / ".agent"
        if agent_dir.is_dir() and any(agent_dir.glob("*.toml")):
            return agent_dir, False
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
    from ralph.policy.explain import explain_policy  # noqa: PLC0415
    from ralph.policy.loader import load_policy  # noqa: PLC0415
    from ralph.policy.render import (  # noqa: PLC0415
        render_explanation_ascii,
        render_explanation_text,
    )
    from ralph.policy.validation import PolicyValidationError  # noqa: PLC0415

    try:
        if policy_dir is not None:
            resolved_dir = policy_dir
            is_bundled = False
        else:
            resolved_dir, is_bundled = _resolve_policy_dir()
        if not resolved_dir.is_dir():
            print(f"Policy directory not found: {resolved_dir}", file=sys.stderr)
            return 1
        if is_bundled:
            print(
                "INFO: Using bundled default policy — "
                "no project-local .agent/*.toml files found"
            )
        print(f"Policy source: {resolved_dir}")
        bundle = load_policy(resolved_dir)
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
