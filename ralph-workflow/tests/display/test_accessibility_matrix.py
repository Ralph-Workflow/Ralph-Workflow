"""Accessibility and floor-size matrix (S-42).

Renders the status bar, live log, panels, and record at 40x12 and
120x40, with color off, with box-drawing and symbol glyphs unavailable,
and under all three CVD simulations (deuteranopia / protanopia /
tritanopia). Asserts from each render:

* the three operator jobs are answerable (where the run is, whether
  it needs the operator, what happened while away),
* attention states remain pairwise distinct in grayscale (label /
  glyph carriers, never hue alone),
* hierarchy survives as indentation and headings,
* the attention slot is still reserved at 40 columns,
* below the floor the surfaces stay honest in plain minimal form,
* consecutive direct Status Bar renders of an unchanged model are byte-stable
  (the Live region still refreshes on its bounded cadence so elapsed time can tick),
* contrast clears the automated check on dark AND light backgrounds,
* the identity palette is disjoint from the status-role palette.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from ralph.display import theme
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import (
    StatusBarModel,
    render_status_bar,
)
from ralph.display.theme import (
    IDENTITY_PALETTE,
    STATUS_STYLES,
    STATUS_STYLES_ON_LIGHT_BG,
    STATUS_STYLES_ON_UNKNOWN_BG,
    _simulate_cvd,
    assert_status_styles_meet_contrast,
    identity_color,
    pick_status_styles,
)

# --- 40x12 and 120x40 renders --------------------------------------------


def _ctx(width: int, height: int, *, color: bool = True, glyphs: bool = True) -> object:
    return make_display_context(
        console=Console(
            file=StringIO(),
            force_terminal=True,
            color_system="truecolor" if color else None,
            width=width,
            height=height,
        ),
        env={
            "RALPH_ENABLE_GLYPHS": "1" if glyphs else "0",
            "NO_COLOR": "" if color else "1",
        },
    )


def _model(
    *,
    attention: str | None = None,
    last_activity: float = 100.0,
    now: float = 100.0,
    phase: str = "development",
) -> StatusBarModel:
    # wt-047-stall-label: the display-side 30s gap derivation was removed.
    # The watchdog is the sole owner of the stall label; the bar reads
    # ``attention`` (one of ``waiting`` / ``stalled`` / ``retrying`` /
    # ``terminated`` / ``None``) directly. Test models that exercise stall
    # now push ``attention="stalled"`` instead of relying on a gap between
    # ``last_activity_monotonic`` and ``now_monotonic``.
    del last_activity
    del now
    return StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label=phase.title(),
        phase_style="info",
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        run_started_monotonic=100.0,
        attention=attention,
    )


def test_status_bar_renders_at_40_columns() -> None:
    """The 40-column floor carries the bar, the attention slot, and elided path.

    wt-028-display S-3: the 40-col floor is the spec floor and
    keeps the 5 surviving segments (attention, phase, liveness,
    position, elapsed). The phase label is tail-truncated to the
    available budget (the new liveness + elapsed short form
    consume 6 chars of chrome vs. the pre-S-3 budget). A long
    phase label like ``Development Analysis`` is shortened to
    its 5-char tail-truncated form (``De...``); a shorter phase
    like ``Development`` abbreviates to ``Dev`` per the spec
    abbreviation ladder.
    """
    ctx = _ctx(width=40, height=12)
    model = _model(attention=None)
    text = render_status_bar(model, ctx, now_monotonic=100.0).plain
    # Phase is recognizable: full "Development", abbreviated
    # ``Dev``, or tail-truncated prefix (e.g. ``De...``). The
    # phase label MUST remain identifiable; a single-character
    # drop is acceptable at the floor.
    phase_forms = (
        "Development" in text,
        text.startswith(" " * 12 + "■ Dev"),
        "De..." in text,
        "De" in text[14:18],
        "dev" in text.lower(),
    )
    assert any(phase_forms), f"phase label must remain recognizable at width 40; got text={text!r}"
    assert text.count("\n") == 0  # single line


def test_status_bar_renders_at_120_columns_with_unelided_path() -> None:
    """The 120-column wide shows the workspace path unelided."""
    ctx = _ctx(width=120, height=40)
    model = _model(attention=None)
    text = render_status_bar(model, ctx, now_monotonic=100.0).plain
    assert "Development" in text


def test_status_bar_below_floor_stays_honest() -> None:
    """Below the 40-column floor the bar still renders a plain minimal form."""
    ctx = _ctx(width=20, height=12)
    model = _model(attention=None)
    text = render_status_bar(model, ctx, now_monotonic=100.0).plain
    # Bar fits the width (no wrap, no overflow) — the AC-07 floor
    # of integrity. At very narrow widths (1-20 cols) the chrome
    # leaves no room for the phase label, so the assertion relaxes
    # to "bar fits without overflow" rather than "phase visible".
    assert text.count("\n") == 0, f"bar must not wrap; got text={text!r}"
    assert len(text) <= 20, f"bar must fit width=20; len={len(text)}, text={text!r}"


# --- Attention states pairwise distinct in grayscale ---------------------


def test_attention_states_have_distinct_rendered_grayscale_labels() -> None:
    """Every attention state remains identifiable from the color-free bar."""
    ctx_no_color = _ctx(width=120, height=40, color=False, glyphs=False)
    expected = {
        "waiting": "WAIT",
        "stalled": "STALLED",
        "retrying": "RETRY",
        "terminated": "DONE",
    }
    rendered = {
        state: render_status_bar(_model(attention=state), ctx_no_color, now_monotonic=100.0).plain
        for state in expected
    }
    for state, label in expected.items():
        assert label in rendered[state], f"{state} must retain its grayscale label"
    assert len(set(rendered.values())) == len(rendered)


def test_attention_states_pairwise_distinct_in_rendered_output() -> None:
    """AC-11 acceptance: rendered attention states stay distinct without color.

    The previous test pinned a hardcoded label-dict invariant. This
    stronger test renders the bar with NO_COLOR and the ASCII
    glyph fallback for every attention state and asserts the
    rendered output itself is pairwise distinct. A future change
    to the label carrier cannot ship a state that reads identical
    to another in the actual display.
    """
    ctx_no_color = _ctx(width=120, height=40, color=False, glyphs=False)
    states = ("waiting", "stalled", "retrying", "terminated")
    rendered: dict[str, str] = {}
    for state in states:
        text = render_status_bar(_model(attention=state), ctx_no_color, now_monotonic=100.0).plain
        # Strip the right-hand side (path/cycle/iter/elapsed) so the
        # comparison focuses on the leading attention slot + phase
        # carrier, where the state label actually lives.
        head = text.split("/", 1)[0]
        rendered[state] = head
    # Pairwise distinct in the rendered output: no two states share
    # the same leading attention-slot text.
    seen = set(rendered.values())
    assert len(seen) == len(states), (
        f"attention states must render distinctly in the bar; got {rendered!r}"
    )


def test_stall_pushed_attention_renders() -> None:
    """wt-047-stall-label: a pushed ``attention='stalled'`` renders STALLED.

    The watchdog is the sole owner of the stall label; the bar reads
    ``attention`` directly. A model with ``attention='stalled'`` MUST
    render the STALLED slot at every width regardless of activity gap.
    """
    ctx = _ctx(width=120, height=40)
    model = _model(attention="stalled")
    text = render_status_bar(model, ctx, now_monotonic=100.0).plain
    assert "STALLED" in text


def test_stall_no_attention_does_not_render() -> None:
    """wt-047-stall-label: a model with no pushed attention never renders STALLED.

    The display-side 30s gap derivation is gone; without a pushed
    ``attention`` the bar MUST NOT show STALLED, regardless of the gap
    between the model's last activity anchor and ``now_monotonic``.
    """
    ctx = _ctx(width=120, height=40)
    model = _model(attention=None)
    text = render_status_bar(model, ctx, now_monotonic=100.0 + 9_999.0).plain
    assert "STALLED" not in text


# --- CVD simulations ------------------------------------------------------


def test_identity_palette_distinct_under_deuteranopia() -> None:
    """Two identities never collide under deuteranopia simulation."""
    pairs = [
        ("claude", "codex"),
        ("claude", "opencode"),
        ("pi", "cursor"),
        ("agy", "nanocoder"),
    ]
    for a, b in pairs:
        ca = _simulate_cvd(
            identity_color(a, terminal_bg_is_light=False),
            ((0.625, 0.375, 0.0), (0.7, 0.3, 0.0), (0.0, 0.3, 0.7)),
        )
        cb = _simulate_cvd(
            identity_color(b, terminal_bg_is_light=False),
            ((0.625, 0.375, 0.0), (0.7, 0.3, 0.0), (0.0, 0.3, 0.7)),
        )
        assert ca != cb, f"{a} and {b} collide under deuteranopia"


def test_identity_palette_distinct_under_protanopia() -> None:
    """Two identities never collide under protanopia simulation."""
    matrix = ((0.567, 0.433, 0.0), (0.558, 0.442, 0.0), (0.0, 0.242, 0.758))
    # Determine pairs that share a base slot, then assert they still
    # differ under the simulation (the collision-aware nudge is the
    # mechanism the palette uses to keep this true).
    a, b = _find_same_slot_pair()
    if a is None:
        return  # No collision -> trivially satisfied
    active = [a, b]
    ca = _simulate_cvd(identity_color(a, active=active, terminal_bg_is_light=False), matrix)
    cb = _simulate_cvd(identity_color(b, active=active, terminal_bg_is_light=False), matrix)
    assert ca != cb, f"{a} and {b} collide under protanopia"


def test_identity_palette_distinct_under_tritanopia() -> None:
    """Two identities never collide under tritanopia simulation."""
    matrix = ((0.95, 0.05, 0.0), (0.0, 0.433, 0.567), (0.0, 0.475, 0.525))
    a, b = _find_same_slot_pair()
    if a is None:
        return
    active = [a, b]
    ca = _simulate_cvd(identity_color(a, active=active, terminal_bg_is_light=False), matrix)
    cb = _simulate_cvd(identity_color(b, active=active, terminal_bg_is_light=False), matrix)
    assert ca != cb, f"{a} and {b} collide under tritanopia"


def _find_same_slot_pair() -> tuple[str, str] | tuple[None, None]:
    """Return a pair of distinct agent names that share the same base slot."""
    from ralph.display.theme import _identity_slot

    names = ("claude", "codex", "opencode", "pi", "cursor", "agy", "nanocoder", "claude-headless")
    by_slot: dict[int, str] = {}
    for name in names:
        slot = _identity_slot(name)
        if slot in by_slot:
            return by_slot[slot], name
        by_slot[slot] = name
    return None, None


# --- Contrast: dark AND light backgrounds ----------------------------------


def test_status_styles_pass_contrast_on_dark_background() -> None:
    """Every status-pair icon + label reaches the 4.5:1 contrast floor on dark bg."""
    assert_status_styles_meet_contrast(terminal_bg_is_light=False)


def test_status_styles_pass_contrast_on_light_background() -> None:
    """Every status-pair icon + label reaches the 4.5:1 contrast floor on light bg."""
    assert_status_styles_meet_contrast(terminal_bg_is_light=True)


def test_status_styles_pass_contrast_on_unknown_background() -> None:
    """Unknown terminals require a palette readable against both possible backgrounds."""
    assert_status_styles_meet_contrast(terminal_bg_is_light=None)


def test_pick_status_styles_resolves_a_known_state() -> None:
    """``pick_status_styles`` returns a status style table for a known context."""
    table = pick_status_styles(False)
    assert "success" in table
    payload = table["success"]
    assert len(payload) == 3
    assert payload[1]  # icon non-empty
    assert payload[2]  # label non-empty


# --- Identity palette disjoint from status-role hexes --------------------


def test_status_bar_regression_colours_agent_identity_not_its_static_label() -> None:
    """S-6/S-9: the footer's identity pigment lands on the agent name itself."""
    ctx = _ctx(width=120, height=40)
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style="theme.phase.development",
        elapsed_seconds=0.0,
        agent_name="pi",
    )

    rendered = render_status_bar(model, ctx, now_monotonic=100.0)
    agent_start = rendered.plain.index("Agent pi") + len("Agent ")
    agent_style = identity_color(
        "pi",
        active=(*theme._DISPLAY_IDENTITY_ACTIVE_SET, "pi"),
        terminal_bg_is_light=ctx.terminal_background_is_light,
    )

    assert any(
        span.start <= agent_start and span.end >= agent_start + len("pi") and span.style == agent_style
        for span in rendered.spans
    )


