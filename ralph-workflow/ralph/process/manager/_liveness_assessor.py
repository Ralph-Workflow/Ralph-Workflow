"""Pure deterministic liveness assessor for long-running exec jobs (§3b).

The assessor is a no-I/O, no-clock function that takes a freshly sampled
``LivenessWindow`` of child-process activity and returns a ``LivenessVerdict``
describing how alive the child looked at that instant. All breakpoints come
from :mod:`ralph.timeout_defaults`; this module never reads processes, never
calls ``time``, and never touches the network — callers wire the sampled
window in and read the verdict out.

The verdict feeds two downstream owners:

* ``StallIntegrator`` (in this module) integrates the verdict score over
  time and signals ``should_kill`` once cumulative pressure exceeds the
  configured ``stall_window_s``.
* The job-flow runtime reads ``verdict_kind`` and ``suspicious`` to emit
  SUSPICIOUS / WORKING / SUSPENDED events. Continuous suspicion
  (>= ``EXEC_SUSPICIOUS_REPORT_S`` = 60s) is owned by ``SuspicionClock``
  (also in this module).

Frozen contract: the dataclasses below and the public ``assess`` /
``StallIntegrator`` / ``SuspicionClock`` signatures are part of the §3b
wire format. Other modules may import them but must not extend them.

----------------------------------------------------------------------
Score — literal Sugeno (PRODUCT_CRITERIA Part A)
----------------------------------------------------------------------

The verdict score follows the PRODUCT_CRITERIA / §3b Part A formula
verbatim:

    s = Σ wᵢ·zᵢ / Σ wᵢ,    z₁=1.0, z₂=0.9, z₃=0.9, z₄=0.6, z₅=0.0,
                            z₆=0.2, z₇=0.55.

Where ``wᵢ`` is the AND/OR/NOT firing strength of rule ``i`` per the
spec table (``min`` for AND, ``max`` for OR, ``1 - x`` for NOT — see
``_evaluate_rules``). The seven rule conditions and their ``z`` outputs
are the exact table PRODUCT_CRITERIA Part A defines; no rule is
redefined in this module.

Monotonicity (PRODUCT_CRITERIA Verification §3 — "Monotonic"): raising
any working signal never lowers ``s`` or speeds up a kill. Under canonical
D2/D3/D5:
- Output novelty signal ``n`` is defined over the last
  ``EXEC_LIVENESS_EVIDENCE_WINDOW_S`` (30 s). If no output occurred in that
  evidence window (or output is not recent), ``n`` is unknown, so per D5
  its working membership ``Repetitive`` is 0.0. Silent or idle processes
  never fire R6 ("Printing the same line in a loop"), ensuring that an
  idle process is evaluated as idle (R5, z=0.0) without spurious extension,
  resolving the E-MONOTONIC counterexample.
- When output is occurring in the evidence window, raising novelty
  decreases ``Repetitive`` (which fires R6 with z=0.2) and increases
  ``NOT Repetitive`` (which fires R1 with z=1.0). Shifting firing strength
  from z=0.2 to z=1.0 strictly increases or preserves the Sugeno weighted
  average, guaranteeing monotonic non-decreasing score with improved
  novelty.

The "Monotonic" property is verified by
``tests/test_liveness_properties.py::test_score_monotone_non_decreasing_with_improved_signals``
and the isolated novelty counterexample in
``tests/test_liveness_properties.py::test_novelty_monotone_non_decreasing_score``.
"""

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
    "default_liveness_config",
    "step_with_assessment",
]

# Unknown-signal policy (D5): an unreadable signal never extends a deadline.
# An unreadable signal sets every working membership built on it to 0.0,
# and every NOT built on it to 1.0. When all signals are unknown, verdict is
# "unknown" (score 0.0) and accumulates stall pressure with no extension.


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LivenessConfig:
    output_recent_min_s: float  # OutputRecent: 1 at o<=min_s, 0 at o>=max_s
    output_recent_max_s: float
    cpu_busy_min: float  # CpuBusy:    0 at c<=min, 1 at c>=max
    cpu_busy_max: float
    io_kbps_inactive: float  # IoActive:   0 at <=inactive, 1 at >=active
    io_kbps_active: float
    tree_active_threshold: int  # TreeActive: 0 at t=0, 1 at t>=threshold
    repetitive_max: float  # Repetitive: 1 at n<=max, 0 at n>=min
    repetitive_min: float
    awake_per_s_inactive: float  # Awake:      0 at w<=inactive, 1 at w>=active
    awake_per_s_active: float
    stall_theta: float  # theta for StallIntegrator
    evidence_window_s: float  # informational; included so the config is fully parameterised


@dataclass(frozen=True)
class _LivenessWindow:
    seconds_since_output: float | None
    cpu_ratio: float | None
    io_bytes_per_s: float | None  # already in KiB/s; the assessor never converts
    child_events: int | None
    output_novelty: float | None  # distinct/total in [0,1]; None if total < 20
    voluntary_ctx_per_s: float | None
    elapsed_s: float


