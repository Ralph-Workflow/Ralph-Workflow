from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.cli.commands.explain import _resolve_policy_dir
from ralph.display.context import make_display_context
from ralph.policy.loader import load_policy, load_policy_for_workspace_scope
from ralph.policy.validation import PolicyValidationError, validate_policy_completeness
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.context import DisplayContext


def check_policy_command(
    policy_dir: Path | None = None,
    counter_overrides: dict[str, int] | None = None,
    *,
    display_context: DisplayContext | None = None,
) -> int:
    from ralph.display.parallel_display import resolve_active_display

    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)
    try:
        if policy_dir is not None:
            resolved_dir = policy_dir
            if not resolved_dir.is_dir():
                display.emit_warning(f"Policy directory not found: {resolved_dir}")
                return 1
            bundle = load_policy(resolved_dir, strict_optional_files=True)
        else:
            resolved_dir, _ = _resolve_policy_dir()
            bundle = load_policy_for_workspace_scope(
                resolve_workspace_scope(),
                strict_optional_files=True,
            )

        if counter_overrides:
            validate_policy_completeness(bundle, cli_counter_overrides=counter_overrides)

        block_count = len(bundle.pipeline.blocks)
        lifecycle_count = len(bundle.pipeline.lifecycle_phases)
        phase_count = len(bundle.pipeline.phases)
        drain_count = len(bundle.agents.agent_drains)
        artifact_count = len(bundle.artifacts.artifacts)
        loop_count = len(bundle.pipeline.loop_counters)
        budget_count = len(bundle.pipeline.budget_counters)
        workflow_fallback_count = sum(
            1 for defn in bundle.pipeline.phases.values() if defn.workflow_fallback is not None
        )
        terminal_failure_phase = bundle.pipeline.recovery.terminal_failure_phase

        display.emit_status(f"Policy OK: {resolved_dir}")
        if bundle.pipeline.entry_block is not None:
            display.emit_status(f"  entry block: {bundle.pipeline.entry_block}")
        display.emit_status(f"  blocks: {block_count}")
        display.emit_status(f"  lifecycle completion phases: {lifecycle_count}")
        display.emit_status(f"  phases: {phase_count}")
        display.emit_status(f"  drains: {drain_count}")
        display.emit_status(f"  artifact contracts: {artifact_count}")
        display.emit_status(f"  loop counters: {loop_count}")
        display.emit_status(f"  budget counters: {budget_count}")
        display.emit_status(f"  workflow fallbacks: {workflow_fallback_count}")
        if terminal_failure_phase is not None:
            display.emit_status(f"  terminal failure phase: {terminal_failure_phase}")

        if counter_overrides and bundle.pipeline.budget_counters:
            display.emit_status("  effective budget caps (after --counter overrides):")
            for name, cfg in bundle.pipeline.budget_counters.items():
                effective = counter_overrides.get(name, cfg.default_max)
                display.emit_status(f"    {name}: {effective}")

        return 0

    except PolicyValidationError as exc:
        display.emit_warning(f"Policy validation error: {exc}")
        return 2
    except Exception as exc:
        display.emit_warning(f"Error loading policy: {exc}")
        return 1
