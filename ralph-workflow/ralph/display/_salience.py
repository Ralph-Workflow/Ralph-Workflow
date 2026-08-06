"""Deterministic per-frame salience allocator (PLAN.md Section G).

Sections A-E treat the palette as a *lookup*: a role resolves to one
pigment for the lifetime of the surface. This module sits between that
resolution and Rich style emission, choosing -- for each rendered frame --
which already-solved pigment a role actually gets: full anchor chroma
("lit") or demoted toward its E-1 tier-2 structural chroma budget. It never
invents a colour; it only chooses among the ones :mod:`ralph.display._palette`
already solved (G-1).

Only tier-3 (event) and tier-4 (alarm) roles are part of the accent
competition this module governs. Tier-1 (field) and tier-2 (structure)
roles are not scarce -- they already render at their own fixed, low chroma
budget every time (E-1) -- so they are always reported "lit" and pass
through unaffected.

**Hysteresis simplification (G-7).** G-7 allows re-promotion either on a
real state change *or* on "a change in the competing set large enough to
clear a stated margin". This implementation only re-promotes on a real
state change: once a role is demoted (by decay or by losing the budget
to higher-priority contenders), it stays demoted until its own underlying
state changes, never merely because contention eased. This is the
strictly stronger of the two permitted conditions -- a role's lit/demoted
status can only ever change in response to a role's own state changing,
which forecloses flicker entirely (two roles trading a budget slot back
and forth is structurally impossible, not just discouraged).

**Determinism (G-6).** ``SalienceAllocator`` carries state (steady-frame
counters, last-lit bits) purely to implement G-4 decay and G-7 hysteresis
across a *sequence* of frames -- there is no wall-clock read and no I/O.
Replaying an identical sequence of :meth:`SalienceAllocator.allocate_frame`
calls against a freshly constructed instance always produces
byte-identical decisions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from ralph.display._frequency_tier import FrequencyTier
from ralph.display._palette import (
    ROLE_FREQUENCY_TIER,
    TIER_2_CHROMA_BUDGET,
    hex_to_rgb,
    oklab_to_oklch,
    oklab_to_rgb,
    oklch_to_oklab,
    rgb_to_hex,
    rgb_to_oklab,
)
from ralph.display._salience_bid import RoleBid
from ralph.display._salience_decision import AllocationDecision

#: The colour depths G-8's budget is derived for. ``"none"`` covers
#: NO_COLOR / TERM=dumb / non-tty / forced-ASCII -- the allocator is a
#: no-op there (B-6): every bid is reported lit so no demotion logic ever
#: touches an output that carries no colour anyway.
ColorDepth = Literal["truecolor", "256", "standard", "none"]

#: G-8: the accent budget -- how many tier-3/4 roles may be simultaneously
#: lit (full anchor chroma) in one frame -- derived from how many roles can
#: actually stay pairwise-separable at that colour depth (C-3/C-5). Fewer
#: separable pigments at a coarser depth means a smaller budget: truecolor
#: affords the most concurrent accents, the 256-cube fewer, and the
#: 16-colour ANSI ("standard") set fewer still. ``"none"`` carries no
#: budget concept at all -- see the module docstring.
ACCENT_BUDGET_BY_DEPTH: Final[dict[ColorDepth, int]] = {
    "truecolor": 4,
    "256": 3,
    "standard": 2,
    "none": 0,
}

#: G-4: the number of consecutive frames a role may stay lit without an
#: underlying state change before it decays one rung.
STEADY_STATE_DECAY_FRAMES: Final[int] = 3

#: C-1's contrast floor, restated here so demote_hex's safety check does not
#: import a bare literal from _palette.py's own module-level constant name.
_CONTRAST_FLOOR: Final[float] = 4.5

if TYPE_CHECKING:
    from rich.console import Console


def resolve_color_depth(console: Console) -> ColorDepth:
    """G-8: map a live Rich ``Console`` to the allocator's ``ColorDepth``.

    ``console.no_color`` covers NO_COLOR/forced-no-colour runs directly.
    ``console.color_system`` is Rich's own already-resolved system name
    (``"standard"``/``"256"``/``"truecolor"``/``"windows"``) or ``None``
    for a destination Rich has determined carries no colour at all (a
    non-tty stream with no ``FORCE_COLOR``, or ``TERM=dumb`` -- B-6). Rich
    resolves ``"windows"`` only on legacy pre-VT100 Windows consoles, which
    this project does not target as a distinct case -- it is treated as
    the truecolor-equivalent upper band.
    """
    if console.no_color:
        return "none"
    system = console.color_system
    if system is None:
        return "none"
    if system == "standard":
        return "standard"
    if system == "256":
        return "256"
    return "truecolor"


def demote_hex(resolved_hex: str, *, budget: float = TIER_2_CHROMA_BUDGET, surface_hex: str | None = None) -> str:
    """G-2: move ``resolved_hex`` toward ``budget`` chroma at unchanged
    lightness and hue -- a chroma ladder, never a contrast or meaning
    change. If capping chroma would drop under the C-1 4.5:1 floor against
    ``surface_hex`` (when given), the original hex is returned unchanged
    instead: a demoted role never drops below its own accessibility floor,
    even for one frame.
    """
    lab_l, a, b_lab = rgb_to_oklab(*hex_to_rgb(resolved_hex))
    l_val, chroma, hue = oklab_to_oklch(lab_l, a, b_lab)
    if chroma <= budget:
        return resolved_hex.upper()
    _l2, new_a, new_b = oklch_to_oklab(l_val, budget, hue)
    r, g, b = oklab_to_rgb(l_val, new_a, new_b)
    demoted = rgb_to_hex(r, g, b)
    if surface_hex is not None:
        from ralph.display._palette import contrast_ratio

        if contrast_ratio(demoted, surface_hex) < _CONTRAST_FLOOR:
            return resolved_hex.upper()
    return demoted


def _priority_key(bid: RoleBid) -> tuple[int, str]:
    """G-3: recency (a state change this frame outranks a steady one) then
    a stable role-name ordering -- never dict/set iteration order."""
    recency = 0 if bid.state_changed else 1
    return (recency, bid.role)


class SalienceAllocator:
    """G-1..G-9: deterministic, frame-indexed per-frame accent allocator.

    See the module docstring for the determinism (G-6) and hysteresis
    (G-7) guarantees this class provides.
    """

    def __init__(self) -> None:
        self._frame_index = 0
        #: role -> consecutive frames continuously lit without a state
        #: change (G-4's decay counter). Absent means never lit yet.
        #: bounded-accumulator-ok: keyed only by role names, a fixed small
        #: set (ROLE_FREQUENCY_TIER's declared roles); never grows with the
        #: number of frames allocated.
        self._steady_frames: dict[str, int] = {}  # bounded-accumulator-ok: keyed only by ROLE_FREQUENCY_TIER's fixed small role-name set
        #: role -> whether it was lit in the immediately preceding frame
        #: (G-7's hysteresis memory). Absent means never seen yet.
        #: bounded-accumulator-ok: keyed only by role names, a fixed small
        #: set (ROLE_FREQUENCY_TIER's declared roles); never grows with the
        #: number of frames allocated.
        self._was_lit: dict[str, bool] = {}  # bounded-accumulator-ok: keyed only by ROLE_FREQUENCY_TIER's fixed small role-name set

    @property
    def frame_index(self) -> int:
        """The number of frames allocated so far (G-6: frame-indexed, not clock-based)."""
        return self._frame_index

    def allocate_frame(
        self, bids: tuple[RoleBid, ...], *, depth: ColorDepth
    ) -> tuple[AllocationDecision, ...]:
        """Allocate the accent budget for one frame (G-1..G-9).

        Returns one :class:`AllocationDecision` per bid, in the same order
        ``bids`` was given.
        """
        self._frame_index += 1
        budget = ACCENT_BUDGET_BY_DEPTH[depth]
        decisions: dict[str, AllocationDecision] = {}

        alarm_bids: list[RoleBid] = []
        event_bids: list[RoleBid] = []
        for bid in bids:
            tier = ROLE_FREQUENCY_TIER.get(bid.role, FrequencyTier.FIELD)
            if tier in (FrequencyTier.FIELD, FrequencyTier.STRUCTURE):
                decisions[bid.role] = AllocationDecision(
                    bid.role, tier, True, "non-accent tier: always painted at its own budget"
                )
                continue
            if tier is FrequencyTier.ALARM:
                alarm_bids.append(bid)
                continue
            event_bids.append(bid)

        # G-5: alarms are never demoted, never decay, and always count as
        # lit regardless of budget -- the budget is instead balanced below
        # by evicting the lowest-priority tier-3 (event) contender.
        for bid in alarm_bids:
            decisions[bid.role] = AllocationDecision(bid.role, FrequencyTier.ALARM, True, "alarm: exempt")
            self._steady_frames[bid.role] = 0
            self._was_lit[bid.role] = True

        remaining_budget = max(0, budget - len(alarm_bids))

        # G-4 decay bookkeeping + G-7 one-way demotion candidacy.
        candidates: list[RoleBid] = []
        carryover_demoted: list[RoleBid] = []
        for bid in event_bids:
            was_lit = self._was_lit.get(bid.role, False)
            if bid.state_changed:
                self._steady_frames[bid.role] = 0
                candidates.append(bid)
                continue
            if was_lit:
                steady = self._steady_frames.get(bid.role, 0) + 1
                self._steady_frames[bid.role] = steady
                if steady > STEADY_STATE_DECAY_FRAMES:
                    # G-4: decay. One-way (G-7) until a real state change.
                    carryover_demoted.append(bid)
                else:
                    candidates.append(bid)
            else:
                # G-7: already demoted, no state change -- stays demoted.
                carryover_demoted.append(bid)

        ranked = sorted(candidates, key=_priority_key)
        lit_bids = ranked[:remaining_budget]
        evicted_bids = ranked[remaining_budget:]

        for bid in lit_bids:
            reason = "state change" if bid.state_changed else "budget slot"
            decisions[bid.role] = AllocationDecision(bid.role, FrequencyTier.EVENT, True, reason)
            self._was_lit[bid.role] = True

        for bid in evicted_bids:
            decisions[bid.role] = AllocationDecision(
                bid.role, FrequencyTier.EVENT, False, "budget exhausted: evicted"
            )
            self._was_lit[bid.role] = False

        for bid in carryover_demoted:
            steady = self._steady_frames.get(bid.role, 0)
            reason = "decayed" if steady > STEADY_STATE_DECAY_FRAMES else "demoted: one-way until state change"
            decisions[bid.role] = AllocationDecision(bid.role, FrequencyTier.EVENT, False, reason)
            self._was_lit[bid.role] = False

        return tuple(decisions[bid.role] for bid in bids)


__all__ = [
    "ACCENT_BUDGET_BY_DEPTH",
    "STEADY_STATE_DECAY_FRAMES",
    "AllocationDecision",
    "ColorDepth",
    "RoleBid",
    "SalienceAllocator",
    "demote_hex",
    "resolve_color_depth",
]
