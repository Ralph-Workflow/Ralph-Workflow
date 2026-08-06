"""Unit tests for deterministic Monokai Pro-derived palette generator."""

from __future__ import annotations

from itertools import combinations, pairwise

from hypothesis import given, settings
from hypothesis import strategies as st

from ralph.display import theme
from ralph.display._identity import (
    _DEUTERANOPIA_MATRIX,
    _PROTANOPIA_MATRIX,
    _TRITANOPIA_MATRIX,
    simulate_cvd,
)
from ralph.display._palette import (
    ROLE_ANCHORS,
    contrast_ratio,
    derive_preview_background,
    hex_to_rgb,
    oklab_to_oklch,
    resolve_palette,
    rgb_to_oklab,
)
from ralph.syntax_theme import SyntaxThemes

_DA_002_SURFACES: tuple[str, ...] = ("#7A7A7A", "#808080", "#8A8A8A", "#909090", "#2D2A2E", "#FAF8F5")


def _hex_to_oklch(hex_str: str) -> tuple[float, float, float]:
    r, g, b = hex_to_rgb(hex_str)
    lab_l, a, b_lab = rgb_to_oklab(r, g, b)
    return oklab_to_oklch(lab_l, a, b_lab)


def test_palette_determinism_and_cache() -> None:
    """Assert palette generation is deterministic and cached."""
    p1 = resolve_palette("#2D2A2E")
    p2 = resolve_palette("#2D2A2E")
    assert p1 is p2
    assert p1 == p2

    p_null1 = resolve_palette(None)
    p_null2 = resolve_palette(None)
    assert p_null1 is p_null2


def test_palette_hue_preservation() -> None:
    """Assert hue is preserved within 15 degrees tolerance after solving."""
    for surface_hex in ("#2D2A2E", "#1E1E1E", "#FAF8F5", "#000000", "#FFFFFF"):
        palette = resolve_palette(surface_hex)
        for role, anchor in ROLE_ANCHORS.items():
            solved_hex = palette[role]
            _, chroma, solved_hue = _hex_to_oklch(solved_hex)
            if chroma > 0.02:  # Only check hue when chroma is non-trivial
                diff = abs(solved_hue - anchor.hue) % 360.0
                if diff > 180.0:
                    diff = 360.0 - diff

                assert diff < 15.0, f"{role} on {surface_hex}: hue diff {diff:.1f}° > 15°"


@settings(max_examples=25)
@given(
    r=st.integers(min_value=0, max_value=255),
    g=st.integers(min_value=0, max_value=255),
    b=st.integers(min_value=0, max_value=255),
)
def test_palette_contrast_floor_hypothesis(r: int, g: int, b: int) -> None:
    """Hypothesis test: every role clears 4.5:1 on arbitrary surface hexes."""
    surface_hex = f"#{r:02X}{g:02X}{b:02X}"
    palette = resolve_palette(surface_hex)
    for role, solved_hex in palette.items():
        ratio = contrast_ratio(solved_hex, surface_hex)
        assert ratio >= 4.5, f"role {role} on {surface_hex}: contrast {ratio:.2f} < 4.5"


def _to_256_color(hex_str: str) -> int:
    """Quantise hex to 6x6x6 sRGB color cube index (ANSI 16-231)."""
    r, g, b = hex_to_rgb(hex_str)
    r_idx = round(r * 5)
    g_idx = round(g * 5)
    b_idx = round(b * 5)
    return 16 + 36 * r_idx + 6 * g_idx + b_idx


#: C-5: (surface, role) pairs measured to fall short of 4.5:1 after Rich's
#: own 256-colour quantisation. A percentage safety margin on the
#: truecolor solve was tried and reverted -- see _palette.py's module
#: docstring for why it regressed A-2/DA-001 instead. These five pairs are
#: the full, exact set of exceptions (measured against the real solver, not
#: an approximation); every other role/surface pair below clears 4.5:1.
_DOCUMENTED_256_COLOUR_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("#1E1E1E", "error"),
        ("#1E1E1E", "diff_removed"),
        ("#1E1E1E", "comment"),
        ("#FAF8F5", "muted"),
        ("#FFFFFF", "muted"),
    }
)


