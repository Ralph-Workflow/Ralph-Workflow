from __future__ import annotations

import collections
from typing import TYPE_CHECKING

import pytest
from pygments.lexers import get_lexer_by_name
from rich.console import Console

from ralph.display import theme as theme_mod
from ralph.display._frequency_tier import FrequencyTier
from ralph.display._palette import ROLE_FREQUENCY_TIER, resolve_palette
from ralph.display.scene_catalog import FULL_LAYOUT_WIDTH, SCENE_NAMES, SupportCase, render_scene

if TYPE_CHECKING:
    from rich.style import Style

_PROBE_CASE: SupportCase = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
_PROBE_SURFACE_HEX: str = "#2D2A2E"


@pytest.fixture(scope="module", autouse=True)
def _warm_python_lexer() -> None:
    get_lexer_by_name("python")


def _count_theme_style_resolutions(monkeypatch: pytest.MonkeyPatch) -> collections.Counter[str]:
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
    palette = {
        role: hex_val.upper() for role, hex_val in resolve_palette(_PROBE_SURFACE_HEX).items()
    }
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
    counts = _count_theme_style_resolutions(monkeypatch)
    role_max = _role_max_resolution_counts(counts)
    assert role_max, "no theme-table style resolutions were observed"

    top_role = max(role_max, key=role_max.__getitem__)
    assert ROLE_FREQUENCY_TIER[top_role] is FrequencyTier.FIELD, (top_role, role_max)


def test_frequency_tier_evidence_no_event_or_alarm_role_out_resolves_the_top_field_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = _count_theme_style_resolutions(monkeypatch)
    role_max = _role_max_resolution_counts(counts)
    assert role_max, "no theme-table style resolutions were observed"

    field_counts = [
        count
        for role, count in role_max.items()
        if ROLE_FREQUENCY_TIER[role] is FrequencyTier.FIELD
    ]
    assert field_counts, "no FIELD-tier role backed any observed theme key"
    field_ceiling = max(field_counts)

    for role, count in role_max.items():
        if ROLE_FREQUENCY_TIER[role] in (FrequencyTier.EVENT, FrequencyTier.ALARM):
            assert count <= field_ceiling, (role, count, field_ceiling, role_max)