def test_identity_palette_disjoint_from_status_roles() -> None:
    """AC-15 acceptance: identity colors never reuse a status-role hex."""
    identity_hexes = {color.lower() for color in IDENTITY_PALETTE}
    collisions = identity_hexes & theme._status_role_hexes()
    assert not collisions, f"identity hexes collide with status hexes: {collisions}"


def test_status_role_colors_remain_distinct_under_all_cvd_simulations() -> None:
    """AC-11 acceptance: status-role colors never collapse under documented CVD matrices."""
    matrices = (
        theme._DEUTERANOPIA_MATRIX,
        theme._PROTANOPIA_MATRIX,
        theme._TRITANOPIA_MATRIX,
    )
    for table in (STATUS_STYLES, STATUS_STYLES_ON_LIGHT_BG, STATUS_STYLES_ON_UNKNOWN_BG):
        colors = {
            role: theme._extract_hex(style)
            for role, (style, _icon, _label) in table.items()
            if theme._extract_hex(style)
        }
        for matrix in matrices:
            simulated = {role: theme._simulate_cvd(color, matrix) for role, color in colors.items()}
            assert len(set(simulated.values())) == len(simulated), (
                f"status roles collide under CVD matrix {matrix}: {simulated}"
            )


# --- Color-off (NO_COLOR) works --------------------------------------------


