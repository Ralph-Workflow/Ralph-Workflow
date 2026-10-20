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
Score monotonicity — explicit design resolution (ADR-recorded deviation)
----------------------------------------------------------------------

PRODUCT_CRITERIA Verification §3 requires: "raising any 'working' signal
never lowers ``s`` or speeds up a kill". The spec's literal Sugeno
weighted-average ``s = Σwᵢzᵢ / Σwᵢ`` cannot satisfy that property with the
fixed ``z`` table: adding a second rule whose ``z`` is below the current
score — e.g. R1 (z=1.0) firing alone followed by R2 (z=0.9) starting to
fire — pulls the weighted average DOWN from ``1.0`` to ``0.95``
(reproduced in property tests; see ``wt-19-fix-long-exec-tasks`` task
brief E-005). This is a categorical violation of "raising IO lowers the
score", which the safety-critical kill-curve semantics cannot tolerate.

A second monotonicity failure surfaces when the aggregate is ``max(wᵢ·zᵢ)``
instead of ``Σwᵢzᵢ / Σwᵢ``: R6's condition is ``Repetitive AND NOT IoActive``
and R4's condition is ``CpuBusy AND NOT OutputRecent AND NOT IoActive``.
Under ``max(wᵢ·zᵢ)``, raising io LOWERS R6's contribution (because
``NOT IoActive`` decreases) faster than R2's contribution rises
(z=0.9 vs z=0.2), violating "raising io never lowers s". This is
reproduced by ``test_score_monotone_non_decreasing_with_improved_signals``
on an idle+repetitive baseline with io rising from 0 → 5.

Resolution: the verdict score is the **maximum of per-membership
contributions**:

    s = max( output_recent * (0.2 + 0.8 * (1 - repetitive)),
              io_active * 0.9, tree_active * 0.9, cpu_busy * 0.6,
              repetitive * 0.2, awake * 0.55, 0.0 )

Each working signal contributes monotonically (a larger membership can
only raise its own contribution) and the max across seven monotone
branches is itself monotone. The seven rules, their ``z`` outputs, and
the per-rule firing structure used to identify the strongest rule (and
therefore the verdict_kind — "working", "suspicious", "stalled",
"unknown") are preserved as documented in the §3b spec table.

This is the SINGLE score function in this module. There is no parallel
per-signal scorer: ``_score_from_memberships`` is the unique entry
point consumed by :func:`assess`. The PRODUCT_CRITERIA "Monotonic"
property is encoded verbatim in
``tests/test_liveness_properties.py::test_score_monotone_non_decreasing_with_improved_signals``.

ADR consequence: this deviation is captured as **D11** in the ADR table
that already enumerates D1..D10 in PRODUCT_CRITERIA.md. A future
spec revision that requires the literal Sugeno weighted-average MUST
either accept the monotonicity violation, redefine the rule z-table so
the weighted average is monotone, or add a per-rule
``Σwᵢ<0.05``-guard at every step — none of which are reversible without
re-pinning the §3b wire format. A future spec revision that requires
literal ``max(wᵢ·zᵢ)`` MUST either accept the R6/R4 monotonicity
violation or remove the NOT-clauses over working signals from the rule
conditions.
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


def _score_from_memberships(
    output_recent: float,
    cpu_busy: float,
    io_active: float,
    tree_active: float,
    repetitive: float,
    awake: float,
) -> float:
    """Aggregate the per-rule z-scores into the verdict score ``s``.

    Each rule's per-membership contribution is computed and the verdict
    score is the maximum across them. This is the SINGLE score function
    consumed by :func:`assess`. The aggregation deviates from the
    PRODUCT_CRITERIA "Rules" table in TWO documented places; both are
    required to preserve the "Monotonic" property verbatim — see the
    module docstring "Score monotonicity — explicit design resolution
    (ADR-recorded deviation)" for the full ADR rationale.

    Per-rule mapping:

    * R1: ``output_recent * (0.2 + 0.8 * (1 - repetitive))`` — the output
      score combines the OutputRecent membership with a NOT-Repetitive
      damping term. Spec literal is ``min(output_recent, NOT Repetitive)``
      but that produces a discontinuous contribution that does not survive
      the max aggregate monotonically (see Deviation #2 below).
    * R2: ``io_active * 0.9`` — IoActive membership multiplied by R2's z.
      Spec literal is ``w2 * z2``; for R2 ``w2 = IoActive`` so this is identical.
    * R3: ``tree_active * 0.9`` — TreeActive membership multiplied by R3's z. Same.
    * R4: ``cpu_busy * 0.6`` — CpuBusy multiplied by R4's z. Spec literal has
      ``w4 = min(CpuBusy, NOT OutputRecent, NOT IoActive)``; we use only
      CpuBusy so R4 stays monotone in cpu. The "suspicious" semantics
      still fire through the strongest-rule selector.
    * R5: ``0.0`` — the idle rule never contributes positively; the
      verdict is flagged ``stalled`` when R5 is the only rule firing.
    * R6: ``repetitive * 0.2`` — Repetitive membership multiplied by R6's z. Spec
      literal ``w6 = min(Repetitive, NOT IoActive)`` makes R6's
      contribution decrease when io rises, which violates monotonicity.
    * R7: ``awake * 0.55`` — Awake multiplied by R7's z.

    Deviation #1 (already documented above): the literal Sugeno
    weighted-average ``Σwᵢzᵢ / Σwᵢ`` is replaced by ``max(wᵢ·zᵢ)``;
    R5/R4/R7 keep their semantics via ``verdict_kind``/``strongest_rule``.

    Deviation #2 (this paragraph): the per-rule ``wᵢ`` gating inside
    NOT-clauses is replaced with per-membership contributions, so the
    aggregation is strictly monotone in every working signal. This is
    REQUIRED because R6 (``Repetitive AND NOT IoActive``) and R4
    (``CpuBusy AND NOT OutputRecent AND NOT IoActive``) both have
    NOT-clauses over working signals; under the literal ``max(wᵢ·zᵢ)``
    aggregation, raising io LOWERS R6's contribution (NOT IoActive
    decreases faster than R2's contribution rises), which violates
    PRODUCT_CRITERIA "Monotonic" verbatim. The fix decouples each rule's
    contribution from NOT-clauses over working signals: R6 becomes
    Repetitive-only, R4 becomes CpuBusy-only; the strongest-rule
    selector still tracks which rule fires for the verdict_kind.
    """
    not_repetitive = 1.0 - repetitive
    output_score = output_recent * (0.2 + 0.8 * not_repetitive)
    return max(
        output_score,
        io_active * 0.9,
        tree_active * 0.9,
        cpu_busy * 0.6,
        repetitive * 0.2,
        awake * 0.55,
        0.0,
    )


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
    repetitive = _repetitive(window.output_novelty, config)
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
        score = _score_from_memberships(
            output_recent, cpu_busy, io_active, tree_active, repetitive, awake
        )
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
