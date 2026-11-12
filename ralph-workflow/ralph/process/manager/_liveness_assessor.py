from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Literal

from ralph import timeout_defaults as _td
from ralph.process.manager._suspicion_clock import SuspicionClock

__all__ = [
    "LivenessConfig",
    "LivenessVerdict",
    "LivenessWindow",
    "StallIntegrator",
    "SuspicionClock",
    "VerdictKind",
    "assess",
    "awake",
    "cpu_busy",
    "default_liveness_config",
    "evaluate_rules",
    "io_active",
    "output_recent",
    "repetitive",
    "step_with_assessment",
    "tree_active",
]


@dataclass(frozen=True)
class _LivenessConfig:
    output_recent_min_s: float
    output_recent_max_s: float
    cpu_busy_min: float
    cpu_busy_max: float
    io_kbps_inactive: float
    io_kbps_active: float
    tree_active_threshold: int
    repetitive_max: float
    repetitive_min: float
    awake_per_s_inactive: float
    awake_per_s_active: float
    stall_theta: float
    evidence_window_s: float


@dataclass(frozen=True)
class _LivenessWindow:
    seconds_since_output: float | None
    cpu_ratio: float | None
    io_bytes_per_s: float | None
    child_events: int | None
    output_novelty: float | None
    voluntary_ctx_per_s: float | None
    elapsed_s: float


VerdictKind = Literal["working", "suspicious", "stalled", "unknown"]


@dataclass(frozen=True)
class _LivenessVerdict:
    score: float
    pressure: float
    stall_window_s: float
    strongest_rule: str
    suspicious: bool
    verdict_kind: VerdictKind
    signals: dict[str, float | None]
    elapsed_s: float


LivenessConfig = _LivenessConfig
LivenessWindow = _LivenessWindow
LivenessVerdict = _LivenessVerdict


def output_recent(o: float | None, c: LivenessConfig) -> float:
    if o is None:
        return 0.0
    if o <= c.output_recent_min_s:
        return 1.0
    if o >= c.output_recent_max_s:
        return 0.0
    return (c.output_recent_max_s - o) / (c.output_recent_max_s - c.output_recent_min_s)


_output_recent = output_recent


def cpu_busy(c_val: float | None, c: LivenessConfig) -> float:
    if c_val is None:
        return 0.0
    if c_val <= c.cpu_busy_min:
        return 0.0
    if c_val >= c.cpu_busy_max:
        return 1.0
    return (c_val - c.cpu_busy_min) / (c.cpu_busy_max - c.cpu_busy_min)


_cpu_busy = cpu_busy


def io_active(io: float | None, c: LivenessConfig) -> float:
    if io is None:
        return 0.0
    if io <= c.io_kbps_inactive:
        return 0.0
    if io >= c.io_kbps_active:
        return 1.0
    return (io - c.io_kbps_inactive) / (c.io_kbps_active - c.io_kbps_inactive)


_io_active = io_active


def tree_active(t: int | None, c: LivenessConfig) -> float:
    if t is None:
        return 0.0
    if t <= 0:
        return 0.0
    threshold = c.tree_active_threshold
    if t >= threshold:
        return 1.0
    return float(t) / float(threshold)


_tree_active = tree_active


def repetitive(n: float | None, c: LivenessConfig) -> float:
    if n is None:
        return 0.0
    if n <= c.repetitive_max:
        return 1.0
    if n >= c.repetitive_min:
        return 0.0
    return (c.repetitive_min - n) / (c.repetitive_min - c.repetitive_max)


_repetitive = repetitive


def awake(w: float | None, c: LivenessConfig) -> float:
    if w is None:
        return 0.0
    if w <= c.awake_per_s_inactive:
        return 0.0
    if w >= c.awake_per_s_active:
        return 1.0
    return (w - c.awake_per_s_inactive) / (c.awake_per_s_active - c.awake_per_s_inactive)


_awake = awake


_RULE_WEIGHTS: tuple[float, ...] = (1.0, 0.9, 0.9, 0.6, 0.0, 0.2, 0.55)
_RULE_NAMES: tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5", "R6", "R7")
_SUSPICIOUS_RULES: frozenset[str] = frozenset({"R4", "R7"})


def evaluate_rules(
    output_recent: float,
    cpu_busy: float,
    io_active: float,
    tree_active: float,
    repetitive: float,
    awake: float,
) -> tuple[tuple[float, ...], str]:
    not_output_recent = 1.0 - output_recent
    not_cpu_busy = 1.0 - cpu_busy
    not_io_active = 1.0 - io_active
    not_tree_active = 1.0 - tree_active
    not_repetitive = 1.0 - repetitive
    not_awake = 1.0 - awake

    w1 = min(output_recent, not_repetitive)
    w2 = io_active
    w3 = tree_active
    w4 = min(cpu_busy, not_output_recent, not_io_active)
    w5 = min(not_output_recent, not_cpu_busy, not_io_active, not_tree_active, not_awake)
    w6 = min(repetitive, not_io_active)
    w7 = min(awake, not_cpu_busy, not_io_active, not_output_recent)

    weights = (w1, w2, w3, w4, w5, w6, w7)
    strongest_rule = ""
    strongest_w = -1.0
    for name, w in zip(_RULE_NAMES, weights, strict=True):
        if w > strongest_w:
            strongest_w = w
            strongest_rule = name
    if strongest_w <= 0.0:
        strongest_rule = ""
    return weights, strongest_rule


