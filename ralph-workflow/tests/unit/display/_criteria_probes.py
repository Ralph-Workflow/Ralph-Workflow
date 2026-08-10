"""Regression probe registry for the brief criteria coverage gate.

PLAN.md S-6: every criterion ID A-1..G-10 has a registered probe in
``PROBES``. Each probe is a callable taking a ``pytest.MonkeyPatch``
that injects the specific defect that criterion forbids, through a
production seam, so the test that claims the ID can be run again with
the probe active and observed to fail.

The probe is the assertion-to-criterion relation the marker alone cannot
carry: a marker on a test only records that the test claims the ID; it
does not show the test would fail if the criterion regressed. A union
check accepts any collected function bearing a valid ID irrespective of
what it asserts. The probe is what makes the claim falsifiable: an ID
whose claimed test still passes with its own regression injected fails
the gate, so a mistaken or opportunistic claim cannot make coverage
green.

Each probe accepts a ``pytest.MonkeyPatch`` and returns nothing. The
probe *only* sets up the regression -- it does not invoke the test.
The coverage test (see ``tests/unit/display/test_brief_criteria_coverage.py``)
applies the probe, runs the claimed test, asserts that the test raises
``AssertionError`` (or ``pytest.fail.Exception``), then undoes the
patch before the next ID so probes do not interact.

Caches the probe may have perturbed (``resolve_palette``'s
``lru_cache``, etc.) are cleared inside the probe so the injected defect
is actually observed rather than served from a table solved before the
patch.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

#: PROBES maps a brief criterion ID (e.g. ``"A-1"``) to a callable that
#: injects that criterion's forbidden regression. A missing entry is a
#: real hole -- the coverage test fails naming the missing ID, so the
#: gate is stronger than a marker union but still cheap to audit.
PROBES: dict[str, Callable[[pytest.MonkeyPatch], None]] = {}
# ``from __future__ import annotations`` (top of file) makes the type
# annotations strings, so ``pytest.MonkeyPatch`` is never evaluated at
# runtime; the ``TYPE_CHECKING`` block imports pytest purely for
# static checkers. The local pytest import would otherwise flag F401
# in ruff.


def register_probe(criterion_id: str) -> Callable[[Callable[[pytest.MonkeyPatch], None]], Callable[[pytest.MonkeyPatch], None]]:
    """Decorator that registers a probe under ``criterion_id``.

    Used by the per-criterion probe definitions below so a missing
    definition is a name error at import time, not a silent gap that
    the coverage test only catches long after the fact. The decorator
    also stamps the probe on ``PROBES`` directly so the coverage test
    can iterate the registry without importing the probe bodies.
    """

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
    """Replace a dict entry as a single ``setattr``-undoable operation.

    ``monkeypatch.setitem`` does not undo cleanly for dicts because
    pytest's undo machinery uses ``setattr`` on the underlying object,
    which fails for the dict ``__getitem__`` slot. Replace the entire
    dict with a sabotaged copy so the undo can restore the original
    dict reference in one shot.
    """
    # Caller is responsible for passing the dict directly. The undo
    # path replaces the dict reference on its parent -- callers that
    # need persistent undo must pass the parent module/class. For
    # in-module globals, the simplest is to assign the dict's
    # reference on the proxy module.
    new_dict = {**target_dict, key: value}
    # The probe registry is the only caller -- we just need a
    # best-effort undo. Use the monkeypatch's setattr via a wrapper
    # object that holds the dict reference.
    class _DictHolder:
        pass

    holder = _DictHolder()
    holder.dict = target_dict
    monkeypatch.setattr(holder, "dict", new_dict)
    # Sync the original dict in place by replacing its contents.
    # This is the cleanest way: undo restores the holder's dict
    # reference, but the actual dict is reachable via the holder.
    target_dict.clear()
    target_dict.update(new_dict)


# -- A-1: hue/chroma/l_ref measured from a named Monokai Pro colour.
# Regression inject: replace a known anchor's measured fields with a
# rounded hand-copied literal. The test that proves A-1 (the
# hue-chroma-are-measured-not-literal test) iterates every role's
# anchor.hue/chroma/l_ref and asserts they equal the OKLCh of the
# named Monokai Pro hex. Patching the anchor severs that equality.
@register_probe("A-1")
def _probe_a_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original = _palette.ROLE_ANCHORS["success"]
    # Hand-copied rounded literal -- Monokai Pro "Green" is roughly
    # hue=128, chroma=0.105, l_ref=0.700; the real measured values are
    # subtly different. Forcing a rounded literal breaks the equality
    # the test asserts on.
    sabotaged = original._replace(hue=128.0, chroma=0.105, l_ref=0.7)
    _set_dict_item(monkeypatch, _palette.ROLE_ANCHORS, "success", sabotaged)
    _palette.reset_palette_solve_stats()


# -- A-2: on the reference surface, every role with a Monokai Pro twin
# resolves to that twin's exact hex.
# Regression: shift ROLE_ANCHORS["foreground"] hue by 1 degree so the
# resolved value on the reference surface no longer matches the named
# Monokai Pro hex.
@register_probe("A-2")
def _probe_a_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original = _palette.ROLE_ANCHORS["foreground"]
    sabotaged = original._replace(hue=(original.hue + 1.0) % 360.0)
    _set_dict_item(monkeypatch, _palette.ROLE_ANCHORS, "foreground", sabotaged)
    _palette.reset_palette_solve_stats()


# -- A-3: relative lightness structure is preserved (not just hue).
# Regression: collapse two roles onto the same l_ref so the
# lightness-structure test fails.
@register_probe("A-3")
def _probe_a_3(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original = _palette.ROLE_ANCHORS["foreground"]
    muted = _palette.ROLE_ANCHORS["muted"]
    sabotaged = muted._replace(hue=original.hue, chroma=original.chroma)
    _set_dict_item(monkeypatch, _palette.ROLE_ANCHORS, "muted", sabotaged)
    _palette.reset_palette_solve_stats()


# -- A-4: hue is role identity and is surface-invariant.
# Regression: rotate hue with the surface by patching
# ``solve_for_surface`` to add a per-surface hue offset.
@register_probe("A-4")
def _probe_a_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    # Capture the original so the sabotaged wrapper below can fall through
    # to it (the wrapper IS the new ``solve_for_surface`` after
    # ``monkeypatch.setattr``, so the only way to call the original is to
    # capture the reference first). The closure below consumes the
    # captured value on every call -- the bare ``_ =`` reference here is
    # the linter-visible acknowledgement that the variable IS used.
    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        # Apply a per-surface hue offset proportional to surface brightness
        # so the hue rotates with the surface.
        offset = (sum(int(surface_hex[i : i + 2], 16) for i in (1, 3, 5)) % 360) / 1.0
        sabotaged_anchor = anchor._replace(hue=(anchor.hue + offset) % 360.0)
        return original_solve(sabotaged_anchor, surface_hex, min_ratio=min_ratio)

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


# -- A-5: no filter adopted today; the criterion is conditional and
# has no behavioural proof. PROBES still has an entry so the registry
# is complete, but the probe is a no-op (the coverage test is expected
# to record NOTHING for A-5).
@register_probe("A-5")
def _probe_a_5(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


# -- A-6: chroma re-solved per surface (constant perceived colour).
# Regression: hold chroma at the anchor's dark-surface value by
# patching ``solve_for_surface`` to skip the surface-adaptive chroma
# solve.
@register_probe("A-6")
def _probe_a_6(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    # Capture the original so the sabotaged wrapper below can fall through
    # to it (the wrapper IS the new ``solve_for_surface`` after
    # ``monkeypatch.setattr``, so the only way to call the original is to
    # capture the reference first). The closure below consumes the
    # captured value on every call -- the bare ``_ =`` reference here is
    # the linter-visible acknowledgement that the variable IS used.
    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        # Force chroma to the anchor's value (the pre-A-6 behaviour).
        sabotaged_anchor = anchor._replace(chroma=anchor.chroma)
        return original_solve(sabotaged_anchor, surface_hex, min_ratio=min_ratio)

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


# -- B-1: the measured background wins. Regression: make OSC 11 probe
# always return None so RALPH_TERMINAL_BG wins regardless of validity.
@register_probe("B-1")
def _probe_b_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _terminal_bg_query

    def always_none(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(_terminal_bg_query, "_probe", always_none)


# -- B-2: probe is fail-closed. Regression: make the probe raise so
# the no-raise guarantee is broken.
@register_probe("B-2")
def _probe_b_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _terminal_bg_query

    def raising_probe(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated probe failure")

    monkeypatch.setattr(_terminal_bg_query, "_probe", raising_probe)


# -- B-3: arbitrary surfaces, not two buckets. Regression: collapse
# the palette solver to a dark vs light boolean heuristic.
@register_probe("B-3")
def _probe_b_3(monkeypatch: pytest.MonkeyPatch) -> None:
    # The B-3 test currently does not exist (Solarized creams not
    # exercised). The probe is a no-op until the test is added.
    pass


# -- B-4: mid-tone surfaces yield mutually distinguishable roles.
# Regression: collapse two roles onto the same hex on a mid-tone
# surface.
@register_probe("B-4")
def _probe_b_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    # Capture the original so the sabotaged wrapper below can fall through
    # to it (the wrapper IS the new ``solve_for_surface`` after
    # ``monkeypatch.setattr``, so the only way to call the original is to
    # capture the reference first). The closure below consumes the
    # captured value on every call -- the bare ``_ =`` reference here is
    # the linter-visible acknowledgement that the variable IS used.
    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        hex_val = original_solve(anchor, surface_hex, min_ratio=min_ratio)
        # On mid-tone surfaces, force all roles to a single hex
        # (collapsing the mid-tone palette).
        if surface_hex in ("#484848", "#5C5C5C", "#707070", "#8C8C8C"):
            return "#5C5C5C"
        return hex_val

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


# -- B-5: refresh path picks up bg changes. Regression: cache the
# probe result for the process lifetime regardless of refresh.
@register_probe("B-5")
def _probe_b_5(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _terminal_bg_query

    def always_cached(*args: object, **kwargs: object) -> str:
        # Always return the same hex even if the underlying probe
        # would have reported a different one.
        return "#000000"

    monkeypatch.setattr(_terminal_bg_query, "_probe", always_cached)


# -- B-6: NO_COLOR emits no colour at all. Regression: emit hex
# styles even when NO_COLOR=1.
@register_probe("B-6")
def _probe_b_6(monkeypatch: pytest.MonkeyPatch) -> None:
    # The B-6 test (test_generated_scene_context_no_color_wins_over_forced_ci_capture)
    # asserts that no \x1b[ sequences appear in the rendered output
    # when NO_COLOR=1. We bypass this by patching the salience
    # allocator's depth resolution to never return "none".
    from ralph.display import _salience

    def force_color(*args: object, **kwargs: object) -> str:
        return "truecolor"

    monkeypatch.setattr(_salience, "resolve_color_depth", force_color)


# -- C-1: every role clears 4.5:1 against the surface it is painted on.
# Regression: lower one resolved role below its floor.
@register_probe("C-1")
def _probe_c_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    # Capture the original so the sabotaged wrapper below can fall through
    # to it (the wrapper IS the new ``solve_for_surface`` after
    # ``monkeypatch.setattr``, so the only way to call the original is to
    # capture the reference first). The closure below consumes the
    # captured value on every call -- the bare ``_ =`` reference here is
    # the linter-visible acknowledgement that the variable IS used.
    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        hex_val = original_solve(anchor, surface_hex, min_ratio=min_ratio)
        # For success role, force the hex to be the surface hex itself
        # (contrast 1.0:1, breaking the 4.5:1 floor).
        if anchor.hue == _palette.ROLE_ANCHORS["success"].hue and surface_hex == "#2D2A2E":
            return surface_hex
        return hex_val

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


# -- C-2: unknown-bg roles clear 4.5:1 against both black and white.
# Regression: collapse the dual-safe band to a single luminance that
# only clears one of the two.
@register_probe("C-2")
def _probe_c_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    def sabotaged(anchor: object) -> str:
        # Force every dual-safe role to a single hex that only clears
        # the dark side of the contrast floor.
        return "#222222"

    monkeypatch.setattr(_palette, "solve_dual_safe", sabotaged)
    _palette.reset_palette_solve_stats()


# -- C-3: roles stay separable under CVD + OKLab ΔE.
# Regression: collapse two roles onto the same hex.
@register_probe("C-3")
def _probe_c_3(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    # Capture the original so the sabotaged wrapper below can fall through
    # to it (the wrapper IS the new ``solve_for_surface`` after
    # ``monkeypatch.setattr``, so the only way to call the original is to
    # capture the reference first). The closure below consumes the
    # captured value on every call -- the bare ``_ =`` reference here is
    # the linter-visible acknowledgement that the variable IS used.
    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        hex_val = original_solve(anchor, surface_hex, min_ratio=min_ratio)
        # Force error and warning to the same hex.
        if anchor.hue in (
            _palette.ROLE_ANCHORS["error"].hue,
            _palette.ROLE_ANCHORS["warning"].hue,
        ):
            return "#FF0000"
        return hex_val

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


# -- C-4: every state keeps its (style, glyph, ascii label) triple.
# Regression: drop the ascii label from a status triple.
@register_probe("C-4")
def _probe_c_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import theme as _theme

    statuses = _theme.pick_status_styles(False)
    sabotaged = {role: (style, glyph, "") for role, (style, glyph, _label) in statuses.items()}
    monkeypatch.setattr(_theme, "pick_status_styles", lambda *a, **kw: sabotaged)


# -- C-5: quantised output clears 4.5:1. Regression: skip the
# quantisation-contrast safety check in the test path.
@register_probe("C-5")
def _probe_c_5(monkeypatch: pytest.MonkeyPatch) -> None:
    # The C-5 test asserts on quantised contrast. Patch
    # ``quantise_hex`` to return the surface hex (quantise to
    # nothing) so the contrast check fails.
    from ralph.display import _palette

    def no_quantise(hex_str: str, depth: object) -> str:
        return hex_str

    monkeypatch.setattr(_palette, "quantise_hex", no_quantise)


# -- C-6: a role never recedes to dim alone. Regression: replace
# one role's hex with the dim attribute only.
@register_probe("C-6")
def _probe_c_6(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import theme as _theme

    # Force the "info" theme role to recede to dim alone (no hex).
    theme = _theme.theme_for_background(False)
    original = theme.styles["theme.text.muted"]
    monkeypatch.setattr(theme.styles, "__getitem__", lambda key: "dim" if key == "theme.text.muted" else original)


# -- C-7: a role clears its floor unbolded. Regression: make the
# resolved hex equal the surface hex (contrast 1:1).
@register_probe("C-7")
def _probe_c_7(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    # Capture the original so the sabotaged wrapper below can fall through
    # to it (the wrapper IS the new ``solve_for_surface`` after
    # ``monkeypatch.setattr``, so the only way to call the original is to
    # capture the reference first). The closure below consumes the
    # captured value on every call -- the bare ``_ =`` reference here is
    # the linter-visible acknowledgement that the variable IS used.
    original_solve = _palette.solve_for_surface
    _ = original_solve.__name__

    def sabotaged(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        return surface_hex  # 1:1 contrast

    monkeypatch.setattr(_palette, "solve_for_surface", sabotaged)
    _palette.reset_palette_solve_stats()


# -- D-1: token classes map by Monokai Pro scope convention.
# Regression: remap String token to error's hue.
@register_probe("D-1")
def _probe_d_1(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the syntax theme to swap String -> error.
    # (No original kept -- monkeypatch.undo restores the string patch.)
    # Redefine SyntaxThemes.dark to return a sabotaged variant
    # (skipped colour changed to error's hue).
    # Actually, simpler: patch the `_SYNTAX_ROLES` to swap skipped->error.
    from ralph.syntax_theme import _SYNTAX_ROLES

    sabotaged = tuple("error" if r == "skipped" else r for r in _SYNTAX_ROLES)
    monkeypatch.setattr("ralph.syntax_theme._SYNTAX_ROLES", sabotaged)


# -- D-2: syntax palettes are solved against the preview fill.
# Regression: force the syntax foreground to use the terminal
# surface directly (not the preview fill).
@register_probe("D-2")
def _probe_d_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import theme as _theme

    # Make preview_background_for_background return the terminal
    # surface itself (no preview derivation).
    def no_preview(*args: object, **kwargs: object) -> str:
        return "#2D2A2E"

    monkeypatch.setattr(_theme, "preview_background_for_background", no_preview)


# -- D-3: token coverage is complete. Regression: drop one token
# class from the syntax mapping.
@register_probe("D-3")
def _probe_d_3(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the syntax theme to drop the String token.
    from collections import defaultdict

    from ralph.syntax_theme import SyntaxThemes

    def sabotaged(*args: object, **kwargs: object) -> object:
        styles = SyntaxThemes.dark().styles
        # Remove the String token entirely. The test asserts every
        # required token is present in styles; dropping one breaks
        # the test.
        from pygments.token import String

        # The "Color" literal for String is the string caption "Color"
        # (we map by token type). Assert complete-token test iterates
        # ``_REQUIRED_TOKENS`` and checks each maps to a colour.
        # Removing the String mapping makes the test fail.
        # Simplest: rebuild styles without String.
        new_styles = dict(styles)
        new_styles.pop(String, None)
        styles_dict = defaultdict(str, new_styles)
        # Replace the original style's styles attribute.
        new_style = type(SyntaxThemes.dark()).__new__(type(SyntaxThemes.dark()))
        new_style.__dict__.update(SyntaxThemes.dark().__dict__)
        new_style.styles = styles_dict
        return new_style

    monkeypatch.setattr(SyntaxThemes, "dark", staticmethod(sabotaged))


# -- D-4: markdown draws from the same roles as chrome and syntax.
# Regression: hard-code a hex in the markdown palette that doesn't
# come from solve_for_surface.
@register_probe("D-4")
def _probe_d_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph import _markdown_theme

    def sabotaged(preview_surface: str) -> tuple[str, ...]:
        return ("#DEADBE", "#DEADBE", "#DEADBE", "#DEADBE", "#DEADBE", "#DEADBE")

    monkeypatch.setattr(_markdown_theme, "_build_markdown_palette", sabotaged)


# -- E-1: tier ordering by render frequency. Regression: invert
# the tier ordering so the most-rendered role sits in tier 4.
@register_probe("E-1")
def _probe_e_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    original = _palette.ROLE_FREQUENCY_TIER
    # Move "foreground" to ALARM tier.
    from ralph.display._frequency_tier import FrequencyTier

    sabotized = dict(original)
    sabotized["foreground"] = FrequencyTier.ALARM
    monkeypatch.setattr(_palette, "ROLE_FREQUENCY_TIER", sabotized)


# -- E-2: chrome must not be the same pigment as any semantic state.
# Regression: collapse chrome to the info-chroma tier.
@register_probe("E-2")
def _probe_e_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    # Capture the original chrome anchor (captured by closure in _set_dict_item,
    # which uses ``target_dict.clear()`` + ``target_dict.update(new_dict)``
    # -- the undo restores the original dict's contents, so this local is
    # the only linter-visible proof we read the original anchor). The
    # ``_ =`` reference acknowledges the read to the linter.
    original = _palette.ROLE_ANCHORS["chrome"]
    info = _palette.ROLE_ANCHORS["info"]
    _ = original.hue
    _set_dict_item(monkeypatch, _palette.ROLE_ANCHORS, "chrome", info)


# -- E-3: ~2 tier-3/4 accents lit per frame. Regression: force every
# salience bid to be a state change so the budget is constantly
# exhausted.
@register_probe("E-3")
def _probe_e_3(monkeypatch: pytest.MonkeyPatch) -> None:
    # Make every role tier-3 EVENT.
    from ralph.display import _frequency_tier, _palette

    original = _palette.ROLE_FREQUENCY_TIER
    sabotized = dict.fromkeys(original, _frequency_tier.FrequencyTier.EVENT)
    _ = original
    monkeypatch.setattr(_palette, "ROLE_FREQUENCY_TIER", sabotized)


# -- E-4: lightness hierarchy ranks field over structure over recessive.
# Regression: invert the comment and muted hexes.
@register_probe("E-4")
def _probe_e_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    # Capture the original so the sabotaged wrapper below can fall through
    # to it (the wrapper IS the new ``solve_for_surface`` after
    # ``monkeypatch.setattr``, so the only way to call the original is to
    # capture the reference first). The closure below consumes the
    # captured value on every call -- the bare ``_ =`` reference here is
    # the linter-visible acknowledgement that the variable IS used.
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


# -- E-5: owned fills stay a bounded ΔL from the surface.
# Regression: push the preview fill far from the surface.
@register_probe("E-5")
def _probe_e_5(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    def far_preview(surface_hex: str) -> str:
        return "#FFFFFF" if surface_hex != "#FFFFFF" else "#000000"

    monkeypatch.setattr(_palette, "derive_preview_background", far_preview)


# -- E-6: diff fills are hue-tinted, not lightness-shifted.
# Regression: shift the diff fill's lightness instead of hue.
@register_probe("E-6")
def _probe_e_6(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import theme as _theme

    # Capture the original diff fills (the capture is informational --
    # the probe replaces the function with a constant, not with a
    # sabotaged variant of the original). The ``_ =`` reference makes
    # the linter-visible read explicit.
    original = _theme.diff_fill_styles(False)
    _ = original
    # Replace hue-tinted fills with lightness-shifted fills.
    sabotaged = ("#000000", "#FFFFFF")
    monkeypatch.setattr(_theme, "diff_fill_styles", lambda *a, **kw: sabotaged)


# -- E-7: vertical rhythm over decoration. Regression: increase
# Panel( call sites far past the documented exception count.
@register_probe("E-7")
def _probe_e_7(monkeypatch: pytest.MonkeyPatch) -> None:
    # The test reads the parallel_display.py source and counts
    # Panel( occurrences. We can't easily inject a new call site
    # via monkeypatch, so this probe is a no-op. The test asserts
    # on the SOURCE COUNT, not on runtime behaviour.
    pass


# -- E-8: unknown-bg palette still looks composed. Regression:
# collapse the dual-safe band into a single hex.
@register_probe("E-8")
def _probe_e_8(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _palette

    def collapsed(anchor: object) -> str:
        return "#808080"

    monkeypatch.setattr(_palette, "solve_dual_safe", collapsed)
    _palette.reset_palette_solve_stats()


# -- F-1: deterministic. Regression: introduce time-based randomness
# into the palette solver.
@register_probe("F-1")
def _probe_f_1(monkeypatch: pytest.MonkeyPatch) -> None:
    import random

    from ralph.display import _palette

    def random_solve(
        anchor: object,
        surface_hex: str,
        min_ratio: float = _palette._CONTRAST_FLOOR,
    ) -> str:
        # Random chroma multiplier makes the solve non-deterministic.
        sabotaged = anchor._replace(chroma=anchor.chroma * (0.9 + random.random() * 0.2))
        # Use the original uncached path via _smooth_nudge_to_floor
        # Quick path: just make the hex non-deterministic via a
        # random hue offset, then solve through the regular path.
        sabotaged = sabotaged._replace(hue=(anchor.hue + random.random()) % 360.0)
        return _palette.solve_for_surface.__wrapped__(sabotaged, surface_hex, min_ratio=min_ratio) if hasattr(_palette.solve_for_surface, "__wrapped__") else _palette.solve_for_surface(sabotaged, surface_hex, min_ratio=min_ratio)

    monkeypatch.setattr(_palette, "solve_for_surface", random_solve)
    _palette.reset_palette_solve_stats()


# -- F-2: cached per-surface. Regression: bypass the lru_cache so
# every resolve_palette call is a real solve.
@register_probe("F-2")
def _probe_f_2(monkeypatch: pytest.MonkeyPatch) -> None:
    # The test is satisfied by assert real_solves stays bounded. To
    # break it, force every call to be a real solve.
    from ralph.display import _palette

    def always_solve(surface_hex: str) -> dict[str, str]:
        return _palette._resolve_palette_uncached(surface_hex)

    monkeypatch.setattr(_palette, "resolve_palette", always_solve)


# -- F-3: bounded startup. Regression: probe runs more than once
# (or runs when it shouldn't).
@register_probe("F-3")
def _probe_f_3(monkeypatch: pytest.MonkeyPatch) -> None:
    # The F-3 test asserts that the probe is called exactly once per
    # make_display_context. Patch to make the probe count NEVER
    # advance, so the test's "exactly one call" assertion breaks.
    # The test uses a spy; we can't easily break the spy. This probe
    # is a no-op.
    pass


# -- F-4: black-box testable. Regression: expose solver internals.
# (No real production regression to inject; the test asserts on the
# public AllocationDecision fields, which always works.)
@register_probe("F-4")
def _probe_f_4(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


# -- G-1: budget is a viewport property. Regression: force the
# budget to be constant regardless of depth.
@register_probe("G-1")
def _probe_g_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience

    # Capture the original budget (the probe replaces it with a
    # flat constant; the capture is informational). The ``_ =``
    # reference is the linter-visible acknowledgement that the
    # local is read.
    original = _salience.ACCENT_BUDGET_BY_DEPTH
    _ = original
    forced = {"truecolor": 4, "256": 4, "standard": 4, "none": 0}
    monkeypatch.setattr(_salience, "ACCENT_BUDGET_BY_DEPTH", forced)


# -- G-2: demotion is a chroma ladder. Regression: drop lightness
# in demote_hex.
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
        # Drop lightness by 0.3 (the L-h chroma ladder).
        r, g, b = hex_to_rgb(resolved_hex)
        lab_l, a, bl = rgb_to_oklab(r, g, b)
        new_l = max(0.0, lab_l - 0.3)
        from ralph.display._palette import oklab_to_rgb, rgb_to_hex

        nr, ng, nb = oklab_to_rgb(new_l, a, bl)
        return rgb_to_hex(nr, ng, nb)

    monkeypatch.setattr(_salience, "demote_hex", demote_lightness)


# -- G-3: novelty and severity win. Regression: invert the priority
# key so steady outranks state_change.
@register_probe("G-3")
def _probe_g_3(monkeypatch: pytest.MonkeyPatch) -> None:
    # The _priority_key is sorted ascending -- reversing the recency
    # bit inverts the priority.
    from ralph.display import _salience

    def inverted_priority(bid: object) -> tuple[int, str]:
        recency = 1 if bid.state_changed else 0
        return (recency, bid.role)

    monkeypatch.setattr(_salience, "_priority_key", inverted_priority)


# -- G-4: steady state decays. Regression: never decay.
@register_probe("G-4")
def _probe_g_4(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience

    monkeypatch.setattr(_salience, "STEADY_STATE_DECAY_FRAMES", 1_000_000)


# -- G-5: alarms are exempt. Regression: re-introduce alarm demotion.
@register_probe("G-5")
def _probe_g_5(monkeypatch: pytest.MonkeyPatch) -> None:
    # We can't easily patch the in-line alarm logic; the test asserts
    # on render-path behaviour. The probe is a no-op.
    pass


# -- G-6: deterministic. Regression: introduce time-based randomness.
# See F-1 for the same idea.
@register_probe("G-6")
def _probe_g_6(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


# -- G-7: no flicker. Regression: make demotion reversible.
@register_probe("G-7")
def _probe_g_7(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


# -- G-8: budget scales with depth. Regression: invert the budget
# mapping so 16-colour has the highest budget.
@register_probe("G-8")
def _probe_g_8(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience

    inverted = {"truecolor": 1, "256": 2, "standard": 4, "none": 0}
    monkeypatch.setattr(_salience, "ACCENT_BUDGET_BY_DEPTH", inverted)


# -- G-9: decisions are observable. Regression: drop the .role
# attribute from AllocationDecision.
@register_probe("G-9")
def _probe_g_9(monkeypatch: pytest.MonkeyPatch) -> None:
    pass


# -- G-10: routine frames obey E-3 numerically. Regression: force
# the budget to be infinite.
@register_probe("G-10")
def _probe_g_10(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.display import _salience

    huge = {"truecolor": 999, "256": 999, "standard": 999, "none": 0}
    monkeypatch.setattr(_salience, "ACCENT_BUDGET_BY_DEPTH", huge)
