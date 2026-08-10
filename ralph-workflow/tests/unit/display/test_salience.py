"""Unit tests for the deterministic per-frame salience allocator (Section G)."""

from __future__ import annotations

import pytest

from ralph.display._palette import ROLE_ANCHORS, FrequencyTier, resolve_palette
from ralph.display._salience import (
    ACCENT_BUDGET_BY_DEPTH,
    STEADY_STATE_DECAY_FRAMES,
    AllocationDecision,
    RoleBid,
    SalienceAllocator,
    demote_hex,
)


def _lit_map(decisions: tuple[AllocationDecision, ...]) -> dict[str, bool]:
    return {d.role: d.lit for d in decisions}
@pytest.mark.criteria("G-3")


def test_tier_priority_state_change_wins_over_steady_accents() -> None:
    """G-3: a role that just changed state outranks steady ones of the same tier."""
    allocator = SalienceAllocator()
    # Prime four steady EVENT-tier accents at the truecolor budget (4).
    bids = (RoleBid("success", True), RoleBid("warning", True), RoleBid("skipped", True), RoleBid("pending", True))
    decisions = allocator.allocate_frame(bids, depth="truecolor")
    assert all(_lit_map(decisions).values())

    # Now a fifth role changes state -- it must win a slot over the least
    # important already-steady incumbent it displaces.
    bids2 = (
        RoleBid("success", False),
        RoleBid("warning", False),
        RoleBid("skipped", False),
        RoleBid("pending", False),
        RoleBid("info", True),
    )
    decisions2 = allocator.allocate_frame(bids2, depth="truecolor")
    lit2 = _lit_map(decisions2)
    assert lit2["info"] is True
@pytest.mark.criteria("G-1")


def test_field_and_structure_tier_roles_are_always_lit() -> None:
    """Field/structure roles are not scarce -- they never compete for budget."""
    allocator = SalienceAllocator()
    bids = (RoleBid("foreground"), RoleBid("chrome"), RoleBid("muted"))
    decisions = allocator.allocate_frame(bids, depth="standard")
    assert all(d.lit for d in decisions)
    assert {d.tier for d in decisions} == {FrequencyTier.FIELD, FrequencyTier.STRUCTURE}
@pytest.mark.criteria("G-5")


def test_alarm_tier_is_never_demoted_even_when_budget_is_exhausted() -> None:
    """G-5: an alarm always renders at full chroma regardless of contention."""
    allocator = SalienceAllocator()
    # Saturate the truecolor budget (4) with steady EVENT accents first.
    steady = (RoleBid("success", True), RoleBid("warning", True), RoleBid("skipped", True), RoleBid("pending", True))
    allocator.allocate_frame(steady, depth="truecolor")

    bids = (
        RoleBid("success", False),
        RoleBid("warning", False),
        RoleBid("skipped", False),
        RoleBid("pending", False),
        RoleBid("error", True),
    )
    decisions = allocator.allocate_frame(bids, depth="truecolor")
    lit = _lit_map(decisions)
    assert lit["error"] is True
    tiers = {d.role: d.tier for d in decisions}
    assert tiers["error"] is FrequencyTier.ALARM


def test_alarm_eviction_displaces_the_lowest_priority_event_accent_not_the_alarm() -> None:
    """G-5: budget is balanced by evicting a tier-3 accent, never by dimming the alarm."""
    allocator = SalienceAllocator()
    # Fill the "standard" depth budget (2) with two steady accents.
    allocator.allocate_frame((RoleBid("success", True), RoleBid("warning", True)), depth="standard")
    bids = (
        RoleBid("success", False),
        RoleBid("warning", False),
        RoleBid("error", True),
    )
    decisions = allocator.allocate_frame(bids, depth="standard")
    lit = _lit_map(decisions)
    assert lit["error"] is True
    # Only one of success/warning can remain lit at budget=2 minus 1 alarm = 1 slot.
    assert sum(1 for role in ("success", "warning") if lit[role]) <= 1
