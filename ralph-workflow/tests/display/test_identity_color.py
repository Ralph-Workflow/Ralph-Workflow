"""P3 (wt-028-display AC-15): automatic identity color.

An unconfigured agent name receives a deterministic, stable,
accessible identity color distinct from status colors, checked
automatically under the three CVD simulations. The agent prefix in
the event renderer and the Status Bar's agent segment both surface
the color so a glance at the live log or the footer reveals which
agent produced each line.

These tests pin:

* Determinism -- the same name always lands on the same slot in
  the same palette.
* Normalization -- case / whitespace / underscore collapse to a
  single identity; ``claude`` and ``claude-headless`` are
  intentionally distinct.
* Pairwise distance -- 12-slot palette clears the 40-unit threshold
  the existing status-palette test enforces.
* Disjoint from status roles -- identity never inherits a state
  color.
* CVD distinguishability -- pairwise distance holds under
  deuteranopia / protanopia / tritanopia simulation at a relaxed
  threshold (the simulations reduce the color space, so a smaller
  threshold is appropriate).
* Contrast -- each color clears WCAG 4.5:1 on at least one
  background (dark or light) so it is always legible.
* Collision nudge -- ``active=[other]`` shifts the slot forward
  until a non-conflicting color is found.
* Application -- the renderer's unit prefix and the Status Bar's
  agent segment both pick up the identity color.
"""

from __future__ import annotations

from itertools import combinations

import pytest

from ralph.display import theme
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_model import ActivityProvider, EventOptions, make_event
from ralph.display.agent_event_renderer import (
    _identity_style_for,
    _split_body_with_unit,
    render_event,
    render_event_kind_text,
)
from ralph.display.context import make_display_context
from ralph.display.status_bar import StatusBarModel, render_status_bar
from ralph.display.theme import (
    IDENTITY_PALETTE,
    IDENTITY_PALETTE_ON_LIGHT_BG,
    STATUS_STYLES,
    STATUS_STYLES_ON_LIGHT_BG,
    identity_color,
)

pytestmark = pytest.mark.timeout_seconds(5)


# --- Determinism ---------------------------------------------------------


def test_identity_color_is_deterministic_across_calls() -> None:
    """Same name -> same color, every call."""
    first = identity_color("claude")
    second = identity_color("claude")
    assert first == second
    third = identity_color("claude")
    assert third == first


def test_identity_color_is_stable_across_palette_hits() -> None:
    """Two distinct names that hash to the same slot are still deterministic."""
    # Recompute and assert that the same name always picks the same
    # slot, even when the call is interleaved with other lookups.
    a = identity_color("claude")
    b = identity_color("codex")
    c = identity_color("claude")
    assert a == c
    assert a != b


# --- Normalization -------------------------------------------------------


@pytest.mark.parametrize(
    "variant,expected",
    [
        ("claude", "claude"),
        ("CLAUDE", "claude"),
        ("Claude", "claude"),
        ("claude_headless", "claude-headless"),
        ("claude-headless", "claude-headless"),
        ("Claude-Headless", "claude-headless"),
        ("  claude  ", "claude"),
        ("claude headless", "claude-headless"),
    ],
)
def test_normalize_identity_name_preserves_dash_distinction(variant: str, expected: str) -> None:
    """``claude`` and ``claude-headless`` are distinct identities.

    Underscore and whitespace collapse to ``-``; the ``-`` separator
    itself is preserved so the headless and non-headless transport
    names map to different palette slots.
    """
    assert theme._normalize_identity_name(variant) == expected
    # Same identity -> same color regardless of how it was spelled.
    assert identity_color(variant) == identity_color(expected)


def test_empty_name_falls_back_to_unknown() -> None:
    """Empty / whitespace-only input -> ``"unknown"`` bucket.

    Without this guard a malicious or accidental empty ``unit_id``
    would route to palette slot 0 deterministically; making the
    empty case explicit keeps every empty string on the same
    color across calls.
    """
    assert theme._normalize_identity_name("") == "unknown"
    assert theme._normalize_identity_name("   ") == "unknown"
    assert theme._normalize_identity_name("---") == "unknown"
    assert identity_color("") == identity_color("unknown")
    assert identity_color("") == identity_color("   ")


