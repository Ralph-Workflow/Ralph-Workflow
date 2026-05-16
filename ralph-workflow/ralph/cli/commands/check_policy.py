"""check_policy command — validate the active policy and report results."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from ralph.cli.commands.explain import _resolve_policy_dir
from ralph.policy.loader import load_policy, load_policy_for_workspace_scope
from ralph.policy.validation import PolicyValidationError, validate_policy_completeness
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path


def check_policy_command(
    policy_dir: Path | None = None,
    counter_overrides: dict[str, int] | None = None,
) -> int:
    """Validate the active policy and print a pass/fail summary to stdout.

    Resolves the policy directory the same way as --explain-policy, loads and
    validates the policy, then prints a summary of what was found or the
    validation error. When counter_overrides are supplied, validates that every
    key is declared in pipeline.budget_counters.

    Args:
        policy_dir: Directory containing policy TOML files. Defaults to the
            workspace-local .agent directory (if it contains TOML files),
            then the bundled defaults.
        counter_overrides: Budget counter overrides from --counter flags. Any key
            not declared in pipeline.budget_counters raises a PolicyValidationError.

    Returns:
        Exit code: 0 on success, 1 on general error, 2 on policy validation error.
    """
    try:
        if policy_dir is not None:
            resolved_dir = policy_dir
            if not resolved_dir.is_dir():
                print(f"Policy directory not found: {resolved_dir}", file=sys.stderr)
                return 1
            bundle = load_policy(resolved_dir)
        else:
            resolved_dir, _ = _resolve_policy_dir()
            bundle = load_policy_for_workspace_scope(resolve_workspace_scope())

        if counter_overrides:
            validate_policy_completeness(bundle, cli_counter_overrides=counter_overrides)

        phase_count = len(bundle.pipeline.phases)
        drain_count = len(bundle.agents.agent_drains)
        artifact_count = len(bundle.artifacts.artifacts)
        loop_count = len(bundle.pipeline.loop_counters)
        budget_count = len(bundle.pipeline.budget_counters)
        workflow_fallback_count = sum(
            1 for defn in bundle.pipeline.phases.values() if defn.workflow_fallback is not None
        )
        terminal_failure_phase = bundle.pipeline.recovery.terminal_failure_phase

        print(f"Policy OK: {resolved_dir}")
        print(f"  phases: {phase_count}")
        print(f"  drains: {drain_count}")
        print(f"  artifact contracts: {artifact_count}")
        print(f"  loop counters: {loop_count}")
        print(f"  budget counters: {budget_count}")
        print(f"  workflow fallbacks: {workflow_fallback_count}")
        if terminal_failure_phase is not None:
            print(f"  terminal failure phase: {terminal_failure_phase}")

        if counter_overrides and bundle.pipeline.budget_counters:
            print("  effective budget caps (after --counter overrides):")
            for name, cfg in bundle.pipeline.budget_counters.items():
                effective = counter_overrides.get(name, cfg.default_max)
                print(f"    {name}: {effective}")

        return 0

    except PolicyValidationError as exc:
        print(f"Policy validation error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error loading policy: {exc}", file=sys.stderr)
        return 1