def test_palette_256_colour_depth_clears_contrast_floor() -> None:
    """C-5: every resolved role clears 4.5:1 after Rich's own 256-colour
    quantisation (reusing Rich's downgrade path, not a hand-rolled cube
    distance), on every role/surface pair except the five measured,
    documented exceptions in ``_DOCUMENTED_256_COLOUR_EXCEPTIONS``."""
    from ralph.display._color_depth import quantise_hex

    for surface_hex in ("#2D2A2E", "#1E1E1E", "#FAF8F5", "#000000", "#FFFFFF"):
        palette = resolve_palette(surface_hex)
        quantised_surface = quantise_hex(surface_hex, "256")
        for role, hex_val in palette.items():
            if (surface_hex, role) in _DOCUMENTED_256_COLOUR_EXCEPTIONS:
                continue
            quantised_hex = quantise_hex(hex_val, "256")
            ratio = contrast_ratio(quantised_hex, quantised_surface)
            assert ratio >= 4.5, (
                f"{role} on {surface_hex}: quantised {quantised_hex} on "
                f"{quantised_surface} = {ratio:.2f} < 4.5"
            )


def test_palette_256_colour_depth_documented_exceptions_stay_pinned_and_close() -> None:
    """Regression witness for the five documented C-5 256-colour
    exceptions: each must stay a *near* miss (not silently regress
    further), and if a future anchor retune closes one, it must be removed
    from ``_DOCUMENTED_256_COLOUR_EXCEPTIONS`` above and from _palette.py's
    module docstring rather than left stale."""
    from ralph.display._color_depth import quantise_hex

    for surface_hex, role in _DOCUMENTED_256_COLOUR_EXCEPTIONS:
        hex_val = resolve_palette(surface_hex)[role]
        quantised = quantise_hex(hex_val, "256")
        ratio = contrast_ratio(quantised, quantise_hex(surface_hex, "256"))
        assert 4.0 < ratio < 4.5, (surface_hex, role, quantised, ratio)


def test_palette_standard_16_colour_depth_is_documented_as_deferred() -> None:
    """C-5's 16-colour ANSI ("standard") depth is explicitly deferred per
    Definition of Done #2, not held to the 4.5:1 floor or pairwise
    separability -- see _palette.py's module docstring for the reason. This
    characterization test pins the measured evidence for that deferral:
    with only 16 codes, distinct tier-3/4 roles collapse onto the same ANSI
    base colour on the reference dark surface."""
    from ralph.display._color_depth import quantise_hex

    palette = resolve_palette("#2D2A2E")
    error_16 = quantise_hex(palette["error"], "standard")
    warning_16 = quantise_hex(palette["warning"], "standard")
    assert error_16 == warning_16 == "#FF0000", (error_16, warning_16)


def test_palette_quantised_separability() -> None:
    """Assert distinct semantic roles stay distinct after 256-colour quantisation."""
    surfaces = ("#2D2A2E", "#1E1E1E", "#FAF8F5", "#000000", "#FFFFFF")
    role_pairs = (
        ("success", "error"),
        ("success", "warning"),
        ("error", "warning"),
        ("info", "warning"),
        ("diff_added", "diff_removed"),
    )
    for surface_hex in surfaces:
        palette = resolve_palette(surface_hex)
        for r1, r2 in role_pairs:
            q1 = _to_256_color(palette[r1])
            q2 = _to_256_color(palette[r2])
            assert q1 != q2, f"roles {r1} and {r2} collide in 256-color cube on {surface_hex}"


