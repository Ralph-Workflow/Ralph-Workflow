"""Unit tests for deterministic Monokai Pro-derived palette generator."""

from __future__ import annotations

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


def test_derive_preview_background_matches_theme_module() -> None:
    """DA-002: theme.py and _palette.py must derive the identical preview fill
    -- previously they disagreed because each carried its own light/dark
    luminance crossover."""
    for surface_hex in _DA_002_SURFACES:
        assert theme.preview_background_for_background(
            None, surface_hex=surface_hex
        ) == derive_preview_background(surface_hex), surface_hex


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
