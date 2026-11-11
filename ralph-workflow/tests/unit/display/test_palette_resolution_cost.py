from __future__ import annotations

import pytest

from ralph.display._palette import palette_solve_stats, reset_palette_solve_stats
from ralph.display.scene_catalog import (
    FULL_LAYOUT_WIDTH,
    SupportCase,
    render_scene,
)


def test_palette_solve_stats_returns_zero_zero_after_reset() -> None:
    reset_palette_solve_stats()
    assert palette_solve_stats() == (0, 0)


def test_palette_solve_stats_counts_real_solves_per_distinct_surface() -> None:
    reset_palette_solve_stats()
    from ralph.display._palette import resolve_palette

    resolve_palette("#2D2A2E")
    hits, real_solves = palette_solve_stats()
    assert real_solves == 1, (hits, real_solves)

    resolve_palette("#2D2A2E")
    hits, real_solves = palette_solve_stats()
    assert real_solves == 1, (hits, real_solves)
    assert hits == 1, (hits, real_solves)


def test_palette_solve_stats_counts_one_real_solve_per_distinct_surface() -> None:
    reset_palette_solve_stats()
    from ralph.display._palette import resolve_palette

    for surface in ("#2D2A2E", "#1E1E1E", "#FAF8F5"):
        resolve_palette(surface)
    hits, real_solves = palette_solve_stats()
    assert real_solves == 3, (hits, real_solves)
    assert hits == 0, (hits, real_solves)


@pytest.mark.criteria("F-2")
def test_render_scene_does_not_solve_palette_per_rendered_row() -> None:
    reset_palette_solve_stats()
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    rendered = render_scene("clean_run", case, terminal_bg_is_light=False)
    assert rendered, "render_scene must produce a non-empty render"
    hits, real_solves = palette_solve_stats()

    rendered_rows = rendered.count("\n")
    assert rendered_rows > real_solves, (
        rendered_rows,
        real_solves,
        "solver cost must not track rendered rows",
    )

    assert real_solves <= 3, (
        hits,
        real_solves,
        rendered_rows,
        f"real_solves={real_solves} exceeds the distinct-surface bound for clean_run",
    )