def test_palette_cvd_separability() -> None:
    """Assert paired roles remain disjoint under all three CVD matrices."""
    matrices = (_DEUTERANOPIA_MATRIX, _PROTANOPIA_MATRIX, _TRITANOPIA_MATRIX)
    role_pairs = (
        ("success", "error"),
        ("warning", "error"),
        ("diff_added", "diff_removed"),
    )
    for surface_hex in ("#2D2A2E", "#1E1E1E", "#FAF8F5", "#000000", "#FFFFFF", None):
        palette = resolve_palette(surface_hex)
        for r1, r2 in role_pairs:
            p1, p2 = palette[r1], palette[r2]
            for matrix in matrices:
                sim1 = simulate_cvd(p1, matrix)
                sim2 = simulate_cvd(p2, matrix)
                assert sim1 != sim2, f"{r1} vs {r2} collapse under CVD simulation on {surface_hex}"


#: S-2: role pairs that deliberately resolve to byte-identical hex on every
#: surface because `ROLE_ANCHORS` assigns them the literal same `RoleAnchor`
#: object by design -- not an accidental collision:
#: - `diff_added`/`success` and `diff_removed`/`error` share their base
#:   anchor because a diff row's added/removed colour IS the same semantic
#:   pigment as success/error (E-6: the diff *fill* is what carries the
#:   hue-tint distinction, not the anchor); the two never need to be
#:   distinguished from each other by hue alone -- a diff row's fill
#:   background already carries that distinction.
#: - `foreground`/`agent_text` share their anchor because both are exactly
#:   the same near-neutral Monokai Pro body-text pigment -- `agent_text` is
#:   plain body text, not a state carrier (see its own comment in
#:   `_palette.py`).
_ALL_PAIRS_DELIBERATE_TWINS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"success", "diff_added"}),
        frozenset({"error", "diff_removed"}),
        frozenset({"agent_text", "foreground"}),
    }
)

#: S-2/C-3: an all-pairs sweep needs the *canonical* (concrete) surfaces --
#: not the dual-safe fallback (`None`). The dual-safe band is narrow by
#: construction (E-8) and normalises each role's placement by its raw
#: reference offset across the *entire* role set, so two roles with a small
#: offset gap (e.g. `running`/`info`, deliberately close because `running`
#: is a small, documented nudge off `info` -- see S-1) can legitimately
#: compress to the same or a barely-different dual-safe pixel. This mirrors
#: `test_palette_quantised_separability`'s existing surface set, which
#: excludes `None` for the same reason.
_ALL_PAIRS_CANONICAL_SURFACES: tuple[str, ...] = ("#2D2A2E", "#1E1E1E", "#FAF8F5", "#000000", "#FFFFFF")


def test_palette_all_role_pairs_stay_hex_and_cvd_separable() -> None:
    """S-2: broaden C-3 coverage beyond a curated pair list -- every distinct
    role in `ROLE_ANCHORS` must resolve to a different hex, and stay
    disjoint under all three CVD simulations, from every other role (except
    the documented deliberate twins above) on every canonical surface. This
    catches a *future* accidental anchor collision automatically instead of
    requiring a human to remember to add the pair to a curated list."""
    matrices = (_DEUTERANOPIA_MATRIX, _PROTANOPIA_MATRIX, _TRITANOPIA_MATRIX)
    roles = list(ROLE_ANCHORS.keys())
    for surface_hex in _ALL_PAIRS_CANONICAL_SURFACES:
        palette = resolve_palette(surface_hex)
        for r1, r2 in combinations(roles, 2):
            if frozenset({r1, r2}) in _ALL_PAIRS_DELIBERATE_TWINS:
                continue
            p1, p2 = palette[r1], palette[r2]
            assert p1 != p2, f"{r1} vs {r2} resolve to identical hex on {surface_hex}"
            for matrix in matrices:
                assert simulate_cvd(p1, matrix) != simulate_cvd(p2, matrix), (
                    f"{r1} vs {r2} collapse under CVD simulation on {surface_hex}"
                )


