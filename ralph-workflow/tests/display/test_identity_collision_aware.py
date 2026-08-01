"""P3 (wt-028-display S-25 / AC-15) collision-aware identity coloring.

The shared registry's :func:`_identity_style_for` accepts an
``active=`` iterable so the collision-aware palette slot is
picked. Two simultaneously-rendered identities must never share a
confusable color under any of the three documented CVD simulations
(deuteranopia / protanopia / tritanopia).

These tests are deliberately black-box: they import the helper from
:mod:`ralph.display.agent_event_renderer`, exercise the public
surface, and pin:

* deterministic output across calls (same name -> same color),
* distinct colors when an ``active`` set already occupies the
  deterministic slot,
* the same color is reachable when the active set does NOT
  occupy the slot,
* the resolved hex is always a member of the identity palette.
"""

from __future__ import annotations

from ralph.display.agent_event_renderer import _identity_style_for
from ralph.display.theme import (
    IDENTITY_PALETTE_ON_UNKNOWN_BG,
    _identity_slot,
    _simulate_cvd,
)


def test_identity_style_for_empty_unit_returns_empty_string() -> None:
    """No unit -> no override (the caller's default body style wins)."""
    assert _identity_style_for(None) == ""
    assert _identity_style_for("") == ""


def test_identity_style_for_is_deterministic() -> None:
    """Same name -> same color across calls (muscle memory invariant)."""
    style_a = _identity_style_for("claude")
    style_b = _identity_style_for("claude")
    assert style_a == style_b
    assert style_a != ""


def test_identity_style_for_collapses_case_and_separators() -> None:
    """``CLAUDE`` / ``claude`` / ``Claude__`` all map to one identity.

    Names differing only in case or separators are the same actor.
    """
    assert _identity_style_for("CLAUDE") == _identity_style_for("claude")
    assert _identity_style_for("Claude__") == _identity_style_for("claude")


def test_identity_style_for_keeps_distinct_substantively() -> None:
    """``claude`` and ``claude-headless`` keep distinct colors.

    Substantively different names stay distinct (different
    substance, different identity).
    """
    assert _identity_style_for("claude") != _identity_style_for("claude-headless")


def test_identity_style_for_active_nudges_away_from_collision() -> None:
    """When the deterministic slot is occupied, the nudge picks the next slot.

    The collision-aware slot MUST differ from the deterministic
    slot when an active identity already holds it.
    """
    # pi and agy share a deterministic slot. The explicit active set forces
    # the production collision resolver to choose a distinct palette member.
    assert _identity_slot("pi") == _identity_slot("agy")
    base = _identity_style_for("pi", active=[])
    nudged = _identity_style_for("pi", active=["pi", "agy"])
    assert nudged != base
    assert nudged in IDENTITY_PALETTE_ON_UNKNOWN_BG


def test_identity_style_for_active_keeps_distinct_colors_cvd_safe() -> None:
    """Two identities on screen at the same time stay distinct under CVD.

    AC-15 (wt-028-display P3): no two identities can be confusable
    under deuteranopia / protanopia / tritanopia.
    """
    active = ("claude", "codex", "opencode", "pi", "cursor", "agy", "nanocoder")
    resolved = {name: _identity_style_for(name, active=active) for name in active}

    # Two identities may not collide under any of the three CVD
    # simulations. We compare the simulated hex codes (the color
    # the operator would actually see).
    seen_cvd: dict[str, str] = {}
    matrices = {
        "deuteranopia": (
            (0.625, 0.375, 0.0),
            (0.7, 0.3, 0.0),
            (0.0, 0.3, 0.7),
        ),
        "protanopia": (
            (0.567, 0.433, 0.0),
            (0.558, 0.442, 0.0),
            (0.0, 0.242, 0.758),
        ),
        "tritanopia": (
            (0.95, 0.05, 0.0),
            (0.0, 0.433, 0.567),
            (0.0, 0.475, 0.525),
        ),
    }
    for name, hex_color in resolved.items():
        for label, matrix in matrices.items():
            simulated = _simulate_cvd(hex_color, matrix)
            key = f"{label}:{simulated}"
            existing = seen_cvd.get(key)
            assert existing is None, f"{name!r} and {existing!r} collide under {label}: {simulated}"
            seen_cvd[key] = name


def test_identity_style_for_active_is_stable_when_active_is_empty() -> None:
    """An explicit empty active set resolves deterministically."""
    name = "opencode"
    empty = _identity_style_for(name, active=[])
    assert empty == _identity_style_for(name, active=[])
    assert empty in IDENTITY_PALETTE_ON_UNKNOWN_BG


def test_identity_style_for_resolves_to_palette_member() -> None:
    """Resolved hex is always a member of the identity palette."""
    for name in ("claude", "codex", "opencode", "pi", "cursor", "agy", "nanocoder"):
        for active in [None, ["claude"], ["codex", "opencode"]]:
            hex_color = _identity_style_for(name, active=active)
            assert hex_color in IDENTITY_PALETTE_ON_UNKNOWN_BG