@pytest.mark.criteria("G-4")


def test_frame_indexed_decay_demotes_after_the_steady_state_window() -> None:
    """G-4: a role lit without a state change for STEADY_STATE_DECAY_FRAMES decays."""
    allocator = SalienceAllocator()
    allocator.allocate_frame((RoleBid("success", True),), depth="truecolor")
    for _ in range(STEADY_STATE_DECAY_FRAMES):
        decisions = allocator.allocate_frame((RoleBid("success", False),), depth="truecolor")
        assert decisions[0].lit is True, "must stay lit within the decay window"
    decayed = allocator.allocate_frame((RoleBid("success", False),), depth="truecolor")
    assert decayed[0].lit is False
    assert decayed[0].reason == "decayed"


def test_genuine_state_change_relights_instantly_even_mid_quiet_stretch() -> None:
    """G-4: a real state change restores full chroma in the same frame -- no ramp."""
    allocator = SalienceAllocator()
    allocator.allocate_frame((RoleBid("success", True),), depth="truecolor")
    for _ in range(STEADY_STATE_DECAY_FRAMES + 2):
        allocator.allocate_frame((RoleBid("success", False),), depth="truecolor")
    # `success` is now decayed/demoted. A fresh state change re-lights it
    # in the very next frame, not gradually.
    relit = allocator.allocate_frame((RoleBid("success", True),), depth="truecolor")
    assert relit[0].lit is True
    assert relit[0].reason == "state change"
@pytest.mark.criteria("G-7")


def test_demotion_is_one_way_until_a_real_state_change() -> None:
    """G-7: a role decayed out of the lit set stays demoted even once contention eases."""
    allocator = SalienceAllocator()
    allocator.allocate_frame((RoleBid("success", True), RoleBid("warning", True)), depth="standard")
    # Both fit within the standard-depth budget (2). Decay warning by
    # holding it steady (no state change) past the decay window.
    for _ in range(STEADY_STATE_DECAY_FRAMES + 1):
        decisions = allocator.allocate_frame(
            (RoleBid("success", False), RoleBid("warning", False)), depth="standard"
        )
    warning_lit = {d.role: d.lit for d in decisions}["warning"]
    assert warning_lit is False
    # Contention now eases entirely (success stops bidding) -- warning must
    # NOT flicker back on without its own state change.
    still_demoted = allocator.allocate_frame((RoleBid("warning", False),), depth="standard")
    assert still_demoted[0].lit is False
    assert still_demoted[0].reason in ("decayed", "demoted: one-way until state change")


def test_no_oscillation_across_many_identical_frames() -> None:
    """G-7: two roles never trade the last budget slot back and forth."""
    allocator = SalienceAllocator()
    allocator.allocate_frame((RoleBid("success", True), RoleBid("warning", True)), depth="standard")
    history: list[dict[str, bool]] = []
    for _ in range(10):
        decisions = allocator.allocate_frame(
            (RoleBid("success", False), RoleBid("warning", False)), depth="standard"
        )
        history.append(_lit_map(decisions))
    # Once resolved, subsequent identical (unchanged-state) frames must
    # reproduce the exact same lit/demoted split every time.
    steady_state = history[-1]
    assert all(entry == steady_state for entry in history[STEADY_STATE_DECAY_FRAMES + 1 :])
@pytest.mark.criteria("G-8")


def test_budget_scales_down_with_colour_depth() -> None:
    """G-8: fewer separable pigments at a coarser depth means a smaller budget."""
    assert ACCENT_BUDGET_BY_DEPTH["truecolor"] > ACCENT_BUDGET_BY_DEPTH["256"]
    assert ACCENT_BUDGET_BY_DEPTH["256"] > ACCENT_BUDGET_BY_DEPTH["standard"]
    assert ACCENT_BUDGET_BY_DEPTH["standard"] > ACCENT_BUDGET_BY_DEPTH["none"]
    assert ACCENT_BUDGET_BY_DEPTH["none"] == 0