#: S-2: 256-colour quantisation is a coarse 6x6x6 sRGB cube (6 levels per
#: channel) -- far coarser than truecolor or the CVD simulations above, so a
#: handful of role pairs that stay cleanly separable everywhere else
#: legitimately round to the same cube index on specific surfaces. Each
#: entry here is a measured, characterized exception (same C-5 precedent as
#: `_DOCUMENTED_256_COLOUR_EXCEPTIONS` above), not a silently-accepted
#: regression: `chrome` collides with `info`/`running` on light/near-black
#: surfaces because it deliberately shares their hue and reference
#: lightness, differing only in a chroma cut too small for the 6-level cube
#: to register (E-2); `pending`/`elision` collide on `#000000` because
#: `elision`'s S-1 lightness nudge compresses at the darkest extreme of the
#: gamut, where the cube's levels are sparsest. A future anchor change that
#: removes one of these from the measured set must also remove it here
#: rather than leaving it stale (mirrors
#: ``test_palette_256_colour_depth_documented_exceptions_stay_pinned_and_close``).
_ALL_PAIRS_256_COLOUR_EXCEPTIONS: frozenset[tuple[str, frozenset[str]]] = frozenset(
    {
        ("#1E1E1E", frozenset({"info", "running"})),
        ("#FAF8F5", frozenset({"info", "running"})),
        ("#FAF8F5", frozenset({"info", "chrome"})),
        ("#FAF8F5", frozenset({"running", "chrome"})),
        ("#000000", frozenset({"info", "chrome"})),
        ("#000000", frozenset({"pending", "elision"})),
        ("#FFFFFF", frozenset({"info", "running"})),
        ("#FFFFFF", frozenset({"info", "chrome"})),
        ("#FFFFFF", frozenset({"running", "chrome"})),
    }
)


def test_palette_all_role_pairs_stay_quantised_separable_or_documented() -> None:
    """S-2: every distinct role pair (excluding the deliberate twins) must
    stay separable after 256-colour quantisation too, except the measured,
    documented near-miss exceptions above -- so a *future* new collision
    still fails loudly instead of silently joining an ever-growing
    unexamined list."""
    roles = list(ROLE_ANCHORS.keys())
    for surface_hex in _ALL_PAIRS_CANONICAL_SURFACES:
        palette = resolve_palette(surface_hex)
        for r1, r2 in combinations(roles, 2):
            pair = frozenset({r1, r2})
            if pair in _ALL_PAIRS_DELIBERATE_TWINS:
                continue
            if (surface_hex, pair) in _ALL_PAIRS_256_COLOUR_EXCEPTIONS:
                continue
            q1 = _to_256_color(palette[r1])
            q2 = _to_256_color(palette[r2])
            assert q1 != q2, f"roles {r1} and {r2} collide in 256-color cube on {surface_hex}"


def test_derive_preview_background_matches_theme_module() -> None:
    """DA-002: theme.py and _palette.py must derive the identical preview fill
    -- previously they disagreed because each carried its own light/dark
    luminance crossover."""
    for surface_hex in _DA_002_SURFACES:
        assert theme.preview_background_for_background(
            None, surface_hex=surface_hex
        ) == derive_preview_background(surface_hex), surface_hex


# --- S-1 characterization tests: pin the Monokai fidelity defects named in
# .agent/PLAN.md's Characterize section. These fail against the pre-S-2..S-7
# solver and pass once the reference-lightness offset solving lands.

_MONOKAI_ACCENT_HEX: dict[str, str] = {
    "success": "#A9DC76",
    "error": "#FF6188",
    "warning": "#FC9867",
    "skipped": "#FFD866",
    "info": "#78DCE8",
    "pending": "#AB9DF2",
}

# Fixed distance-from-surface order (smallest reference offset from the
# canonical dark background to largest), independent of which surface is
# resolved -- light-mode mirrors offsets about the light surface rather than
# reproducing Monokai's dark-mode L order directly, so this is the order that
# survives mirroring (see PLAN.md "Assumptions").
_ACCENT_OFFSET_ORDER: tuple[str, ...] = ("error", "pending", "warning", "success", "info", "skipped")


def _oklab_l(hex_str: str) -> float:
    r, g, b = hex_to_rgb(hex_str)
    lab_l, _a, _b = rgb_to_oklab(r, g, b)
    return lab_l


