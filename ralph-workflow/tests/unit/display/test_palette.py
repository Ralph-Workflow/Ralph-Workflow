from __future__ import annotations

from itertools import combinations, pairwise

import pytest

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
    relative_luminance,
    resolve_palette,
    rgb_to_oklab,
)
from ralph.display.syntax_theme import SyntaxThemes

_DA_002_SURFACES: tuple[str, ...] = (
    "#7A7A7A",
    "#808080",
    "#8A8A8A",
    "#909090",
    "#2D2A2E",
    "#FAF8F5",
)


def _hex_to_oklch(hex_str: str) -> tuple[float, float, float]:
    r, g, b = hex_to_rgb(hex_str)
    lab_l, a, b_lab = rgb_to_oklab(r, g, b)
    return oklab_to_oklch(lab_l, a, b_lab)


@pytest.mark.criteria("F-1")
def test_palette_determinism_and_cache() -> None:
    p1 = resolve_palette("#2D2A2E")
    p2 = resolve_palette("#2D2A2E")
    assert p1 is p2
    assert p1 == p2

    p_null1 = resolve_palette(None)
    p_null2 = resolve_palette(None)
    assert p_null1 is p_null2


@pytest.mark.criteria("A-4")
def test_palette_hue_preservation() -> None:
    for surface_hex in ("#2D2A2E", "#1E1E1E", "#FAF8F5", "#000000", "#FFFFFF"):
        palette = resolve_palette(surface_hex)
        for role, anchor in ROLE_ANCHORS.items():
            solved_hex = palette[role]
            _, chroma, solved_hue = _hex_to_oklch(solved_hex)
            if chroma > 0.02:
                diff = abs(solved_hue - anchor.hue) % 360.0
                if diff > 180.0:
                    diff = 360.0 - diff

                assert diff < 15.0, f"{role} on {surface_hex}: hue diff {diff:.1f}° > 15°"


@pytest.mark.parametrize(
    ("r", "g", "b"),
    [(i * 73 % 256, i * 151 % 256, i * 211 % 256) for i in range(25)],
)
@pytest.mark.criteria("C-7")
def test_palette_contrast_floor_samples(r: int, g: int, b: int) -> None:
    surface_hex = f"#{r:02X}{g:02X}{b:02X}"
    palette = resolve_palette(surface_hex)
    for role, solved_hex in palette.items():
        ratio = contrast_ratio(solved_hex, surface_hex)
        assert ratio >= 4.5, f"role {role} on {surface_hex}: contrast {ratio:.2f} < 4.5"


def _to_256_color(hex_str: str) -> int:
    r, g, b = hex_to_rgb(hex_str)
    r_idx = round(r * 5)
    g_idx = round(g * 5)
    b_idx = round(b * 5)
    return 16 + 36 * r_idx + 6 * g_idx + b_idx


_DOCUMENTED_256_COLOUR_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("#1E1E1E", "error"),
        ("#1E1E1E", "diff_removed"),
        ("#1E1E1E", "comment"),
        ("#FAF8F5", "muted"),
        ("#FFFFFF", "muted"),
    }
)


@pytest.mark.criteria("C-5")
def test_palette_256_colour_depth_clears_contrast_floor() -> None:
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
    from ralph.display._color_depth import quantise_hex

    for surface_hex, role in _DOCUMENTED_256_COLOUR_EXCEPTIONS:
        hex_val = resolve_palette(surface_hex)[role]
        quantised = quantise_hex(hex_val, "256")
        ratio = contrast_ratio(quantised, quantise_hex(surface_hex, "256"))
        assert 4.0 < ratio < 4.5, (surface_hex, role, quantised, ratio)


def test_palette_standard_16_colour_depth_is_documented_as_deferred() -> None:
    from ralph.display._color_depth import quantise_hex

    palette = resolve_palette("#2D2A2E")
    error_16 = quantise_hex(palette["error"], "standard")
    warning_16 = quantise_hex(palette["warning"], "standard")
    assert error_16 == warning_16 == "#FF0000", (error_16, warning_16)


def test_palette_quantised_separability() -> None:
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


_ALL_PAIRS_DELIBERATE_TWINS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"success", "diff_added"}),
        frozenset({"error", "diff_removed"}),
        frozenset({"agent_text", "foreground"}),
    }
)

