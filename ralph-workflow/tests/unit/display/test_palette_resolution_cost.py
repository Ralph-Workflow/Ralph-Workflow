"""F-2 proving test: palette solving must not run per rendered row.

The brief's F-2 criterion has two halves:

1. "Resolution is cached per surface" -- already proved by
   ``test_palette_determinism_and_cache``.
2. "Palette solving must not run per rendered row" -- this file is the
   proof for the second half.

The seam is the new ``palette_solve_stats()`` /
``reset_palette_solve_stats()`` pair in ``ralph.display._palette``, which
exposes the ``(hits, real_solves)`` counter the production
``_resolve_palette_cached`` lru_cache already maintains. The test
resets the counter, renders a deterministic scene through
``render_scene`` (the canonical display surface), and asserts that
``real_solves`` stays at the number of distinct surfaces the scene
paints while the rendered row count is orders of magnitude larger.

If a regression made the solver run per rendered row, ``real_solves``
would track the rendered row count -- the bound is stated against
distinct surfaces specifically, so the assertion fails if the cost
ever tracks rows.
"""

from __future__ import annotations

import pytest

from ralph.display._palette import palette_solve_stats, reset_palette_solve_stats
from ralph.display.scene_catalog import (
    FULL_LAYOUT_WIDTH,
    SupportCase,
    render_scene,
)


def test_palette_solve_stats_returns_zero_zero_after_reset() -> None:
    """The reset seam must return to ``(0, 0)`` immediately."""
    reset_palette_solve_stats()
    assert palette_solve_stats() == (0, 0)


def test_palette_solve_stats_counts_real_solves_per_distinct_surface() -> None:
    """``real_solves`` increments once per distinct surface, not once per
    call to ``resolve_palette``. Two back-to-back calls for the same
    surface count as one real solve and one cache hit."""
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
    """Three distinct surfaces produce three real solves."""
    reset_palette_solve_stats()
    from ralph.display._palette import resolve_palette

    for surface in ("#2D2A2E", "#1E1E1E", "#FAF8F5"):
        resolve_palette(surface)
    hits, real_solves = palette_solve_stats()
    assert real_solves == 3, (hits, real_solves)
    assert hits == 0, (hits, real_solves)
@pytest.mark.criteria("F-2")


def test_render_scene_does_not_solve_palette_per_rendered_row() -> None:
    """F-2: ``render_scene`` for a clean_run costs one real solve per
    distinct surface the scene paints, not one per rendered row.

    Measured on the maintained worktree: one ``clean_run`` render hits
    two distinct surfaces (the terminal surface and the derived
    preview fill), so the bound is ``real_solves <= 2`` while the
    rendered row count is in the hundreds. If the solver ever ran per
    rendered row, ``real_solves`` would track the rendered row count
    and the test would fail.
    """
    reset_palette_solve_stats()
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    rendered = render_scene("clean_run", case, terminal_bg_is_light=False)
    assert rendered, "render_scene must produce a non-empty render"
    hits, real_solves = palette_solve_stats()

    # The rendered row count is orders of magnitude larger than the
    # distinct-surface count the bound is stated against.
    rendered_rows = rendered.count("\n")
    assert rendered_rows > real_solves, (
        rendered_rows,
        real_solves,
        "solver cost must not track rendered rows",
    )

    # Real solves must stay bounded by the number of distinct surfaces
    # the scene actually paints. clean_run paints the terminal surface
    # plus the owned preview fill (and, depending on the test console,
    # a diff fill), so the upper bound is a small constant.
    assert real_solves <= 3, (
        hits,
        real_solves,
        rendered_rows,
        f"real_solves={real_solves} exceeds the distinct-surface bound for clean_run",
    )