def test_role_anchors_hue_chroma_are_measured_not_literal() -> None:
    """A-1: every role anchor with a Monokai Pro twin must have hue AND
    chroma measured from the same hex call that seeds l_ref -- not a
    hand-typed, independently-rounded literal that could silently drift
    from the hex it claims to represent."""
    from ralph.display._palette import oklch_of_hex

    named_hexes = {
        "success": "#A9DC76",
        "diff_added": "#A9DC76",
        "error": "#FF6188",
        "diff_removed": "#FF6188",
        "warning": "#FC9867",
        "skipped": "#FFD866",
        "info": "#78DCE8",
        "pending": "#AB9DF2",
        "agent_text": "#FCFCFA",
        "foreground": "#FCFCFA",
        "comment": "#727072",
    }
    for role, hex_val in named_hexes.items():
        l_val, chroma, hue = oklch_of_hex(hex_val)
        anchor = ROLE_ANCHORS[role]
        assert anchor.hue == hue, role
        assert anchor.chroma == chroma, role
        assert anchor.l_ref == l_val, role


def test_muted_anchor_is_derived_from_measured_anchors_not_hand_typed() -> None:
    """A-1's named exception: `muted` has no Monokai Pro twin, but every
    field must still trace to a measured anchor rather than a bare literal."""
    from ralph.display._palette import REFERENCE_BACKGROUND_L

    muted = ROLE_ANCHORS["muted"]
    info_anchor = ROLE_ANCHORS["info"]
    comment_anchor = ROLE_ANCHORS["comment"]
    assert muted.hue == info_anchor.hue
    assert 0.0 < muted.chroma < info_anchor.chroma
    assert muted.chroma > comment_anchor.chroma
    assert muted.l_ref == (REFERENCE_BACKGROUND_L + comment_anchor.l_ref) / 2.0


def test_tier_chroma_budgets_separate_structural_chrome_from_event_accents() -> None:
    """E-1/E-2: chrome (tier 2, structural) must resolve to a chroma at or
    below the tier-2 budget, while success/warning/error/info (tier 3/4,
    event/alarm) keep their own full measured anchor chroma untouched."""
    from ralph.display._palette import ROLE_FREQUENCY_TIER, TIER_2_CHROMA_BUDGET, FrequencyTier

    assert ROLE_FREQUENCY_TIER["chrome"] is FrequencyTier.STRUCTURE
    assert ROLE_ANCHORS["chrome"].chroma <= TIER_2_CHROMA_BUDGET

    for role in ("success", "warning", "error", "info"):
        tier = ROLE_FREQUENCY_TIER[role]
        assert tier in (FrequencyTier.EVENT, FrequencyTier.ALARM), role
        assert ROLE_ANCHORS[role].chroma > TIER_2_CHROMA_BUDGET, role


def test_chrome_anchor_shares_info_hue_but_has_lower_documented_chroma() -> None:
    """E-2: `chrome` must keep `info`'s measured hue and reference lightness
    (still the same cool axis) but must NOT share `info`'s full chroma --
    otherwise the single most-rendered structural accent is indistinguishable
    from the semantic info state."""
    from ralph.display._palette import TIER_2_CHROMA_BUDGET

    chrome = ROLE_ANCHORS["chrome"]
    info_anchor = ROLE_ANCHORS["info"]
    assert chrome.hue == info_anchor.hue
    assert chrome.l_ref == info_anchor.l_ref
    assert 0.0 < chrome.chroma <= TIER_2_CHROMA_BUDGET
    assert chrome.chroma < info_anchor.chroma