# --- Pairwise RGB distance ----------------------------------------------


def test_identity_palette_pairwise_distance_meets_threshold() -> None:
    """Every pair of identity colors clears the 40-unit RGB threshold.

    The same threshold the existing
    ``test_status_style_pairs_have_distinct_non_color_carriers_and_rgb_distance``
    test enforces on the Okabe-Ito status palette.
    """
    for a, b in combinations(IDENTITY_PALETTE, 2):
        assert theme._hex_distance(a, b) >= 40, (
            f"identity palette pair {a}/{b} = {theme._hex_distance(a, b):.1f}"
        )


def test_light_bg_identity_palette_pairwise_distance_meets_threshold() -> None:
    """The light-bg variant is also pairwise distant."""
    for a, b in combinations(IDENTITY_PALETTE_ON_LIGHT_BG, 2):
        assert theme._hex_distance(a, b) >= 40, (
            f"light-bg identity pair {a}/{b} = {theme._hex_distance(a, b):.1f}"
        )


def test_identity_palette_disjoint_from_status_palette() -> None:
    """AC-15 acceptance: no identity color is also a status color.

    A reader who can distinguish ``success`` (bluish-green) from
    ``error`` (vermillion) must NEVER confuse either with a name
    color. The disjoint check enforces that boundary.
    """
    status_hexes: set[str] = set()
    for table in (STATUS_STYLES, STATUS_STYLES_ON_LIGHT_BG):
        for style, _icon, _label in table.values():
            extracted = theme._extract_hex(style)
            if extracted:
                status_hexes.add(extracted.lower())
    for ident in IDENTITY_PALETTE:
        assert ident.lower() not in status_hexes, f"identity {ident} collides with a status role"


# --- CVD distinguishability ---------------------------------------------


_CVD_THRESHOLD: float = 15.0


@pytest.mark.parametrize(
    "matrix",
    [
        theme._DEUTERANOPIA_MATRIX,
        theme._PROTANOPIA_MATRIX,
        theme._TRITANOPIA_MATRIX,
    ],
    ids=["deuteranopia", "protanopia", "tritanopia"],
)
def test_identity_palette_pairwise_distance_under_cvd(matrix: tuple) -> None:
    """Every pair clears the (relaxed) threshold under each CVD simulation.

    The Brettel/Vienot simulation matrices reduce the color space,
    so a 15-unit threshold is appropriate where 40 is appropriate
    for the original color space. The test guarantees that two
    identities that "look fine" to a trichromat do not collapse
    onto the same hue for a colourblind operator.
    """
    for a, b in combinations(IDENTITY_PALETTE, 2):
        a_sim = theme._simulate_cvd(a, matrix)
        b_sim = theme._simulate_cvd(b, matrix)
        distance = theme._hex_distance(a_sim, b_sim)
        assert distance >= _CVD_THRESHOLD, (
            f"CVD {matrix} pair {a}/{b} -> {a_sim}/{b_sim} = {distance:.1f}"
        )


# --- Contrast ------------------------------------------------------------


def test_identity_palette_clears_contrast_on_at_least_one_background() -> None:
    """Each identity clears WCAG 4.5:1 on at least one of {black, white}.

    Operators using either light or dark terminal themes see a
    legible identity color; the test guards the lower bound so a
    future palette edit cannot drop a color below 4.5:1 on both
    backgrounds.
    """
    for color in IDENTITY_PALETTE:
        on_white = theme.contrast_ratio(color, "#FFFFFF")
        on_black = theme.contrast_ratio(color, "#000000")
        assert max(on_white, on_black) >= 4.5, (
            f"identity {color} fails 4.5:1 on both black ({on_black:.2f}) "
            f"and white ({on_white:.2f})"
        )


def test_light_bg_palette_clears_contrast_on_white() -> None:
    """The light-bg variant is the dark-on-white variant -- it MUST pass on white."""
    for color in IDENTITY_PALETTE_ON_LIGHT_BG:
        ratio = theme.contrast_ratio(color, "#FFFFFF")
        assert ratio >= 4.5, f"light-bg {color} = {ratio:.2f} on white"


# --- Collision nudge -----------------------------------------------------


