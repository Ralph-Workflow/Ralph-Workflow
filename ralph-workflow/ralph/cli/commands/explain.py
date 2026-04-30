"""explain command — render the active policy as a human-readable explanation."""

from __future__ import annotations

import sys
from pathlib import Path


def explain_command(policy_dir: Path | None = None) -> int:
    """Print a human-readable explanation of the active policy to stdout.

    The output consists of a WORKFLOW DIAGRAM section showing a deterministic
    pure-ASCII diagram of the pipeline, followed by a RALPH WORKFLOW section
    with the structured policy breakdown.

    Args:
        policy_dir: Directory containing policy TOML files. Defaults to the
            workspace-local policy directory, then the bundled defaults.

    Returns:
        Exit code: 0 on success, 1 on error.
    """
    from ralph.policy.explain import explain_policy  # noqa: PLC0415
    from ralph.policy.loader import load_policy  # noqa: PLC0415
    from ralph.policy.render import (  # noqa: PLC0415
        render_explanation_ascii,
        render_explanation_text,
    )

    try:
        if policy_dir is None:
            policy_dir = Path(__file__).parent.parent.parent / "policy" / "defaults"
        if not policy_dir.is_dir():
            print(f"Policy directory not found: {policy_dir}", file=sys.stderr)
            return 1
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)

        print("\n\nWORKFLOW DIAGRAM")
        print("=" * 70)
        print(render_explanation_ascii(explanation))
        print("\n")
        print(render_explanation_text(explanation))
        return 0
    except Exception as exc:
        print(f"Error loading policy: {exc}", file=sys.stderr)
        return 1