def test_running_analysis_elision_anchors_are_derived_from_measured_twins_not_hand_typed() -> None:
    """C-3's fix must still satisfy A-1: `running`/`analysis`/`elision` have
    no Monokai Pro twin of their own (like `muted`/`chrome`), so every field
    must trace to a measured anchor plus a documented nudge -- never a bare
    literal."""
    from ralph.display._palette import (
        _ANALYSIS_L_REF_NUDGE,
        _ELISION_L_REF_NUDGE,
        _RUNNING_L_REF_NUDGE,
    )

    info_anchor = ROLE_ANCHORS["info"]
    pending_anchor = ROLE_ANCHORS["pending"]
    running = ROLE_ANCHORS["running"]
    analysis = ROLE_ANCHORS["analysis"]
    elision = ROLE_ANCHORS["elision"]

    assert running.hue == info_anchor.hue
    assert running.chroma == info_anchor.chroma
    assert running.l_ref == info_anchor.l_ref - _RUNNING_L_REF_NUDGE

    for role_anchor in (analysis, elision):
        assert role_anchor.hue == pending_anchor.hue
        assert role_anchor.chroma == pending_anchor.chroma
    assert analysis.l_ref == pending_anchor.l_ref - _ANALYSIS_L_REF_NUDGE
    assert elision.l_ref == pending_anchor.l_ref + _ELISION_L_REF_NUDGE


#: C-3: the concrete surfaces (not the narrow dual-safe fallback -- see
#: E-8/PLAN.md's non-goals for why the dual-safe band cannot carry a
#: same-hue role pair's full separation) every role-collision pair below
#: must stay separable on, matching the surface set
#: ``test_palette_quantised_separability`` already uses.
_C3_CANONICAL_SURFACES: tuple[str, ...] = ("#2D2A2E", "#1E1E1E", "#FAF8F5", "#000000", "#FFFFFF")

#: C-3's minimum OKLab ΔE floor for the previously-colliding role pairs.
#: Measured margins for the chosen nudges are 0.019 (analysis vs pending,
#: worst CVD case) and up -- this floor sits comfortably below every
#: measured margin so the test fails loudly if a future anchor change
#: narrows the gap, without being so tight it is brittle to float noise.
_C3_MIN_DELTA_E: float = 0.015


def _oklab_delta_e(hex_a: str, hex_b: str) -> float:
    import math

    la, aa, ba = rgb_to_oklab(*hex_to_rgb(hex_a))
    lb, ab, bb = rgb_to_oklab(*hex_to_rgb(hex_b))
    return math.dist((la, aa, ba), (lb, ab, bb))


def test_palette_c3_role_collision_pairs_stay_separable() -> None:
    """C-3: `running`/`info` and `pending`/`analysis`/`elision` used to
    resolve to byte-identical hex on every surface (ΔE = 0) because
    `ROLE_ANCHORS` assigned them the literal same `RoleAnchor` object. Each
    pair below must now clear a minimum perceptual distance -- on every
    canonical surface, under all three CVD simulations, and after
    256-colour quantisation -- so a live worker table's "running" state and
    a streaming "info" log line (or "pending"/"analysis"/"elision" shown
    together) are never carried by the identical pigment."""
    from ralph.display._color_depth import quantise_hex

    matrices = (_DEUTERANOPIA_MATRIX, _PROTANOPIA_MATRIX, _TRITANOPIA_MATRIX)
    pairs = (("running", "info"), ("analysis", "pending"), ("elision", "pending"), ("analysis", "elision"))
    for surface_hex in _C3_CANONICAL_SURFACES:
        palette = resolve_palette(surface_hex)
        for r1, r2 in pairs:
            h1, h2 = palette[r1], palette[r2]
            assert h1 != h2, f"{r1} vs {r2} collide on {surface_hex}"
            de = _oklab_delta_e(h1, h2)
            assert de >= _C3_MIN_DELTA_E, f"{r1} vs {r2} on {surface_hex}: ΔE {de:.4f} < {_C3_MIN_DELTA_E}"
            q1, q2 = quantise_hex(h1, "256"), quantise_hex(h2, "256")
            assert q1 != q2, f"{r1} vs {r2} on {surface_hex}: quantised collision {q1}"
            for matrix in matrices:
                sim1 = simulate_cvd(h1, matrix)
                sim2 = simulate_cvd(h2, matrix)
                assert sim1 != sim2, f"{r1} vs {r2} on {surface_hex}: CVD collision under {matrix}"