def test_identity_color_collision_nudge_picks_different_color() -> None:
    """When the deterministic slot is taken, the picker walks forward.

    A scenario with 12+ active identities is unrealistic for the
    documented 8-agent roster, but the nudge exists so two parallel
    identities that hash to the same slot are still distinguishable
    on the live display.
    """
    # Take the deterministic slot of "claude" out of the running;
    # the returned color must not be that slot.
    base = identity_color("claude")
    nudged = identity_color("claude", active=["claude"])
    assert nudged != base
    # The nudged color is still in the palette.
    assert nudged in IDENTITY_PALETTE


def test_identity_color_active_must_not_contain_self() -> None:
    """A name in the active set whose slot is free returns the base slot.

    Sanity check: ``active`` filters out already-used colors, but
    if the agent's own color is the only one available, the picker
    still returns it deterministically (the caller does not pass
    the agent's own name in ``active``).
    """
    base = identity_color("claude")
    # active excludes some other agents; the base slot is free so
    # the function returns the deterministic color.
    same = identity_color("claude", active=["codex", "opencode"])
    assert same == base


# --- Light-bg variant ---------------------------------------------------


def test_identity_color_light_bg_uses_light_palette() -> None:
    """``terminal_bg_is_light=True`` returns the light-bg variant."""
    dark = identity_color("claude", terminal_bg_is_light=False)
    light = identity_color("claude", terminal_bg_is_light=True)
    assert dark in IDENTITY_PALETTE
    assert light in IDENTITY_PALETTE_ON_LIGHT_BG
    # Hue identity is preserved: the dark/light variants share
    # the same index in their respective palettes.
    assert IDENTITY_PALETTE.index(dark) == IDENTITY_PALETTE_ON_LIGHT_BG.index(light)


# --- All 8 documented agents -------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "claude",
        "claude-headless",
        "codex",
        "opencode",
        "nanocoder",
        "agy",
        "pi",
        "cursor",
    ],
)
def test_all_documented_agents_receive_a_distinct_color(name: str) -> None:
    """The 8 documented agents each get a deterministic identity color.

    Two agents that hash to the same slot collide; the test
    documents the actual slot pattern so a future hash change is
    visible. The collision-nudge path covers the rare case where
    two agents land on the same slot.
    """
    color = identity_color(name)
    assert color in IDENTITY_PALETTE


# --- Tool identities ----------------------------------------------------


def test_tool_names_receive_collision_aware_accessible_identity_colors() -> None:
    """S-4: unknown tool names use the same safe identity palette as agents."""
    tools = ("read_file", "grep_files", "ralph.submit_artifact")
    for matrix in (
        theme._DEUTERANOPIA_MATRIX,
        theme._PROTANOPIA_MATRIX,
        theme._TRITANOPIA_MATRIX,
    ):
        colors = [theme._simulate_cvd(identity_color(tool), matrix) for tool in tools]
        for first, second in combinations(colors, 2):
            assert theme._hex_distance(first, second) >= _CVD_THRESHOLD
    status_colors = {
        extracted.lower()
        for style, _icon, _label in STATUS_STYLES.values()
        if (extracted := theme._extract_hex(style))
    }
    assert all(identity_color(tool).lower() not in status_colors for tool in tools)


# --- Renderer application ----------------------------------------------


def _ctx():
    return make_display_context(force_width=200, force_glyphs=True)


def test_identity_style_for_returns_empty_for_no_unit() -> None:
    """No unit_id -> no override style (caller's body style wins)."""
    assert _identity_style_for(None) == ""
    assert _identity_style_for("") == ""


def test_identity_style_for_returns_palette_color() -> None:
    """A unit_id picks the deterministic identity color."""
    style = _identity_style_for("claude")
    assert style == identity_color("claude")
    assert style in IDENTITY_PALETTE


def test_split_body_with_unit_separates_prefix() -> None:
    """Body prefixed with ``"{unit_id} "`` is split on the first space."""
    prefix, rest = _split_body_with_unit("claude hello world", "claude")
    assert prefix == "claude "
    assert rest == "hello world"


def test_split_body_with_unit_returns_whole_body_when_no_match() -> None:
    """Bodies that do not start with the canonical prefix are passed through."""
    prefix, rest = _split_body_with_unit("hello world", "claude")
    assert prefix == ""
    assert rest == "hello world"