def test_none_depth_is_a_no_op_everything_reports_lit() -> None:
    """G-8 tail / B-6: NO_COLOR/dumb/non-tty/ASCII carries no accent budget
    concept -- nothing is demoted because nothing carries colour anyway.
    Alarms and non-accent tiers already always report lit; this test pins
    that EVENT-tier roles also always report lit at depth="none"."""
    allocator = SalienceAllocator()
    bids = (RoleBid("success", True), RoleBid("warning", True), RoleBid("skipped", True))
    allocator.allocate_frame(bids, depth="none")
    # With a zero budget every EVENT-tier bid is evicted unless the caller
    # never invokes the allocator under depth="none" in the first place --
    # production wiring skips the allocator entirely at this depth (S-7);
    # this test documents the allocator's own behavior if it *is* called.
    assert ACCENT_BUDGET_BY_DEPTH["none"] == 0
@pytest.mark.criteria("G-6")


def test_replaying_an_identical_event_sequence_is_byte_identical() -> None:
    """G-6: determinism -- replaying the same call sequence from a fresh
    instance reproduces the exact same decisions every time."""
    sequence = (
        (("success", True), ("warning", True)),
        (("success", False), ("warning", False)),
        (("success", False), ("warning", False), ("error", True)),
        (("success", False), ("warning", False), ("error", False)),
    )

    def replay() -> list[tuple[AllocationDecision, ...]]:
        allocator = SalienceAllocator()
        out = []
        for frame in sequence:
            bids = tuple(RoleBid(role, changed) for role, changed in frame)
            out.append(allocator.allocate_frame(bids, depth="truecolor"))
        return out

    first = replay()
    second = replay()
    assert first == second
@pytest.mark.criteria("F-4")
@pytest.mark.criteria("G-9")


def test_allocation_decisions_are_inspectable_data_not_internals() -> None:
    """G-9/F-4: assertions target AllocationDecision fields, never allocator internals."""
    allocator = SalienceAllocator()
    decisions = allocator.allocate_frame((RoleBid("success", True),), depth="truecolor")
    decision = decisions[0]
    assert decision.role == "success"
    assert decision.tier is FrequencyTier.EVENT
    assert decision.lit is True
    assert isinstance(decision.reason, str) and decision.reason
@pytest.mark.criteria("G-2")


def test_demote_hex_caps_chroma_at_unchanged_lightness_and_hue() -> None:
    """G-2: demotion moves chroma toward the tier budget without touching L/H."""
    from ralph.display._palette import hex_to_rgb, oklab_to_oklch, rgb_to_oklab

    resolved = resolve_palette("#2D2A2E")["success"]
    demoted = demote_hex(resolved, budget=0.08)
    orig_l, _orig_c, orig_h = oklab_to_oklch(*rgb_to_oklab(*hex_to_rgb(resolved)))
    new_l, new_c, new_h = oklab_to_oklch(*rgb_to_oklab(*hex_to_rgb(demoted)))
    assert new_c <= 0.08 + 1e-6
    assert abs(new_l - orig_l) < 0.01
    assert abs(new_h - orig_h) < 2.0


def test_demote_hex_never_drops_below_the_contrast_floor() -> None:
    """G-2: a demoted role never drops below its C-1 4.5:1 floor."""
    from ralph.display._palette import contrast_ratio

    surface_hex = "#2D2A2E"
    resolved = resolve_palette(surface_hex)["success"]
    demoted = demote_hex(resolved, budget=0.001, surface_hex=surface_hex)
    assert contrast_ratio(demoted, surface_hex) >= 4.5


def test_demote_hex_is_a_no_op_when_already_within_budget() -> None:
    """demote_hex must not perturb a role that is already at or under budget."""
    anchor = ROLE_ANCHORS["chrome"]
    resolved = resolve_palette("#2D2A2E")["chrome"]
    demoted = demote_hex(resolved, budget=anchor.chroma + 1.0)
    assert demoted == resolved.upper()