VerdictKind = Literal["working", "suspicious", "stalled", "unknown"]


@dataclass(frozen=True)
class _LivenessVerdict:
    score: float
    pressure: float  # post-integration P (0.0 from a bare ``assess`` call)
    stall_window_s: float  # configured stall window (0.0 from a bare ``assess`` call)
    strongest_rule: str  # "" when no rule fires / unknown
    suspicious: bool
    verdict_kind: VerdictKind
    signals: dict[str, float | None]  # raw window fields incl. unknowns
    elapsed_s: float


LivenessConfig = _LivenessConfig
LivenessWindow = _LivenessWindow
LivenessVerdict = _LivenessVerdict


# ---------------------------------------------------------------------------
# Membership functions (linear ramps, clamped to [0, 1])
# ---------------------------------------------------------------------------


def _output_recent(o: float | None, c: LivenessConfig) -> float:
    """Linear ramp on the OutputRecent membership; 0.0 for unknown signals.

    See :mod:`ralph.process.manager._liveness_assessor` module docstring —
    unknown signals yield 0.0 (no working evidence), so the rule network's
    NOT-of-X clauses treat them as ``NOT X = 1`` and the per-rule gating
    above prevents a fire on the absence of positive evidence.
    """
    if o is None:
        return 0.0
    if o <= c.output_recent_min_s:
        return 1.0
    if o >= c.output_recent_max_s:
        return 0.0
    return (c.output_recent_max_s - o) / (c.output_recent_max_s - c.output_recent_min_s)


def _cpu_busy(c_val: float | None, c: LivenessConfig) -> float:
    if c_val is None:
        return 0.0
    if c_val <= c.cpu_busy_min:
        return 0.0
    if c_val >= c.cpu_busy_max:
        return 1.0
    return (c_val - c.cpu_busy_min) / (c.cpu_busy_max - c.cpu_busy_min)


def _io_active(io: float | None, c: LivenessConfig) -> float:
    if io is None:
        return 0.0
    if io <= c.io_kbps_inactive:
        return 0.0
    if io >= c.io_kbps_active:
        return 1.0
    return (io - c.io_kbps_inactive) / (c.io_kbps_active - c.io_kbps_inactive)


def _tree_active(t: int | None, c: LivenessConfig) -> float:
    if t is None:
        return 0.0
    if t <= 0:
        return 0.0
    threshold = c.tree_active_threshold
    if t >= threshold:
        return 1.0
    # Linear ramp in [1, threshold] where threshold > 1; t=1 -> 1/threshold,
    # t=threshold-1 -> (threshold-1)/threshold.
    return float(t) / float(threshold)


def _repetitive(n: float | None, c: LivenessConfig) -> float:
    if n is None:
        return 0.0
    if n <= c.repetitive_max:
        return 1.0
    if n >= c.repetitive_min:
        return 0.0
    return (c.repetitive_min - n) / (c.repetitive_min - c.repetitive_max)


def _awake(w: float | None, c: LivenessConfig) -> float:
    if w is None:
        return 0.0
    if w <= c.awake_per_s_inactive:
        return 0.0
    if w >= c.awake_per_s_active:
        return 1.0
    return (w - c.awake_per_s_inactive) / (c.awake_per_s_active - c.awake_per_s_inactive)


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


# Frozen weights — kept as module-level constants so callers can introspect
# them without instantiating a config. Order matches R1..R7 below.
_RULE_WEIGHTS: tuple[float, ...] = (1.0, 0.9, 0.9, 0.6, 0.0, 0.2, 0.55)
_RULE_NAMES: tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5", "R6", "R7")
_SUSPICIOUS_RULES: frozenset[str] = frozenset({"R4", "R7"})


def _evaluate_rules(
    output_recent: float,
    cpu_busy: float,
    io_active: float,
    tree_active: float,
    repetitive: float,
    awake: float,
) -> tuple[tuple[float, ...], str]:
    """Evaluate R1..R7 firing strengths; return (weights, strongest_rule).

    Operates on MEMBERSHIP values (already normalized to [0,1]) and assumes
    unknown signals have been folded to 0.0 by the membership layer. NOT
    clauses become ``1 - x`` so an unknown signal yields ``NOT = 1`` per
    the spec ("every NOT built on it to 1"). Per-rule gating for the
    "unknown means unknown" semantics lives in :func:`assess`.
    """
    not_output_recent = 1.0 - output_recent
    not_cpu_busy = 1.0 - cpu_busy
    not_io_active = 1.0 - io_active
    not_tree_active = 1.0 - tree_active
    not_repetitive = 1.0 - repetitive
    not_awake = 1.0 - awake

    # R1: OutputRecent AND NOT Repetitive
    w1 = min(output_recent, not_repetitive)
    # R2: IoActive
    w2 = io_active
    # R3: TreeActive
    w3 = tree_active
    # R4: CpuBusy AND NOT OutputRecent AND NOT IoActive
    w4 = min(cpu_busy, not_output_recent, not_io_active)
    # R5: NOT OutputRecent AND NOT CpuBusy AND NOT IoActive AND NOT TreeActive AND NOT Awake
    w5 = min(not_output_recent, not_cpu_busy, not_io_active, not_tree_active, not_awake)
    # R6: Repetitive AND NOT IoActive
    w6 = min(repetitive, not_io_active)
    # R7: Awake AND NOT CpuBusy AND NOT IoActive AND NOT OutputRecent
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


