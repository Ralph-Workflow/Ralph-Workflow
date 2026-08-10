"""E-1's render-frequency-tier evidence, as a re-runnable, checked test.

``_palette.py``'s own module comment on ``ROLE_FREQUENCY_TIER`` cites a
one-time counting probe: "a counting probe wrapping
``rich.console.Console.get_style`` was run once across all six
``ralph.display.scene_catalog.SCENE_NAMES`` scenes (dark, truecolour,
unicode, 80 columns) via ``render_scene``". Definition of Done #3 requires
that evidence not be left as prose. This module re-runs the *same* probe
methodology -- instrumenting ``Console.get_style`` during the same
six-scene render -- and asserts a directional consistency property against
``ROLE_FREQUENCY_TIER`` instead of pinning the exact historical counts
(which would make this a brittle golden-number test rather than a
regression-proof sanity check): the role backing the single most-resolved
named theme style must be tier 1 (``FrequencyTier.FIELD``), and no
tier-3/4 (``EVENT``/``ALARM``) role's own most-resolved style may be
resolved more often than that tier-1 role's.
"""

from __future__ import annotations

import pytest

import collections
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display import theme as theme_mod
from ralph.display._frequency_tier import FrequencyTier
from ralph.display._palette import ROLE_FREQUENCY_TIER, resolve_palette
from ralph.display.scene_catalog import FULL_LAYOUT_WIDTH, SCENE_NAMES, SupportCase, render_scene

if TYPE_CHECKING:
    import pytest
    from rich.style import Style

#: Matches _palette.py's own cited probe environment: dark, truecolour,
#: unicode, 80 columns, across every catalogue scene.
_PROBE_CASE: SupportCase = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
#: The reference dark surface every ``ROLE_ANCHORS`` hex is measured
#: against -- the same surface the probe's own dark theme table resolves
#: against, so a resolved style's embedded hex can be matched back to the
#: role that produced it.
_PROBE_SURFACE_HEX: str = "#2D2A2E"


def _count_theme_style_resolutions(monkeypatch: pytest.MonkeyPatch) -> collections.Counter[str]:
    """Re-run _palette.py's cited probe: wrap ``Console.get_style`` and
    tally every named theme-table key resolved while rendering the full
    six-scene catalogue once."""
    counts: collections.Counter[str] = collections.Counter()
    original_get_style = Console.get_style

    def _counting_get_style(
        self: Console, name: str | Style, *, default: Style | str | None = None
    ) -> Style:
        if isinstance(name, str):
            counts[name] += 1
        return original_get_style(self, name, default=default)

    monkeypatch.setattr(Console, "get_style", _counting_get_style)
    for scene_name in SCENE_NAMES:
        render_scene(scene_name, _PROBE_CASE, terminal_bg_is_light=False)
    return counts


def _role_max_resolution_counts(counts: collections.Counter[str]) -> dict[str, int]:
    """Attribute each observed theme-key count to the role whose resolved
    hex the key's style embeds, keeping each role's *single highest*
    observed key count (not a sum across every key that role backs --
    several structural keys share `chrome`'s hex, and summing them would
    understate how concentrated a single role's own busiest style key is)."""
    palette = {role: hex_val.upper() for role, hex_val in resolve_palette(_PROBE_SURFACE_HEX).items()}
    dark_theme = theme_mod.theme_for_background(False)

    role_max: dict[str, int] = {}
    for key, count in counts.items():
        if count == 0 or key not in dark_theme.styles:
            continue
        hex_val = theme_mod._extract_hex(str(dark_theme.styles[key]))
        if not hex_val:
            continue
        hex_val = hex_val.upper()
        role = next((candidate for candidate, h in palette.items() if h == hex_val), None)
        if role is None:
            continue
        role_max[role] = max(role_max.get(role, 0), count)
    return role_max
@pytest.mark.criteria("E-1")


def test_frequency_tier_evidence_top_resolved_role_is_field_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E-1/DoD #3: re-running the counting probe must show a tier-1
    (``FrequencyTier.FIELD``) role backing the single most-resolved named
    theme style -- reproducing ``_palette.py``'s own cited measurement
    (``theme.text.muted`` highest at 67, backed by the ``muted`` role) as a
    live, regression-checked assertion instead of a static comment."""
    counts = _count_theme_style_resolutions(monkeypatch)
    role_max = _role_max_resolution_counts(counts)
    assert role_max, "no theme-table style resolutions were observed"

    top_role = max(role_max, key=role_max.__getitem__)
    assert ROLE_FREQUENCY_TIER[top_role] is FrequencyTier.FIELD, (top_role, role_max)


def test_frequency_tier_evidence_no_event_or_alarm_role_out_resolves_the_top_field_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E-1/DoD #3: no tier-3 (``EVENT``) or tier-4 (``ALARM``) role's own
    busiest theme key may be resolved more often than the busiest tier-1
    (``FIELD``) role's -- the chroma-budget ordering E-1 describes
    (constant-render roles stay near-neutral; rare-render roles can spend
    full chroma) only holds if the roles that render constantly really are
    the ones resolved most often. A deliberate misordering of
    ``ROLE_FREQUENCY_TIER`` (tried locally, not committed) makes this
    assertion fail, proving it is a real gate."""
    counts = _count_theme_style_resolutions(monkeypatch)
    role_max = _role_max_resolution_counts(counts)
    assert role_max, "no theme-table style resolutions were observed"

    field_counts = [count for role, count in role_max.items() if ROLE_FREQUENCY_TIER[role] is FrequencyTier.FIELD]
    assert field_counts, "no FIELD-tier role backed any observed theme key"
    field_ceiling = max(field_counts)

    for role, count in role_max.items():
        if ROLE_FREQUENCY_TIER[role] in (FrequencyTier.EVENT, FrequencyTier.ALARM):
            assert count <= field_ceiling, (role, count, field_ceiling, role_max)
