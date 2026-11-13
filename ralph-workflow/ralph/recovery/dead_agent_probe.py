from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.registry import AgentRegistry
from ralph.recovery.canary_probe import run_canary_probe

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.agent_config import AgentConfig
    from ralph.config.models import UnifiedConfig
    from ralph.recovery.canary_probe import ProbeOutcome, ProbeResult

DEFAULT_MAX_PROBES_PER_RUN = 3


def build_dead_agent_probe_runner(
    resolve_agent: Callable[[str], AgentConfig | None],
    probe: Callable[[AgentConfig], ProbeResult],
    *,
    max_probes: int = DEFAULT_MAX_PROBES_PER_RUN,
) -> Callable[[str, str], ProbeOutcome | None]:
    probes_started = 0

    def run(phase: str, agent: str) -> ProbeOutcome | None:
        nonlocal probes_started
        if probes_started >= max_probes:
            return None
        try:
            config = resolve_agent(agent)
        except Exception as exc:
            logger.debug("dead-agent probe skipped for {}: {}", agent, exc)
            return None
        if config is None:
            return None
        probes_started += 1
        try:
            return probe(config).outcome
        except Exception as exc:
            logger.warning("dead-agent probe crashed phase={} agent={}: {}", phase, agent, exc)
            return None

    return run


def production_dead_agent_probe_runner(
    config: UnifiedConfig | None,
    *,
    trusted_parent: Path | None = None,
) -> Callable[[str, str], ProbeOutcome | None] | None:
    if config is None:
        return None

    def resolve_agent(name: str) -> AgentConfig | None:
        return AgentRegistry.from_config(config).get(name)

    return build_dead_agent_probe_runner(
        resolve_agent,
        lambda agent_config: run_canary_probe(
            agent_config,
            general_config=config.general,
            trusted_parent=trusted_parent,
        ),
    )


__all__ = [
    "DEFAULT_MAX_PROBES_PER_RUN",
    "build_dead_agent_probe_runner",
    "production_dead_agent_probe_runner",
    "run_canary_probe",
]