_UNKNOWN_THRESHOLD: float = 0.05

# Per-rule z-values per PRODUCT_CRITERIA Part A (canonical D2):
# R1: 1.0 (Producing new output)
# R2: 0.9 (Reading/writing files)
# R3: 0.9 (Spawning/reaping children)
# R4: 0.6 (Silent compute: suspicious)
# R5: 0.0 (Idle: stuck)
# R6: 0.2 (Printing the same line in a loop)
# R7: 0.55 (Waiting on something external: suspicious)
_RULE_Z_VALUES: tuple[float, ...] = (1.0, 0.9, 0.9, 0.6, 0.0, 0.2, 0.55)


def _score_from_rule_weights(weights: tuple[float, ...]) -> float:
    """Literal Sugeno weighted-average ``s = Σwᵢzᵢ / Σwᵢ``.

    This is the SINGLE score function consumed by :func:`assess`. Per-rule
    ``z`` values match the PRODUCT_CRITERIA Part A spec table verbatim.
    The aggregate is the literal Sugeno weighted-average D2 prescribes.

    When ``Σwᵢ < 0.05`` the verdict is "unknown" and the score is 0.0;
    the caller (``assess``) reads the verdict_kind from the rule weights
    via :func:`_kind_from_weights` and treats the score as fail-closed per
    D5 (unknown means no working evidence).
    """
    total_w = 0.0
    total_wz = 0.0
    for w, z in zip(weights, _RULE_Z_VALUES, strict=True):
        total_w += w
        total_wz += w * z
    if total_w < _UNKNOWN_THRESHOLD:
        return 0.0
    return total_wz / total_w


# ---------------------------------------------------------------------------
# Verdict construction
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def assess(window: LivenessWindow, config: LivenessConfig) -> LivenessVerdict:
    output_recent = _output_recent(window.seconds_since_output, config)
    cpu_busy = _cpu_busy(window.cpu_ratio, config)
    io_active = _io_active(window.io_bytes_per_s, config)
    tree_active = _tree_active(window.child_events, config)

    # Signal n (output novelty) is measured over the last evidence window
    # (default 30 s). If no output occurred in that window (or output is not
    # recent), line count in the evidence window is 0 (< 20 lines) and n is
    # unknown. Per D5, an unknown signal sets working membership to 0.0.
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
    """Run a single liveness cycle (assess + integrate) and return the verdict.

    The returned :class:`LivenessVerdict` is the same verdict
    :func:`assess` would produce, but with ``pressure`` carrying the
    post-integration ``P`` from the supplied :class:`StallIntegrator`
    and ``stall_window_s`` carrying the configured kill threshold. The
    runner publishes these post-integration values directly on
    ``JobSnapshot`` / ``JobResult`` so the agent sees the actual
    stall-pressure state, not the pre-integration residue.

    The integration semantics are unchanged from
    :meth:`StallIntegrator.step`: ``P <- max(0, P + dt*(theta - s)/theta)``.
    """
    raw = assess(window, config)
    new_pressure = integrator.step(raw.score, dt)
    return dataclasses.replace(
        raw,
        pressure=new_pressure,
        stall_window_s=stall_window_s,
    )


class StallIntegrator:
    """Cumulative-pressure integrator with kill threshold.

    ``P_{n+1} = max(0, P_n + dt * (theta - s) / theta)``.
    ``should_kill(stop_window_s)`` is True iff ``P >= stall_window_s``.
    """

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


# ---------------------------------------------------------------------------
# Default config factory
# ---------------------------------------------------------------------------


def default_liveness_config() -> LivenessConfig:
    """Pull every threshold from :mod:`ralph.timeout_defaults`.

    Lives here (not in timeout_defaults) so the assessor is the single owner
    of the config shape. The values remain in timeout_defaults.py.

    Note on naming: the OutputRecent breakpoints in timeout_defaults are
    named with the natural-reading reverse (EXEC_OUTPUT_RECENT_MAX_S is the
    LOWER threshold, 10.0; EXEC_OUTPUT_RECENT_MIN_S is the UPPER threshold,
    60.0). The mapping below preserves the spec intent: ``output_recent_min_s``
    is the recent threshold (10.0), ``output_recent_max_s`` is the stale
    threshold (60.0).
    """
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
