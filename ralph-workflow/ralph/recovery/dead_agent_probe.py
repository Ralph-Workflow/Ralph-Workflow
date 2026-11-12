from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.agent_config import AgentConfig
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


__all__ = ["DEFAULT_MAX_PROBES_PER_RUN", "build_dead_agent_probe_runner"]