_evaluate_rules = evaluate_rules


_UNKNOWN_THRESHOLD: float = 0.05

_RULE_Z_VALUES: tuple[float, ...] = (1.0, 0.9, 0.9, 0.6, 0.0, 0.2, 0.55)


def _score_from_rule_weights(weights: tuple[float, ...]) -> float:
    total_w = 0.0
    total_wz = 0.0
    for w, z in zip(weights, _RULE_Z_VALUES, strict=True):
        total_w += w
        total_wz += w * z
    if total_w < _UNKNOWN_THRESHOLD:
        return 0.0
    return total_wz / total_w


def _kind_from_weights(
    weights: tuple[float, ...],
    score: float,
    strongest_rule: str,
) -> VerdictKind:
    total_w = sum(weights)
    if total_w < _UNKNOWN_THRESHOLD:
        return "unknown"
    if weights[4] > 0.0 and score == 0.0:
        return "stalled"
    if strongest_rule in _SUSPICIOUS_RULES:
        return "suspicious"
    return "working"


def assess(window: LivenessWindow, config: LivenessConfig) -> LivenessVerdict:
    output_recent = _output_recent(window.seconds_since_output, config)
    cpu_busy = _cpu_busy(window.cpu_ratio, config)
    io_active = _io_active(window.io_bytes_per_s, config)
    tree_active = _tree_active(window.child_events, config)

    effective_novelty = (
        None
        if window.seconds_since_output is None
        or window.seconds_since_output >= config.evidence_window_s
        or output_recent <= 0.0
        else window.output_novelty
    )
    repetitive = _repetitive(effective_novelty, config)
    awake = _awake(window.voluntary_ctx_per_s, config)

    all_none = (
        window.seconds_since_output is None
        and window.cpu_ratio is None
        and window.io_bytes_per_s is None
        and window.child_events is None
        and window.output_novelty is None
        and window.voluntary_ctx_per_s is None
    )

    if all_none:
        weights: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        strongest_rule = ""
        score = 0.0
    else:
        weights, strongest_rule = _evaluate_rules(
            output_recent, cpu_busy, io_active, tree_active, repetitive, awake
        )
        score = _score_from_rule_weights(weights)
    kind = _kind_from_weights(weights, score, strongest_rule)

    return LivenessVerdict(
        score=score,
        pressure=0.0,
        stall_window_s=0.0,
        strongest_rule=strongest_rule,
        suspicious=(kind == "suspicious"),
        verdict_kind=kind,
        signals={
            "seconds_since_output": window.seconds_since_output,
            "cpu_ratio": window.cpu_ratio,
            "io_bytes_per_s": window.io_bytes_per_s,
            "child_events": float(window.child_events) if window.child_events is not None else None,
            "output_novelty": window.output_novelty,
            "voluntary_ctx_per_s": window.voluntary_ctx_per_s,
        },
        elapsed_s=window.elapsed_s,
    )


def step_with_assessment(
    window: LivenessWindow,
    config: LivenessConfig,
    integrator: StallIntegrator,
    dt: float,
    *,
    stall_window_s: float,
) -> LivenessVerdict:
    raw = assess(window, config)
    new_pressure = integrator.step(raw.score, dt)
    return dataclasses.replace(
        raw,
        pressure=new_pressure,
        stall_window_s=stall_window_s,
    )


class StallIntegrator:
    __slots__ = ("_p", "_theta")

    def __init__(self, *, theta: float) -> None:
        if theta <= 0.0:
            raise ValueError(f"theta must be positive; got {theta!r}")
        self._theta = theta
        self._p = 0.0

    def step(self, verdict_score: float, dt: float) -> float:
        if dt < 0.0:
            raise ValueError(f"dt must be non-negative; got {dt!r}")
        delta = dt * (self._theta - verdict_score) / self._theta
        self._p = max(0.0, self._p + delta)
        return self._p

    def should_kill(self, stall_window_s: float) -> bool:
        return self._p >= stall_window_s

    @property
    def pressure(self) -> float:
        return self._p


def default_liveness_config() -> LivenessConfig:
    return LivenessConfig(
        output_recent_min_s=_td.EXEC_OUTPUT_RECENT_MAX_S,
        output_recent_max_s=_td.EXEC_OUTPUT_RECENT_MIN_S,
        cpu_busy_min=_td.EXEC_CPU_BUSY_MIN,
        cpu_busy_max=_td.EXEC_CPU_BUSY_MAX,
        io_kbps_inactive=_td.EXEC_IO_KBPS_INACTIVE,
        io_kbps_active=_td.EXEC_IO_KBPS_ACTIVE,
        tree_active_threshold=_td.EXEC_TREE_ACTIVE_THRESHOLD,
        repetitive_max=_td.EXEC_REPETITIVE_MAX,
        repetitive_min=_td.EXEC_REPETITIVE_MIN,
        awake_per_s_inactive=_td.EXEC_AWAKE_PER_S_INACTIVE,
        awake_per_s_active=_td.EXEC_AWAKE_PER_S_ACTIVE,
        stall_theta=_td.EXEC_STALL_THETA,
        evidence_window_s=_td.EXEC_LIVENESS_EVIDENCE_WINDOW_S,
    )
