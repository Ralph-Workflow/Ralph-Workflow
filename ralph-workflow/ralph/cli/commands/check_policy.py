"""check_policy command — validate the active policy and report results."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def check_policy_command(policy_dir: Path | None = None) -> int:
    """Validate the active policy and print a pass/fail summary to stdout.

    Resolves the policy directory the same way as --explain-policy, loads and
    validates the policy, then prints a summary of what was found or the
    validation error.

    Args:
        policy_dir: Directory containing policy TOML files. Defaults to the
            workspace-local .agent directory (if it contains TOML files),
            then the bundled defaults.

    Returns:
        Exit code: 0 on success, 1 on general error, 2 on policy validation error.
    """
    from ralph.cli.commands.explain import _resolve_policy_dir  # noqa: PLC0415
    from ralph.policy.loader import PolicyValidationError as LoaderValidationError  # noqa: PLC0415
    from ralph.policy.loader import load_policy  # noqa: PLC0415
    from ralph.policy.validation import PolicyValidationError  # noqa: PLC0415

    try:
        if policy_dir is not None:
            resolved_dir = policy_dir
        else:
            resolved_dir, _ = _resolve_policy_dir()

        if not resolved_dir.is_dir():
            print(f"Policy directory not found: {resolved_dir}", file=sys.stderr)
            return 1

        bundle = load_policy(resolved_dir)

        phase_count = len(bundle.pipeline.phases)
        drain_count = len(bundle.agents.agent_drains)
        artifact_count = len(bundle.artifacts.artifacts)
        loop_count = len(bundle.pipeline.loop_counters)
        budget_count = len(bundle.pipeline.budget_counters)

        print(f"Policy OK: {resolved_dir}")
        print(f"  phases: {phase_count}")
        print(f"  drains: {drain_count}")
        print(f"  artifact contracts: {artifact_count}")
        print(f"  loop counters: {loop_count}")
        print(f"  budget counters: {budget_count}")
        return 0

    except (PolicyValidationError, LoaderValidationError) as exc:
        print(f"Policy validation error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error loading policy: {exc}", file=sys.stderr)
        return 1