_ALL_PAIRS_CANONICAL_SURFACES: tuple[str, ...] = (
    "#2D2A2E",
    "#1E1E1E",
    "#FAF8F5",
    "#000000",
    "#FFFFFF",
)


def test_palette_all_role_pairs_stay_hex_and_cvd_separable() -> None:
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
    for surface_hex in _DA_002_SURFACES:
        assert theme.preview_background_for_background(
            None, surface_hex=surface_hex
        ) == derive_preview_background(surface_hex), surface_hex


_MONOKAI_ACCENT_HEX: dict[str, str] = {
    "success": "#A9DC76",
    "error": "#FF6188",
    "warning": "#FC9867",
    "skipped": "#FFD866",
    "info": "#78DCE8",
    "pending": "#AB9DF2",
}

_ACCENT_OFFSET_ORDER: tuple[str, ...] = (
    "error",
    "pending",
    "warning",
    "success",
    "info",
    "skipped",
)


def _oklab_l(hex_str: str) -> float:
    r, g, b = hex_to_rgb(hex_str)
    lab_l, _a, _b = rgb_to_oklab(r, g, b)
    return lab_l


@pytest.mark.criteria("A-1")
def test_role_anchors_hue_chroma_are_measured_not_literal() -> None:
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
    from ralph.display._palette import REFERENCE_BACKGROUND_L

    muted = ROLE_ANCHORS["muted"]
    info_anchor = ROLE_ANCHORS["info"]
    comment_anchor = ROLE_ANCHORS["comment"]
    assert muted.hue == info_anchor.hue
    assert 0.0 < muted.chroma < info_anchor.chroma
    assert muted.chroma > comment_anchor.chroma
    assert muted.l_ref == (REFERENCE_BACKGROUND_L + comment_anchor.l_ref) / 2.0


@pytest.mark.criteria("E-2")
def test_tier_chroma_budgets_separate_structural_chrome_from_event_accents() -> None:
    from ralph.display._palette import ROLE_FREQUENCY_TIER, TIER_2_CHROMA_BUDGET, FrequencyTier

    assert ROLE_FREQUENCY_TIER["chrome"] is FrequencyTier.STRUCTURE
    assert ROLE_ANCHORS["chrome"].chroma <= TIER_2_CHROMA_BUDGET

    for role in ("success", "warning", "error", "info"):
        tier = ROLE_FREQUENCY_TIER[role]
        assert tier in (FrequencyTier.EVENT, FrequencyTier.ALARM), role
        assert ROLE_ANCHORS[role].chroma > TIER_2_CHROMA_BUDGET, role


def test_chrome_anchor_shares_info_hue_but_has_lower_documented_chroma() -> None:
    from ralph.display._palette import TIER_2_CHROMA_BUDGET

    chrome = ROLE_ANCHORS["chrome"]
    info_anchor = ROLE_ANCHORS["info"]
    assert chrome.hue == info_anchor.hue
    assert chrome.l_ref == info_anchor.l_ref
    assert 0.0 < chrome.chroma <= TIER_2_CHROMA_BUDGET
    assert chrome.chroma < info_anchor.chroma


def test_running_analysis_elision_anchors_are_derived_from_measured_twins_not_hand_typed() -> None:
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


_C3_CANONICAL_SURFACES: tuple[str, ...] = ("#2D2A2E", "#1E1E1E", "#FAF8F5", "#000000", "#FFFFFF")

_C3_MIN_DELTA_E: float = 0.015


def _oklab_delta_e(hex_a: str, hex_b: str) -> float:
    import math

    la, aa, ba = rgb_to_oklab(*hex_to_rgb(hex_a))
    lb, ab, bb = rgb_to_oklab(*hex_to_rgb(hex_b))
    return math.dist((la, aa, ba), (lb, ab, bb))


@pytest.mark.criteria("C-3")
def test_palette_c3_role_collision_pairs_stay_separable() -> None:
    from ralph.display._color_depth import quantise_hex

    matrices = (_DEUTERANOPIA_MATRIX, _PROTANOPIA_MATRIX, _TRITANOPIA_MATRIX)
    pairs = (
        ("running", "info"),
        ("analysis", "pending"),
        ("elision", "pending"),
        ("analysis", "elision"),
    )
    for surface_hex in _C3_CANONICAL_SURFACES:
        palette = resolve_palette(surface_hex)
        for r1, r2 in pairs:
            h1, h2 = palette[r1], palette[r2]
            assert h1 != h2, f"{r1} vs {r2} collide on {surface_hex}"
            de = _oklab_delta_e(h1, h2)
            assert de >= _C3_MIN_DELTA_E, (
                f"{r1} vs {r2} on {surface_hex}: ΔE {de:.4f} < {_C3_MIN_DELTA_E}"
            )
            q1, q2 = quantise_hex(h1, "256"), quantise_hex(h2, "256")
            assert q1 != q2, f"{r1} vs {r2} on {surface_hex}: quantised collision {q1}"
            for matrix in matrices:
                sim1 = simulate_cvd(h1, matrix)
                sim2 = simulate_cvd(h2, matrix)
                assert sim1 != sim2, f"{r1} vs {r2} on {surface_hex}: CVD collision under {matrix}"


