from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.clock import Clock
    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_actions import AgentRunner
    from ralph.pipeline.key_unavailable import KeyUnavailable
    from ralph.pipeline.resolution_attempt_key import ResolutionAttemptKey
    from ralph.recovery.controller import RecoveryController


@dataclass(frozen=True)
class StopResolutionTrigger:
    name: Literal["rebase_stop", "merge_stop"]
    root: Path
    target: str
    resolver: object
    record: IntegrationRecord | None = None
    config: UnifiedConfig | None = None
    conflict_config: ConflictResolutionConfig | None = None
    recovery_controller: RecoveryController | None = None
    phase: str = "rebase_conflict_resolution"
    chain_agents: tuple[str, ...] | None = None
    config_path: Path | None = None
    force_retry: bool = False
    agent_runner: AgentRunner | None = None
    eligible_agents: tuple[str, ...] | None = None
    config_fingerprint: str | None = None
    clock: Clock | None = None
    launcher: Callable[[], object] | None = None
    key_provider: Callable[[], ResolutionAttemptKey | KeyUnavailable] | None = None