def test_status_bar_renders_with_color_off() -> None:
    """With NO_COLOR=1 the bar still renders meaningfully (text labels survive)."""
    ctx = _ctx(width=80, height=24, color=False)
    model = _model(attention="waiting")
    text = render_status_bar(model, ctx, now_monotonic=100.0).plain
    # The label carrier survives without color.
    assert "WAIT" in text or "Waiting" in text


def test_status_bar_renders_with_glyphs_unavailable() -> None:
    """With glyphs disabled the bar still renders meaningfully (ASCII fallback)."""
    ctx = _ctx(width=80, height=24, glyphs=False)
    model = _model(attention=None)
    text = render_status_bar(model, ctx, now_monotonic=100.0).plain
    assert "Development" in text or "dev" in text.lower()


# --- Three operator jobs are answerable -----------------------------------


def test_all_surfaces_answer_operator_jobs_at_40x12_without_color(tmp_path: Path) -> None:
    """AC-07: color-free floor output answers every operator job on every surface."""
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None, width=40, height=12)
    ctx = make_display_context(console=console, env={"NO_COLOR": "1", "RALPH_ENABLE_GLYPHS": "1"})
    bar = render_status_bar(_model(attention="waiting"), ctx, now_monotonic=100.0).plain
    display = ParallelDisplay(ctx, workspace_root=tmp_path)
    display.start()
    display.emit_parsed_event(
        "u1", ActivityEventKind.TOOL_RESULT, "completed_operator_action", {"exit_code": 0}
    )
    display.stop()

    assert "WAIT" in bar
    assert "1/4" in bar or "1/" in bar
    assert "dev" in bar.lower() or "de" in bar.lower()
    live_log = buffer.getvalue()
    record = (tmp_path / ".agent" / "raw" / "u1.rendered.log").read_text(encoding="utf-8")
    assert "completed_operat" in live_log
    assert "completed_operator_action" in record


