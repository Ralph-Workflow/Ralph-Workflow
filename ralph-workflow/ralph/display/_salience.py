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

ColorDepth = Literal["truecolor", "256", "standard", "none"]

ACCENT_BUDGET_BY_DEPTH: Final[dict[ColorDepth, int]] = {
    "truecolor": 4,
    "256": 3,
    "standard": 2,
    "none": 0,
}

STEADY_STATE_DECAY_FRAMES: Final[int] = 3

_CONTRAST_FLOOR: Final[float] = 4.5

if TYPE_CHECKING:
    from rich.console import Console


def resolve_color_depth(console: Console) -> ColorDepth:
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


def demote_hex(
    resolved_hex: str, *, budget: float = TIER_2_CHROMA_BUDGET, surface_hex: str | None = None
) -> str:
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
    recency = 0 if bid.state_changed else 1
    return (recency, bid.role)


class SalienceAllocator:
    def __init__(self) -> None:
        self._frame_index = 0
        self._steady_frames: dict[str, int] = {}
        self._was_lit: dict[str, bool] = {}

    @property
    def frame_index(self) -> int:
        return self._frame_index

    def allocate_frame(
        self, bids: tuple[RoleBid, ...], *, depth: ColorDepth
    ) -> tuple[AllocationDecision, ...]:
        self._frame_index += 1
        budget = ACCENT_BUDGET_BY_DEPTH[depth]
        decisions: dict[str, AllocationDecision] = {}

        alarm_bids: list[RoleBid] = []
        event_bids: list[RoleBid] = []
        for bid in bids:
            tier = ROLE_FREQUENCY_TIER.get(bid.role, FrequencyTier.FIELD)
            if tier in (FrequencyTier.FIELD, FrequencyTier.STRUCTURE):
                decisions[bid.role] = AllocationDecision(
                    bid.role,
                    tier,
                    True,
                    "non-accent tier: always painted at its own budget",
                    self._frame_index,
                )
                continue
            if tier is FrequencyTier.ALARM:
                alarm_bids.append(bid)
                continue
            event_bids.append(bid)

        for bid in alarm_bids:
            decisions[bid.role] = AllocationDecision(
                bid.role, FrequencyTier.ALARM, True, "alarm: exempt", self._frame_index
            )
            self._steady_frames[bid.role] = 0
            self._was_lit[bid.role] = True

        remaining_budget = max(0, budget - len(alarm_bids))

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
                    carryover_demoted.append(bid)
                else:
                    candidates.append(bid)
            else:
                carryover_demoted.append(bid)

        ranked = sorted(candidates, key=_priority_key)
        lit_bids = ranked[:remaining_budget]
        evicted_bids = ranked[remaining_budget:]

        for bid in lit_bids:
            reason = "state change" if bid.state_changed else "budget slot"
            decisions[bid.role] = AllocationDecision(
                bid.role, FrequencyTier.EVENT, True, reason, self._frame_index
            )
            self._was_lit[bid.role] = True

        for bid in evicted_bids:
            decisions[bid.role] = AllocationDecision(
                bid.role, FrequencyTier.EVENT, False, "budget exhausted: evicted", self._frame_index
            )
            self._was_lit[bid.role] = False

        for bid in carryover_demoted:
            steady = self._steady_frames.get(bid.role, 0)
            reason = (
                "decayed"
                if steady > STEADY_STATE_DECAY_FRAMES
                else "demoted: one-way until state change"
            )
            decisions[bid.role] = AllocationDecision(
                bid.role, FrequencyTier.EVENT, False, reason, self._frame_index
            )
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
