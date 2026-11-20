from __future__ import annotations

from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.agents.registry import AgentRegistry
from ralph.recovery.canary_probe import (
    PROBE_COULD_NOT_RUN_DETAIL,
    PROBE_REASON_BUDGET_EXHAUSTED,
    PROBE_REASON_INTERNAL_ERROR,
    ProbeOutcome,
    ProbeResult,
    run_canary_probe,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.agent_config import AgentConfig
    from ralph.config.models import UnifiedConfig

DEFAULT_MAX_PROBES_PER_RUN = 3
PROBE_REASON_AGENT_RESOLUTION_FAILED: Final[str] = "agent_resolution_failed"
PROBE_REASON_AGENT_UNRESOLVED: Final[str] = "agent_unresolved"
PROBE_REASON_PROBE_NOT_CONFIGURED: Final[str] = "probe_not_configured"
PROBE_REASON_RUNNER_CRASHED: Final[str] = "probe_runner_crashed"


def fail_open_probe_result(reason: str) -> ProbeResult:
    return ProbeResult(
        outcome=ProbeOutcome.INCONCLUSIVE,
        detail=PROBE_COULD_NOT_RUN_DETAIL,
        reason=reason,
    )


def build_dead_agent_probe_runner(
    resolve_agent: Callable[[str], AgentConfig | None],
    probe: Callable[[AgentConfig], ProbeResult],
    *,
    max_probes: int = DEFAULT_MAX_PROBES_PER_RUN,
) -> Callable[[str, str], ProbeResult | None]:
    probes_started = 0

    def run(phase: str, agent: str) -> ProbeResult | None:
        nonlocal probes_started
        if probes_started >= max_probes:
            return fail_open_probe_result(PROBE_REASON_BUDGET_EXHAUSTED)
        try:
            config = resolve_agent(agent)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            logger.debug("dead-agent probe skipped for {}: {}", agent, exc)
            return fail_open_probe_result(PROBE_REASON_AGENT_RESOLUTION_FAILED)
        if config is None:
            return fail_open_probe_result(PROBE_REASON_AGENT_UNRESOLVED)
        probes_started += 1
        try:
            result = probe(config)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            logger.warning("dead-agent probe crashed phase={} agent={}: {}", phase, agent, exc)
            return fail_open_probe_result(PROBE_REASON_INTERNAL_ERROR)
        logger.info(
            "dead-agent probe phase={} agent={} outcome={} detail={} reason={}",
            phase,
            agent,
            result.outcome.value,
            result.detail,
            result.reason,
        )
        return result

    return run


def production_dead_agent_probe_runner(
    config: UnifiedConfig | None,
    *,
    trusted_parent: Path | None = None,
) -> Callable[[str, str], ProbeResult | None] | None:
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
    "PROBE_REASON_AGENT_RESOLUTION_FAILED",
    "PROBE_REASON_AGENT_UNRESOLVED",
    "PROBE_REASON_PROBE_NOT_CONFIGURED",
    "PROBE_REASON_RUNNER_CRASHED",
    "build_dead_agent_probe_runner",
    "fail_open_probe_result",
    "production_dead_agent_probe_runner",
    "run_canary_probe",
]