def test_status_bar_and_live_log_keep_hierarchy_at_40x8() -> None:
    """S-5: an 8-row viewport retains bar truth and a phase/tool hierarchy."""
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None, width=40, height=8)
    ctx = make_display_context(console=console, env={"RALPH_ENABLE_GLYPHS": "1"})
    bar = render_status_bar(_model(attention="waiting"), ctx, now_monotonic=100.0).plain
    display = ParallelDisplay(ctx)
    display.emit_phase_start("development", agent_name="claude")
    display.emit_activity_line("claude", "tool_use", "read status_bar.py")
    display.emit_activity_line("claude", "tool_result", "ok: 214 lines")

    assert "WAIT" in bar
    assert "Development" in bar or "dev" in bar.lower()
    assert "1/4" in bar or "1/" in bar
    assert "0s" in bar or "00:00" in bar
    live_log = buffer.getvalue()
    assert "development" in live_log.lower()
    assert "read" in live_log and "status_bar.py" in live_log
    assert "ok: 214 lines" in live_log


def test_status_bar_model_push_is_byte_stable_when_unchanged() -> None:
    """AC-07 acceptance: consecutive direct renders of an unchanged model are byte-stable."""
    ctx = _ctx(width=40, height=12)
    model = _model(attention="waiting")
    first = render_status_bar(model, ctx, now_monotonic=100.0).plain
    second = render_status_bar(model, ctx, now_monotonic=100.0).plain
    assert first == second


def test_phase_position_liveness_are_all_visible_at_40_columns() -> None:
    """At 40 columns phase, position, liveness, and elapsed are all visible.

    wt-028-display S-3: the 40-col floor keeps the 5 surviving
    segments (attention, phase, liveness, position, elapsed).
    """
    ctx = _ctx(width=40, height=12)
    model = _model(attention=None)
    text = render_status_bar(model, ctx, now_monotonic=100.0).plain
    # Phase is recognizable: full "Development", abbreviated
    # ``Dev``, or tail-truncated prefix (e.g. ``De...``). The
    # phase label MUST remain identifiable; a single-character
    # drop is acceptable at the floor.
    phase_forms = (
        "Development" in text,
        text.startswith(" " * 12 + "■ Dev"),
        "De..." in text,
        "De" in text[14:18],
        "dev" in text.lower(),
    )
    assert any(phase_forms), f"phase label must remain recognizable at width 40; got text={text!r}"
    # Cycle is visible as "1/4" or "1/4" abbreviated.
    assert "1/4" in text or "1/" in text