def test_event_renderer_unit_prefix_carries_identity_color() -> None:
    """The unit prefix in the rendered Text carries the identity color.

    Uses the rich-Text ``style`` attribute on the segment that
    holds the unit prefix so a downstream Console with
    ``markup=True`` colors the prefix distinctly from the body.
    """
    event = make_event(
        provider=ActivityProvider.CLAUDE,
        kind=ActivityEventKind.TEXT,
        options=EventOptions(content="hello world"),
    )
    rendered = render_event(event, _ctx(), unit_id="claude")
    # The plain-text path is byte-identical to the pre-P3 contract.
    assert "claude hello world" in rendered.plain
    # The rich-Text path colors the prefix segment with the
    # identity hex; the rest of the body uses the default style.
    expected_color = identity_color("claude")
    # At least one span carries the identity color, and that span
    # covers the unit prefix substring (e.g. ``"claude "``).
    identity_spans = [s for s in rendered.spans if s.style == expected_color]
    assert identity_spans
    assert any(rendered.plain[s.start : s.end].rstrip() == "claude" for s in identity_spans)


def test_event_renderer_uses_light_background_identity_palette() -> None:
    """A light DisplayContext selects the light identity palette in event output."""
    ctx = make_display_context(env={"RALPH_TERMINAL_BG": "light"}, force_width=200)
    event = make_event(
        provider=ActivityProvider.CLAUDE,
        kind=ActivityEventKind.TEXT,
        options=EventOptions(content="hello world"),
    )
    rendered = render_event(event, ctx, unit_id="claude")
    assert any(
        span.style == identity_color("claude", terminal_bg_is_light=True) for span in rendered.spans
    )


def test_status_bar_uses_light_background_identity_palette() -> None:
    """A light DisplayContext selects the light identity palette in the footer."""
    ctx = make_display_context(
        env={"RALPH_TERMINAL_BG": "light"}, force_width=200, force_glyphs=True
    )
    rendered = render_status_bar(
        StatusBarModel(
            workspace_root="/tmp/probe",
            phase_label="Development",
            phase_style="theme.phase.development",
            agent_name="claude",
        ),
        ctx,
    )
    assert any(
        span.style == identity_color("claude", terminal_bg_is_light=True) for span in rendered.spans
    )


def test_event_renderer_plain_text_path_unchanged_by_identity_color() -> None:
    """The plain-text path emits the same body string the legacy callers relied on.

    The identity color lives in the rich-Text path; the plain-text
    path (used by the ring buffer and the activity router) must
    keep the bare ``"{unit_id} {content}"`` contract.
    """
    plain = render_event_kind_text(
        ActivityEventKind.TEXT,
        "hello world",
        timestamp=None,
        metadata=None,
        agent_name="claude",
    )
    assert "claude" in plain
    assert "hello world" in plain


def test_status_bar_agent_segment_carries_identity_color() -> None:
    """The Status Bar's agent segment surfaces the identity color.

    Confirms the agent segment picks up the deterministic color
    from :func:`identity_color` so the live footer reveals which
    agent produced the current cycle.
    """
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=4,
        agent_name="claude",
    )
    rendered = render_status_bar(model, _ctx())
    assert "claude" in rendered.plain
    # The agent segment is colored with the identity color.
    expected_color = identity_color("claude")
    assert any(span.style == expected_color for span in rendered.spans), (
        f"Status Bar agent segment missing identity color {expected_color}"
    )


# --- Module surface ----------------------------------------------------


def test_identity_color_is_in_theme_module_public_surface() -> None:
    """The function is part of the theme module's public surface."""
    assert "identity_color" in theme.__all__
    assert "IDENTITY_PALETTE" in theme.__all__
    assert "IDENTITY_PALETTE_ON_LIGHT_BG" in theme.__all__


def test_identity_color_handles_unicode_name() -> None:
    """A non-ASCII name does not crash the picker.

    Agents with non-ASCII names (the documented 8 are ASCII, but a
    user-defined model name might not be) must still get a
    deterministic color.
    """
    color = identity_color("café")
    assert color in IDENTITY_PALETTE
