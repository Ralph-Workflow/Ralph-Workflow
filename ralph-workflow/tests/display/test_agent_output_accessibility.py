"""Accessibility contract for agent-output rendering.

AC-10 contract: every state the agent-output renderer and Status Bar
use has a redundant carrier (icon + ASCII label), so an operator who
cannot distinguish the hues (or has color disabled) loses no
information. No red/green hue-only pairing.

Tests in this module assert:

* Every :class:`ActivityEventKind` registered in the renderer carries
  a non-color carrier in the rendered text.
* :data:`ralph.display.theme.STATUS_STYLES` defines an icon and an
  ASCII label for every state -- never color alone.
* The Status Bar always surfaces either the path or the phase label
  with a colorblind-safe redundancy.
"""

from __future__ import annotations

import io
import string
from itertools import combinations
from typing import TYPE_CHECKING, cast

import pytest
from rich.console import Console

from ralph.display.activity_model import ActivityProvider, EventOptions, make_event
from ralph.display.agent_event_renderer import EVENT_RENDERERS, render_event
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.status_bar import StatusBarModel, render_status_bar
from ralph.display.theme import STATUS_STYLES, STATUS_STYLES_ON_LIGHT_BG

if TYPE_CHECKING:
    from ralph.display.activity_event_kind import ActivityEventKind
    from ralph.display.agent_activity_event import AgentActivityEvent

pytestmark = pytest.mark.timeout_seconds(5)


def _ctx() -> DisplayContext:
    console = Console(
        file=io.StringIO(), force_terminal=False, color_system=None, width=120
    )
    return make_display_context(console=console)


def _event(kind: object, content: str = "x") -> AgentActivityEvent:

    return make_event(
        provider=ActivityProvider.CLAUDE,
        kind=cast("ActivityEventKind", kind),
        options=EventOptions(content=content),
    )


def test_status_styles_define_redundant_carriers_for_every_state() -> None:
    """Every STATUS_STYLES entry must carry (rich_style, unicode_icon, ascii_label)."""
    for state, payload in STATUS_STYLES.items():
        assert len(payload) == 3, f"{state} must have a 3-tuple style/icon/label"
        _rich_style, icon, label = payload
        assert icon and not icon.isspace(), f"{state} icon must be non-empty"
        assert (
            label and not label.isspace()
        ), f"{state} ascii label must be non-empty"
        # ASCII fallback carriers must contain at least one
        # non-color letter so a colorblind operator (or color-disabled
        # console) gets a carrier it can read without hue.
        assert any(c in string.ascii_uppercase for c in label), (
            f"{state} ascii label {label!r} must contain an ASCII letter"
        )


def _hex_from_style(style: str) -> str | None:
    """Extract the semantic foreground hex, if the style declares one."""
    return next((token for token in style.split() if token.startswith("#")), None)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    """Return the hex color's RGB channels for pairwise-distance checks."""
    return tuple(int(hex_color[index : index + 2], 16) for index in (1, 3, 5))


def test_status_style_pairs_have_distinct_non_color_carriers_and_rgb_distance() -> None:
    """Every semantic pair has labels/icons and at least 40 RGB-channel distance.

    40 rejects near-duplicate hues while
    leaving the documented Okabe-Ito palette intact. States with non-hex Rich
    styles remain protected by the mandatory icon + ASCII-label carriers.
    """
    for table in (STATUS_STYLES, STATUS_STYLES_ON_LIGHT_BG):
        for (left_name, left), (right_name, right) in combinations(table.items(), 2):
            assert left[1:] != right[1:], f"{left_name}/{right_name} need distinct carriers"
            left_hex = _hex_from_style(left[0])
            right_hex = _hex_from_style(right[0])
            if left_hex is not None and right_hex is not None:
                distance = sum(
                    (a - b) ** 2 for a, b in zip(_rgb(left_hex), _rgb(right_hex), strict=True)
                ) ** 0.5
                assert distance >= 40, (
                    f"{left_name}/{right_name} colors are too close: {left_hex}/{right_hex}"
                )


def test_no_red_green_hue_only_pairing_in_status_styles() -> None:
    """Success vs error must NOT rely on red/green hue alone.

    Both colors are present in STATUS_STYLES (the Okabe-Ito palette
    uses vermillion for 'error' and bluish-green for 'success' to
    avoid the red/green accessibility trap), and each carries a
    non-color carrier. The accessibility contract is that no state
    pair relies on color alone -- so we also assert the labels are
    distinct strings (independent of color).
    """
    assert "success" in STATUS_STYLES
    assert "error" in STATUS_STYLES
    _s_succ, _i_succ, l_succ = STATUS_STYLES["success"]
    _s_err, _i_err, l_err = STATUS_STYLES["error"]
    # Labels differ -- so a no-color console can still distinguish them.
    assert l_succ != l_err, "success and error ascii labels must differ"
    assert _i_succ != _i_err, "success and error icons must differ"


def test_every_registered_event_kind_carries_a_non_color_carrier() -> None:
    """Render every kind through the registry and verify the plain text
    contains an icon + ASCII label carrier (independent of color)."""
    ctx = _ctx()
    for kind in EVENT_RENDERERS:
        event = _event(kind, content=f"sample_{kind.value}")
        rendered = render_event(event, ctx)
        plain = rendered.plain
        # The plain text must include at least one unicode glyph used
        # by STATUS_STYLES (proves the icon is rendered).
        icon_carriers = {payload[1] for payload in STATUS_STYLES.values()}
        assert any(icon in plain for icon in icon_carriers), (
            f"kind={kind.value} rendered without any STATUS_STYLES icon: {plain!r}"
        )
        # And a recognized ASCII label (proves the label is rendered).
        label_carriers = {payload[2] for payload in STATUS_STYLES.values()}
        assert any(label in plain for label in label_carriers), (
            f"kind={kind.value} rendered without any STATUS_STYLES label: {plain!r}"
        )


def test_status_bar_redundant_carriers_for_path_and_phase() -> None:
    """The Status Bar's path + phase segments surface an explicit label so
    no-color rendering keeps the operating state identifiable."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=5,
    )
    text = render_status_bar(model, ctx)
    plain = text.plain
    # Workspace path is preserved verbatim (mid-truncation aside).
    assert "probe" in plain
    # Phase label is preserved.
    assert "Development" in plain
    # Outer cycle label uses the neutral 'Cycle' (phase-independent),
    # so a no-color console sees the same label a color console does.
    assert "Cycle" in plain