@pytest.mark.criteria("A-2")
def test_palette_monokai_fidelity_on_reference_surface() -> None:
    palette = resolve_palette("#2D2A2E")
    for role, expected_hex in _MONOKAI_ACCENT_HEX.items():
        assert palette[role] == expected_hex, f"{role}: {palette[role]} != {expected_hex}"


@pytest.mark.criteria("A-3")
def test_palette_lightness_structure_preserves_monokai_spacing() -> None:
    for surface_hex in ("#2D2A2E", "#FAF8F5"):
        palette = resolve_palette(surface_hex)
        surface_l = _oklab_l(surface_hex)
        distances = [abs(_oklab_l(palette[role]) - surface_l) for role in _ACCENT_OFFSET_ORDER]
        for prev, nxt in pairwise(distances):
            assert nxt >= prev - 1e-6, (surface_hex, _ACCENT_OFFSET_ORDER, distances)
        lightnesses = [_oklab_l(palette[role]) for role in _ACCENT_OFFSET_ORDER]
        spread = max(lightnesses) - min(lightnesses)
        assert spread > 0.05, f"{surface_hex}: spread {spread:.4f} too small"


@pytest.mark.criteria("A-6")
def test_palette_light_surface_chroma_stays_above_floor_fraction_of_anchor() -> None:
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
    for surface_hex in ("#2D2A2E", "#FAF8F5", None):
        palette = resolve_palette(surface_hex)
        for role in ("foreground", "comment"):
            _, chroma, _ = _hex_to_oklch(palette[role])
            assert chroma < 0.02, f"{role} on {surface_hex}: chroma {chroma:.4f} too saturated"


def test_palette_five_structural_roles_stay_mutually_distinct() -> None:
    for surface_hex in ("#2D2A2E", "#FAF8F5", None):
        palette = resolve_palette(surface_hex)
        disp = theme._build_display_styles(palette)
        values = [
            disp["chrome"],
            disp["agent_text"],
            disp["elision"],
            disp["diff_added"],
            disp["diff_removed"],
        ]
        assert len(set(values)) == len(values), (surface_hex, disp)


_NARROW_BAND_MIN_DELTA_E: float = 0.0025


@pytest.mark.criteria("B-4")
def test_palette_semantic_roles_stay_distinct_on_narrow_band_surfaces() -> None:
    roles = (
        "success",
        "error",
        "warning",
        "skipped",
        "info",
        "pending",
        "foreground",
        "muted",
        "comment",
    )
    for surface_hex in ("#484848", "#5F5F5F", "#6C6C6C", "#747474", "#808080", "#8C8C8C"):
        palette = resolve_palette(surface_hex)
        values = [palette[role] for role in roles]
        assert len(set(values)) == len(values), (surface_hex, dict(zip(roles, values, strict=True)))
        for r1, r2 in combinations(roles, 2):
            de = _oklab_delta_e(palette[r1], palette[r2])
            assert de >= _NARROW_BAND_MIN_DELTA_E, (
                f"{r1} vs {r2} on {surface_hex}: ΔE {de:.5f} < {_NARROW_BAND_MIN_DELTA_E}"
            )


@pytest.mark.criteria("E-4")
def test_palette_lightness_hierarchy_ranks_field_over_structure_over_recessive() -> None:
    for surface_hex in ("#2D2A2E", "#FAF8F5", "#1E1E1E", "#000000", "#FFFFFF"):
        palette = resolve_palette(surface_hex)
        primary_contrast = contrast_ratio(palette["foreground"], surface_hex)
        structure_contrast = contrast_ratio(palette["chrome"], surface_hex)
        recessive_contrasts = [
            contrast_ratio(palette["muted"], surface_hex),
            contrast_ratio(palette["comment"], surface_hex),
        ]
        assert primary_contrast > structure_contrast, (
            f"{surface_hex}: primary {primary_contrast:.3f} <= structure {structure_contrast:.3f}"
        )
        for recessive_contrast in recessive_contrasts:
            assert structure_contrast > recessive_contrast, (
                f"{surface_hex}: structure {structure_contrast:.3f} <= recessive {recessive_contrast:.3f}"
            )


