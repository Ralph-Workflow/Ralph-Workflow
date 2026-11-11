from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

PROBES: dict[str, Callable[[pytest.MonkeyPatch], None]] = {}


def register_probe(
    criterion_id: str,
) -> Callable[[Callable[[pytest.MonkeyPatch], None]], Callable[[pytest.MonkeyPatch], None]]:

    def decorator(fn: Callable[[pytest.MonkeyPatch], None]) -> Callable[[pytest.MonkeyPatch], None]:
        PROBES[criterion_id] = fn
        return fn

    return decorator


def _set_dict_item(
    monkeypatch: pytest.MonkeyPatch,
    target_dict: dict,
    key: str,
    value: object,
) -> None:
    new_dict = {**target_dict, key: value}

    class _DictHolder:
        pass

    holder = _DictHolder()
    holder.dict = target_dict
    monkeypatch.setattr(holder, "dict", new_dict)
    target_dict.clear()
    target_dict.update(new_dict)


@register_probe("A-1")
def _probe_a_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original = _palette.ROLE_ANCHORS["success"]
    sabotaged = original._replace(hue=128.0, chroma=0.105, l_ref=0.7)
    _set_dict_item(monkeypatch, _palette.ROLE_ANCHORS, "success", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("A-2")
def _probe_a_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original = _palette.ROLE_ANCHORS["foreground"]
    sabotaged = original._replace(hue=(original.hue + 1.0) % 360.0)
    _set_dict_item(monkeypatch, _palette.ROLE_ANCHORS, "foreground", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("A-3")
def _probe_a_3(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original = _palette.ROLE_ANCHORS["foreground"]
    muted = _palette.ROLE_ANCHORS["muted"]
    sabotaged = muted._replace(hue=original.hue, chroma=original.chroma)
    _set_dict_item(monkeypatch, _palette.ROLE_ANCHORS, "muted", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("A-4")
def _probe_a_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        offset = (sum(int(surface_hex[i : i + 2], 16) for i in (1, 3, 5)) % 360) / 1.0
        sabotaged_anchor = anchor._replace(hue=(anchor.hue + offset) % 360.0)
        return original_solve(sabotaged_anchor, surface_hex, min_ratio=min_ratio)

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("A-5")
def _probe_a_5(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


@register_probe("A-6")
def _probe_a_6(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        sabotaged_anchor = anchor._replace(chroma=anchor.chroma)
        return original_solve(sabotaged_anchor, surface_hex, min_ratio=min_ratio)

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("B-1")
def _probe_b_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _terminal_bg_query

    def always_none(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(_terminal_bg_query, "_probe", always_none)


@register_probe("B-2")
def _probe_b_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _terminal_bg_query

    def raising_probe(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated probe failure")

    monkeypatch.setattr(_terminal_bg_query, "_probe", raising_probe)


@register_probe("B-3")
def _probe_b_3(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


@register_probe("B-4")
def _probe_b_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        hex_val = original_solve(anchor, surface_hex, min_ratio=min_ratio)
        if surface_hex in ("#484848", "#5C5C5C", "#707070", "#8C8C8C"):
            return "#5C5C5C"
        return hex_val

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("B-5")
def _probe_b_5(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _terminal_bg_query

    def always_cached(*args: object, **kwargs: object) -> str:
        return "#000000"

    monkeypatch.setattr(_terminal_bg_query, "_probe", always_cached)


@register_probe("B-6")
def _probe_b_6(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience

    def force_color(*args: object, **kwargs: object) -> str:
        return "truecolor"

    monkeypatch.setattr(_salience, "resolve_color_depth", force_color)


@register_probe("C-1")
def _probe_c_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        hex_val = original_solve(anchor, surface_hex, min_ratio=min_ratio)
        if anchor.hue == _palette.ROLE_ANCHORS["success"].hue and surface_hex == "#2D2A2E":
            return surface_hex
        return hex_val

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("C-2")
def _probe_c_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    def sabotaged(anchor: object) -> str:
        return "#222222"

    monkeypatch.setattr(_palette, "solve_dual_safe", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("C-3")
def _probe_c_3(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        hex_val = original_solve(anchor, surface_hex, min_ratio=min_ratio)
        if anchor.hue in (
            _palette.ROLE_ANCHORS["error"].hue,
            _palette.ROLE_ANCHORS["warning"].hue,
        ):
            return "#FF0000"
        return hex_val

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("C-4")
def _probe_c_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import theme as _theme

    statuses = _theme.pick_status_styles(False)
    sabotaged = {role: (style, glyph, "") for role, (style, glyph, _label) in statuses.items()}
    monkeypatch.setattr(_theme, "pick_status_styles", lambda *a, **kw: sabotaged)


@register_probe("C-5")
def _probe_c_5(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    def no_quantise(hex_str: str, depth: object) -> str:
        return hex_str

    monkeypatch.setattr(_palette, "quantise_hex", no_quantise)


@register_probe("C-6")
def _probe_c_6(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import theme as _theme

    theme = _theme.theme_for_background(False)
    original = theme.styles["theme.text.muted"]
    monkeypatch.setattr(
        theme.styles, "__getitem__", lambda key: "dim" if key == "theme.text.muted" else original
    )


@register_probe("C-7")
def _probe_c_7(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        return surface_hex

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("D-1")
def _probe_d_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display.syntax_theme import _SYNTAX_ROLES

    sabotaged = tuple("error" if r == "skipped" else r for r in _SYNTAX_ROLES)
    monkeypatch.setattr("ralph.display.syntax_theme._SYNTAX_ROLES", sabotaged)


@register_probe("D-2")
def _probe_d_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import theme as _theme

    def no_preview(*args: object, **kwargs: object) -> str:
        return "#2D2A2E"

    monkeypatch.setattr(_theme, "preview_background_for_background", no_preview)


@register_probe("D-3")
def _probe_d_3(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections import defaultdict

    from ralph.display.syntax_theme import SyntaxThemes

    def sabotaged(*args: object, **kwargs: object) -> object:
        styles = SyntaxThemes.dark().styles
        from pygments.token import String

        new_styles = dict(styles)
        new_styles.pop(String, None)
        styles_dict = defaultdict(str, new_styles)
        new_style = type(SyntaxThemes.dark()).__new__(type(SyntaxThemes.dark()))
        new_style.__dict__.update(SyntaxThemes.dark().__dict__)
        new_style.styles = styles_dict
        return new_style

    monkeypatch.setattr(SyntaxThemes, "dark", staticmethod(sabotaged))


@register_probe("D-4")
def _probe_d_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _markdown_theme

    def sabotaged(preview_surface: str) -> tuple[str, ...]:
        return ("#DEADBE", "#DEADBE", "#DEADBE", "#DEADBE", "#DEADBE", "#DEADBE")

    monkeypatch.setattr(_markdown_theme, "_build_markdown_palette", sabotaged)


@register_probe("E-1")
def _probe_e_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original = _palette.ROLE_FREQUENCY_TIER
    from ralph.display._frequency_tier import FrequencyTier

    sabotized = dict(original)
    sabotized["foreground"] = FrequencyTier.ALARM
    monkeypatch.setattr(_palette, "ROLE_FREQUENCY_TIER", sabotized)


@register_probe("E-2")
def _probe_e_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original = _palette.ROLE_ANCHORS["chrome"]
    info = _palette.ROLE_ANCHORS["info"]
    _ = original.hue
    _set_dict_item(monkeypatch, _palette.ROLE_ANCHORS, "chrome", info)


@register_probe("E-3")
def _probe_e_3(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _frequency_tier, _palette

    original = _palette.ROLE_FREQUENCY_TIER
    sabotized = dict.fromkeys(original, _frequency_tier.FrequencyTier.EVENT)
    _ = original
    monkeypatch.setattr(_palette, "ROLE_FREQUENCY_TIER", sabotized)


@register_probe("E-4")
def _probe_e_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        hex_val = original_solve(anchor, surface_hex, min_ratio=min_ratio)
        if anchor.hue == _palette.ROLE_ANCHORS["comment"].hue:
            muted = _palette.ROLE_ANCHORS["muted"]
            return original_solve(muted, surface_hex, min_ratio=min_ratio)
        return hex_val

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


@register_probe("E-5")
def _probe_e_5(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    def far_preview(surface_hex: str) -> str:
        return "#FFFFFF" if surface_hex != "#FFFFFF" else "#000000"

    monkeypatch.setattr(_palette, "derive_preview_background", far_preview)


@register_probe("E-6")
def _probe_e_6(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import theme as _theme

    original = _theme.diff_fill_styles(False)
    _ = original
    sabotaged = ("#000000", "#FFFFFF")
    monkeypatch.setattr(_theme, "diff_fill_styles", lambda *a, **kw: sabotaged)


@register_probe("E-7")
def _probe_e_7(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


@register_probe("E-8")
def _probe_e_8(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    def collapsed(anchor: object) -> str:
        return "#808080"

    monkeypatch.setattr(_palette, "solve_dual_safe", collapsed)
    _palette.reset_palette_solve_stats()


@register_probe("F-1")
def _probe_f_1(monkeypatch: pytest.MonkeyPatch) -> None:
    import random

    from ralph.display import _palette

    def random_solve(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        sabotaged = anchor._replace(chroma=anchor.chroma * (0.9 + random.random() * 0.2))
        sabotaged = sabotaged._replace(hue=(anchor.hue + random.random()) % 360.0)
        return (
            _palette.solve_for_surface.__wrapped__(sabotaged, surface_hex, min_ratio=min_ratio)
            if hasattr(_palette.solve_for_surface, "__wrapped__")
            else _palette.solve_for_surface(sabotaged, surface_hex, min_ratio=min_ratio)
        )

    monkeypatch.setattr(_palette, "solve_for_surface", random_solve)
    _palette.reset_palette_solve_stats()


@register_probe("F-2")
def _probe_f_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    def always_solve(surface_hex: str) -> dict[str, str]:
        return _palette._resolve_palette_uncached(surface_hex)

    monkeypatch.setattr(_palette, "resolve_palette", always_solve)


@register_probe("F-3")
def _probe_f_3(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


@register_probe("F-4")
def _probe_f_4(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


@register_probe("G-1")
def _probe_g_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience

    original = _salience.ACCENT_BUDGET_BY_DEPTH
    _ = original
    forced = {"truecolor": 4, "256": 4, "standard": 4, "none": 0}
    monkeypatch.setattr(_salience, "ACCENT_BUDGET_BY_DEPTH", forced)


@register_probe("G-2")
def _probe_g_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience
    from ralph.display._palette import hex_to_rgb, rgb_to_oklab

    def demote_lightness(
        resolved_hex: str,
        *,
        budget: object,
        surface_hex: str | None = None,
    ) -> str:
        r, g, b = hex_to_rgb(resolved_hex)
        lab_l, a, bl = rgb_to_oklab(r, g, b)
        new_l = max(0.0, lab_l - 0.3)
        from ralph.display._palette import oklab_to_rgb, rgb_to_hex

        nr, ng, nb = oklab_to_rgb(new_l, a, bl)
        return rgb_to_hex(nr, ng, nb)

    monkeypatch.setattr(_salience, "demote_hex", demote_lightness)


@register_probe("G-3")
def _probe_g_3(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience

    def inverted_priority(bid: object) -> tuple[int, str]:
        recency = 1 if bid.state_changed else 0
        return (recency, bid.role)

    monkeypatch.setattr(_salience, "_priority_key", inverted_priority)


@register_probe("G-4")
def _probe_g_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience

    monkeypatch.setattr(_salience, "STEADY_STATE_DECAY_FRAMES", 1_000_000)


@register_probe("G-5")
def _probe_g_5(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


@register_probe("G-6")
def _probe_g_6(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


@register_probe("G-7")
def _probe_g_7(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


@register_probe("G-8")
def _probe_g_8(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience

    inverted = {"truecolor": 1, "256": 2, "standard": 4, "none": 0}
    monkeypatch.setattr(_salience, "ACCENT_BUDGET_BY_DEPTH", inverted)


@register_probe("G-9")
def _probe_g_9(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


@register_probe("G-10")
def _probe_g_10(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience

    huge = {"truecolor": 999, "256": 999, "standard": 999, "none": 0}
    monkeypatch.setattr(_salience, "ACCENT_BUDGET_BY_DEPTH", huge)