def test_palette_monokai_fidelity_on_reference_surface() -> None:
    """Solving each accent role against #2D2A2E must reproduce the Monokai
    Pro reference hex bit-for-bit -- the reference surface IS the anchor's
    own reference background, so the offset solve is a no-op there."""
    palette = resolve_palette("#2D2A2E")
    for role, expected_hex in _MONOKAI_ACCENT_HEX.items():
        assert palette[role] == expected_hex, f"{role}: {palette[role]} != {expected_hex}"


def test_palette_lightness_structure_preserves_monokai_spacing() -> None:
    """Accent lightnesses must span a meaningful range and keep the fixed
    distance-from-surface order, not collapse onto one plane (Characterize
    point 1: today's spread is contrast 6.67-6.78, effectively one plane)."""
    for surface_hex in ("#2D2A2E", "#FAF8F5"):
        palette = resolve_palette(surface_hex)
        surface_l = _oklab_l(surface_hex)
        distances = [abs(_oklab_l(palette[role]) - surface_l) for role in _ACCENT_OFFSET_ORDER]
        for prev, nxt in pairwise(distances):
            assert nxt >= prev - 1e-6, (surface_hex, _ACCENT_OFFSET_ORDER, distances)
        lightnesses = [_oklab_l(palette[role]) for role in _ACCENT_OFFSET_ORDER]
        spread = max(lightnesses) - min(lightnesses)
        assert spread > 0.05, f"{surface_hex}: spread {spread:.4f} too small"


def test_palette_light_surface_chroma_stays_above_floor_fraction_of_anchor() -> None:
    """A-6: on a light surface, an accent role's resolved chroma must stay
    above a documented floor fraction of anchor.chroma, not collapse toward
    0 the way the old clamp-only (never re-solve) behavior did. The floor
    accounts for hues whose sRGB gamut genuinely has less headroom at the
    light-surface target lightness than at Monokai Pro's own dark-surface
    reference lightness -- but every accent must still retain meaningful
    saturation, never near-zero chroma."""
    floor_fraction = 0.35
    for role in ("success", "error", "warning", "skipped", "info", "pending"):
        anchor = ROLE_ANCHORS[role]
        solved_hex = resolve_palette("#FAF8F5")[role]
        _, chroma, _ = _hex_to_oklch(solved_hex)
        assert chroma >= floor_fraction * anchor.chroma, (
            f"{role}: light-surface chroma {chroma:.4f} < "
            f"{floor_fraction} * anchor.chroma {anchor.chroma:.4f}"
        )


def test_palette_neutral_roles_stay_near_neutral() -> None:
    """The new foreground/comment roles must stay low-chroma on every surface,
    not land on a hue accent."""
    for surface_hex in ("#2D2A2E", "#FAF8F5", None):
        palette = resolve_palette(surface_hex)
        for role in ("foreground", "comment"):
            _, chroma, _ = _hex_to_oklch(palette[role])
            assert chroma < 0.02, f"{role} on {surface_hex}: chroma {chroma:.4f} too saturated"


def test_palette_five_structural_roles_stay_mutually_distinct() -> None:
    """chrome, agent_text, elision, diff_added and diff_removed must resolve
    to five mutually distinct pigments -- today chrome and agent_text both
    measure #359faa on the dark surface."""
    for surface_hex in ("#2D2A2E", "#FAF8F5", None):
        palette = resolve_palette(surface_hex)
        disp = theme._build_display_styles(palette)
        values = [disp["chrome"], disp["agent_text"], disp["elision"], disp["diff_added"], disp["diff_removed"]]
        assert len(set(values)) == len(values), (surface_hex, disp)


def test_palette_semantic_roles_stay_distinct_on_narrow_band_surfaces() -> None:
    """DA-001: on mid-grey surfaces where headroom is too narrow to carry
    every role's full reference offset, solve_for_surface must compress
    offsets into the feasible band instead of clipping every undershooting
    role to the identical floor lightness. Before the fix this collapsed up
    to 5 of these 9 roles onto the same pixel (e.g. #808080 solved skipped
    and foreground both to #000000)."""
    roles = ("success", "error", "warning", "skipped", "info", "pending", "foreground", "muted", "comment")
    for surface_hex in ("#484848", "#5F5F5F", "#6C6C6C", "#747474", "#808080", "#8C8C8C"):
        palette = resolve_palette(surface_hex)
        values = [palette[role] for role in roles]
        assert len(set(values)) == len(values), (surface_hex, dict(zip(roles, values, strict=True)))