_MAX_OWNED_FILL_DELTA_L: float = 0.06


@pytest.mark.parametrize(
    ("r", "g", "b"),
    [(index * 73 % 256, index * 151 % 256, index * 211 % 256) for index in range(30)],
)
@pytest.mark.criteria("E-5")
def test_owned_fills_stay_a_bounded_delta_l_from_the_surface(r: int, g: int, b: int) -> None:
    surface_hex = f"#{r:02X}{g:02X}{b:02X}"
    surface_l = _oklab_l(surface_hex)

    preview_hex = derive_preview_background(surface_hex)
    preview_l = _oklab_l(preview_hex)
    assert abs(preview_l - surface_l) <= _MAX_OWNED_FILL_DELTA_L, (
        f"{surface_hex}: preview fill ΔL {abs(preview_l - surface_l):.4f} exceeds bound"
    )

    removed_hex, added_hex = theme._derive_diff_fills(surface_hex)
    for label, diff_hex in (("removed", removed_hex), ("added", added_hex)):
        diff_l = _oklab_l(diff_hex)
        assert abs(diff_l - surface_l) <= _MAX_OWNED_FILL_DELTA_L, (
            f"{surface_hex}: diff {label} fill ΔL {abs(diff_l - surface_l):.4f} exceeds bound"
        )


@pytest.mark.criteria("E-6")
def test_diff_fills_are_hue_tinted_not_lightness_shifted() -> None:
    for surface_hex in _ALL_PAIRS_CANONICAL_SURFACES:
        preview_hex = derive_preview_background(surface_hex)
        preview_l = _oklab_l(preview_hex)
        removed_hex, added_hex = theme._derive_diff_fills(surface_hex)
        for label, diff_hex in (("removed", removed_hex), ("added", added_hex)):
            diff_l = _oklab_l(diff_hex)
            assert abs(diff_l - preview_l) < 1e-3, (
                f"{surface_hex}: diff {label} fill L {diff_l:.6f} != preview fill L {preview_l:.6f}"
            )


@pytest.mark.criteria("E-8")
def test_dual_safe_band_preserves_reference_offset_ordering() -> None:
    palette = resolve_palette(None)
    y_values = [relative_luminance(*hex_to_rgb(palette[role])) for role in _ACCENT_OFFSET_ORDER]
    for prev, nxt in pairwise(y_values):
        assert nxt >= prev - 1e-3, (_ACCENT_OFFSET_ORDER, y_values)
    spread = max(y_values) - min(y_values)
    assert spread > 0.002, f"dual-safe band: spread {spread:.5f} too small -- roles packed together"


def test_palette_identity_palette_stays_separable_after_quantisation() -> None:
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
    assert theme.preview_background_for_background(False) == derive_preview_background(
        theme._CANONICAL_DARK_SURFACE_HEX
    )
    assert theme.preview_background_for_background(True) == derive_preview_background(
        theme._CANONICAL_LIGHT_SURFACE_HEX
    )


def test_preview_foreground_boolean_path_matches_measured_canonical_surface() -> None:
    assert theme.preview_foreground_for_background(
        False
    ) == theme.preview_foreground_for_background(
        False, surface_hex=theme._CANONICAL_DARK_SURFACE_HEX
    )
    assert theme.preview_foreground_for_background(True) == theme.preview_foreground_for_background(
        True, surface_hex=theme._CANONICAL_LIGHT_SURFACE_HEX
    )


def test_diff_fill_boolean_path_matches_measured_canonical_surface() -> None:
    assert theme.diff_fill_styles(False) == theme.diff_fill_styles(
        False, surface_hex=theme._CANONICAL_DARK_SURFACE_HEX
    )
    assert theme.diff_fill_styles(True) == theme.diff_fill_styles(
        True, surface_hex=theme._CANONICAL_LIGHT_SURFACE_HEX
    )


def test_syntax_theme_for_surface_clears_contrast_on_its_owned_preview_fill() -> None:
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
