from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.controller import RecoveryController

__all__ = [
    "entry_recovery_controller",
    "shared_recovery_controller",
    "standalone_integration_controller",
]


def standalone_integration_controller(
    root: Path,
    config: UnifiedConfig | None,
    *,
    ledger: AgentFailureLedger | None = None,
) -> RecoveryController:
    from ralph.agents.system_clock import SystemClock
    from ralph.pipeline.agent_entry_point import AgentEntryPoint
    from ralph.pipeline.attempt_runner import build_entry_point_recovery_controller
    from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
    from ralph.pipeline.runner import load_policy_bundle_for_run
    from ralph.workspace.scope import WorkspaceScope

    try:
        policy_bundle = (
            load_policy_bundle_for_run(WorkspaceScope(root=root), config)
            if config is not None
            else None
        )
    except (OSError, RuntimeError, ValueError, KeyError):
        policy_bundle = None
    return build_entry_point_recovery_controller(
        AgentEntryPoint.INTEGRATION_CONTINUATION,
        config=config,
        policy_bundle=policy_bundle,
        deps=AttemptRunnerDeps(
            clock=ledger.clock if ledger is not None else SystemClock(),
            invoke=PRODUCTION_INVOKE,
            probe_runner=None,
            workspace_probe=None,
            connectivity=None,
        ),
        ledger=ledger,
        probe_trusted_parent=root,
    )


def entry_recovery_controller(
    root: Path,
    config: UnifiedConfig | None,
    explicit: RecoveryController | None,
    *,
    ledger: AgentFailureLedger | None = None,
) -> RecoveryController:
    if explicit is not None:
        return explicit
    from ralph.pipeline.effect_executor import current_agent_effect_dependencies

    ambient = current_agent_effect_dependencies().recovery_controller
    if ambient is not None:
        return ambient
    return standalone_integration_controller(root, config, ledger=ledger)


@contextmanager
def shared_recovery_controller(controller: RecoveryController | None) -> Iterator[None]:
    from ralph.pipeline.effect_executor import (
        current_agent_effect_dependencies,
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )

    current = current_agent_effect_dependencies()
    if controller is None or current.recovery_controller is controller:
        yield
        return
    token = install_agent_effect_dependencies(
        agent_runner=current.agent_runner,
        recovery_controller=controller,
        pipeline_deps=current.pipeline_deps,
    )
    try:
        yield
    finally:
        reset_agent_effect_dependencies(token)