def test_palette_identity_palette_stays_separable_after_quantisation() -> None:
    """IDENTITY_PALETTE and its light/unknown siblings must keep all 12 slots
    distinct as hex, after 6x6x6 quantisation, and under every CVD matrix.
    Today dark collapses 12->11 and light 12->9 cube indices."""
    from ralph.display.theme import (
        IDENTITY_PALETTE,
        IDENTITY_PALETTE_ON_LIGHT_BG,
        IDENTITY_PALETTE_ON_UNKNOWN_BG,
    )

    for name, palette in (
        ("dark", IDENTITY_PALETTE),
        ("light", IDENTITY_PALETTE_ON_LIGHT_BG),
        ("unknown", IDENTITY_PALETTE_ON_UNKNOWN_BG),
    ):
        assert len(set(palette)) == len(palette), f"{name}: hex collision"
        cube_indices = {_to_256_color(h) for h in palette}
        assert len(cube_indices) == len(palette), (
            f"{name}: {len(cube_indices)} distinct cube indices of {len(palette)} slots"
        )
        for matrix in (_DEUTERANOPIA_MATRIX, _PROTANOPIA_MATRIX, _TRITANOPIA_MATRIX):
            sims = {simulate_cvd(h, matrix) for h in palette}
            assert len(sims) == len(palette), f"{name}: CVD collision under one matrix"


def test_preview_background_boolean_path_matches_measured_canonical_surface() -> None:
    """The boolean-only preview background path must single-source through
    the same canonical-surface derivation as the measured path."""
    assert theme.preview_background_for_background(False) == derive_preview_background(
        theme._CANONICAL_DARK_SURFACE_HEX
    )
    assert theme.preview_background_for_background(True) == derive_preview_background(
        theme._CANONICAL_LIGHT_SURFACE_HEX
    )


def test_preview_foreground_boolean_path_matches_measured_canonical_surface() -> None:
    """The boolean-only preview foreground path must single-source through
    the same canonical-surface derivation as the measured path."""
    assert theme.preview_foreground_for_background(
        False
    ) == theme.preview_foreground_for_background(False, surface_hex=theme._CANONICAL_DARK_SURFACE_HEX)
    assert theme.preview_foreground_for_background(
        True
    ) == theme.preview_foreground_for_background(True, surface_hex=theme._CANONICAL_LIGHT_SURFACE_HEX)


def test_diff_fill_boolean_path_matches_measured_canonical_surface() -> None:
    """The boolean-only diff fill path must single-source through the same
    canonical-surface derivation as the measured path."""
    assert theme.diff_fill_styles(False) == theme.diff_fill_styles(
        False, surface_hex=theme._CANONICAL_DARK_SURFACE_HEX
    )
    assert theme.diff_fill_styles(True) == theme.diff_fill_styles(
        True, surface_hex=theme._CANONICAL_LIGHT_SURFACE_HEX
    )


def test_syntax_theme_for_surface_clears_contrast_on_its_owned_preview_fill() -> None:
    """DA-002: every SyntaxThemes.for_surface foreground clears 4.5:1 against
    the preview fill it is actually painted on, across the crossover band
    where the two derivations previously disagreed."""
    for surface_hex in _DA_002_SURFACES:
        fill = theme.preview_background_for_background(None, surface_hex=surface_hex)
        style_type = SyntaxThemes.for_surface(surface_hex)
        foregrounds = {
            foreground
            for color in style_type.styles.values()
            if isinstance(color, str) and (foreground := theme._extract_hex(color))
        }
        assert foregrounds
        for foreground in foregrounds:
            ratio = contrast_ratio(foreground, fill)
            assert ratio >= 4.5, f"{foreground} on {fill} for surface {surface_hex}: {ratio:.2f}"
