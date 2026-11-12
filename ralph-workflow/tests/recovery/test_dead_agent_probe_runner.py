from __future__ import annotations

from ralph.config.models import AgentConfig
from ralph.recovery.canary_probe import ProbeOutcome, ProbeResult
from ralph.recovery.dead_agent_probe import build_dead_agent_probe_runner


def _result(outcome: ProbeOutcome) -> ProbeResult:
    return ProbeResult(outcome=outcome, detail="scripted")


def _resolver(name: str) -> AgentConfig | None:
    return AgentConfig(cmd=name) if name != "unknown" else None


def test_the_runner_resolves_the_agent_and_returns_the_probe_outcome() -> None:
    seen: list[str] = []

    def probe(config: AgentConfig) -> ProbeResult:
        seen.append(config.cmd)
        return _result(ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=2)

    assert runner("development", "claude") is ProbeOutcome.FAILED_REPRODUCING_NO_WORK
    assert seen == ["claude"]


def test_the_runner_stops_probing_once_the_per_run_budget_is_spent() -> None:
    calls: list[str] = []

    def probe(config: AgentConfig) -> ProbeResult:
        calls.append(config.cmd)
        return _result(ProbeOutcome.PASSED)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=2)

    outcomes = [runner("development", "claude") for _ in range(4)]

    assert len(calls) == 2
    assert outcomes[:2] == [ProbeOutcome.PASSED, ProbeOutcome.PASSED]
    assert outcomes[2:] == [None, None]


def test_an_unresolvable_agent_yields_no_outcome_and_spends_no_budget() -> None:
    calls: list[str] = []

    def probe(config: AgentConfig) -> ProbeResult:
        calls.append(config.cmd)
        return _result(ProbeOutcome.PASSED)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=1)

    assert runner("development", "unknown") is None
    assert runner("development", "claude") is ProbeOutcome.PASSED
    assert calls == ["claude"]


def test_a_crashing_probe_yields_no_outcome() -> None:
    def probe(config: AgentConfig) -> ProbeResult:
        raise RuntimeError(config.cmd)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=1)

    assert runner("development", "claude") is None
