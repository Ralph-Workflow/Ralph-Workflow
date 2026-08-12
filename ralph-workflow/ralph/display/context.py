"""Single source of truth for Ralph CLI display dependencies.

No renderer may construct its own Console. All display code must receive
a DisplayContext (or build one via make_display_context) that owns the
console, theme, terminal width, color policy, mode, and adaptive limits.

After the wt-028-display consolidation, ``DisplayContext.mode`` is
always the string ``'default'``. There is no width-based dispatch, no
``compact`` / ``medium`` / ``wide`` tier, and no per-mode limits
table. The persistent bottom Status Bar is the single owner of
run-level layout, color, spacing, truncation, and live-update
behavior; width-driven degradation happens in the documented order
below so the bar always fits ``ctx.width`` and remains readable at
every applicable width:

1. Long paths middle-truncate to absorb excess length on long paths.
2. Long phase labels tail-truncate to absorb excess length on labels.
3. Iteration label form degrades canonical (``Cycle 1/3`` /
   ``iter 2/5``) -> compact (``C1/3`` / ``i2/5``) -> minimal
   (``1/3`` / ``2/5``) below the canonical-fit threshold. Custom
   outer labels (such as ``Remediation`` or ``Round``) override ``Cycle``.
4. The phase marker is dropped below the marker-fit threshold.
5. Per-iteration glyphs are dropped below the glyph-fit threshold.
6. Iteration segments drop one at a time (outer_dev first, then
   inner_analysis, then both) below the iteration-visibility
   threshold (14 cols). Below that threshold the bar degrades
   cleanly to whatever subset of phase + path fits.

See :mod:`ralph.display.status_bar` for the full implementation
contract and ``tests/display/test_status_bar.py`` for the regression
suite that locks these invariants end-to-end.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, TextIO, cast

from ralph.display._mode_adaptive_limits import _DEFAULT_LIMITS
from ralph.display._resolved_env import _ResolvedEnv
from ralph.display.mode import DEFAULT_MODE
from ralph.display.theme import (
    ASCII_GLYPHS,
    UNICODE_GLYPHS,
    detect_glyph_capability,
    detect_terminal_background_hex,
    detect_terminal_background_is_light,
    make_console,
    theme_for_background,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from rich.console import Console
    from rich.theme import Theme

_STREAMING_CHECKPOINT_FRAGMENTS: Final[int] = 20

_STREAMING_DEDUP_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})
_STREAMING_CHECKPOINTS_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})

_RALPH_FORCE_ASCII_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def _resolve_env(env: Mapping[str, str]) -> _ResolvedEnv:
    """Parse environment variables into resolved display settings.

    Args:
        env: Environment mapping to parse.

    Returns:
        _ResolvedEnv with all display-relevant env settings resolved.
    """
    # Presence is the conventional NO_COLOR signal; FORCE_COLOR follows the
    # same explicit-value convention so an empty inherited variable cannot
    # accidentally alter a caller's injected console. NO_COLOR remains the
    # final authority when both are present.
    no_color = bool(env.get("NO_COLOR", "").strip())
    # FORCE_COLOR follows the conventional numeric contract as well as the
    # usual truthy spellings: ``FORCE_COLOR=0`` is an explicit opt-out unless
    # NO_COLOR is present (which always wins).
    force_color_value = env.get("FORCE_COLOR", "").strip().lower()
    force_color = force_color_value not in {"", "0", "false", "no", "off"}

    force_ascii_val = env.get("RALPH_FORCE_ASCII", "").lower().strip()
    force_ascii = force_ascii_val in _RALPH_FORCE_ASCII_TRUTHY

    columns: int | None = None
    if "COLUMNS" in env:
        try:
            w = int(env["COLUMNS"])
            columns = w if w > 0 else None
        except (ValueError, TypeError):
            pass

    streaming_dedup_val = env.get("RALPH_STREAMING_DEDUP", "").lower().strip()
    streaming_dedup_enabled = streaming_dedup_val not in _STREAMING_DEDUP_DISABLED_VALUES

    streaming_checkpoints_val = env.get("RALPH_STREAMING_CHECKPOINTS", "").lower().strip()
    streaming_checkpoints_enabled = (
        streaming_checkpoints_val not in _STREAMING_CHECKPOINTS_DISABLED_VALUES
    )

    return _ResolvedEnv(
        no_color=no_color,
        force_color=force_color,
        columns=columns,
        force_ascii=force_ascii,
        streaming_dedup_enabled=streaming_dedup_enabled,
        streaming_checkpoints_enabled=streaming_checkpoints_enabled,
    )


def _console_has_no_color(console: Console, *, injected_console: bool) -> bool:
    """Return True when the console has color disabled via its no_color attribute."""
    raw: object = getattr(console, "no_color", False)
    if not bool(raw):
        return False
    if not injected_console:
        return True
    if _console_has_forced_color(console):
        return False
    raw_file: object = getattr(console, "_file", None)
    return raw_file is not None


def _console_has_forced_color(console: Console) -> bool:
    """Return True when an injected console explicitly requests terminal color."""
    force_terminal_raw: object = getattr(console, "_force_terminal", None)
    return force_terminal_raw is True


def _normalize_injected_console_color(console: Console, resolved_env: _ResolvedEnv) -> None:
    """Make injected Rich consoles honor Ralph's env-driven color contract."""
    if resolved_env.no_color:
        with contextlib.suppress(Exception):
            console.no_color = True
        return
    if _console_has_forced_color(console):
        with contextlib.suppress(Exception):
            console.no_color = False
        # Rich may retain a stale auto-detected colour system on an injected
        # console.  An explicitly forced terminal is Ralph's colour contract:
        # restore a concrete renderer so the public output seam cannot fall
        # back to monochrome after theme/style caching.
        with contextlib.suppress(Exception):
            if console._color_system is None:
                # Rich exposes ``_color_system`` as ``ColorSystem | None`` but
                # accepts ``"truecolor"`` as a valid runtime assignment via
                # the COLOR_SYSTEMS map; the property setter validates and
                # translates the string. The typed setter is intentionally
                # bypassed here because the underlying field is private and
                # re-stamped with a string the public API documents as valid
                # input. ``object.__setattr__`` keeps mypy and ruff B010
                # silent while still assigning the attribute directly.
                # Rich stores the resolved enum internally; assigning the
                # public enum value avoids a stale ``None`` while preserving
                # Rich's renderer contract across supported versions. Import
                # ``ColorSystem`` from its defining module ``rich.color``
                # because ``rich.console`` only re-exports it implicitly,
                # which mypy's ``implicit_reexport`` default rejects.
                from rich.color import ColorSystem

                object.__setattr__(console, "_color_system", ColorSystem.TRUECOLOR)


def _build_console(
    resolved_env: _ResolvedEnv,
    terminal_bg_is_light: bool | None,
    surface_hex: str | None = None,
    *,
    output_stream: TextIO | None = None,
) -> Console:
    """Create a console based on resolved NO_COLOR / FORCE_COLOR settings.

    Ralph keeps ANSI in durable redirected transcripts when the destination
    supports it.  ``FORCE_COLOR`` explicitly opts into that behaviour, while
    the ordinary path still uses Rich's terminal detection for real streams.
    ``NO_COLOR`` is checked first and always wins.

    Args:
        resolved_env: Pre-resolved environment settings.
        terminal_bg_is_light: Resolved terminal background.
        surface_hex: The measured terminal background surface, when known.
        output_stream: Destination stream for rendered output.

    Returns:
        Configured Console instance.
    """
    if resolved_env.no_color:
        return make_console(
            file=output_stream,
            no_color=True,
            force_terminal=False,
            terminal_bg_is_light=terminal_bg_is_light,
            surface_hex=surface_hex,
        )
    if resolved_env.force_color:
        return make_console(
            file=output_stream,
            no_color=False,
            force_terminal=True,
            terminal_bg_is_light=terminal_bg_is_light,
            surface_hex=surface_hex,
        )
    return make_console(
        file=output_stream,
        no_color=False,
        force_terminal=True,
        color_system="truecolor",
        terminal_bg_is_light=terminal_bg_is_light,
        surface_hex=surface_hex,
    )


def _compute_width(
    resolved_env: _ResolvedEnv,
    console: Console,
    force_width: int | None,
    *,
    prefer_configured_width: bool = True,
    injected_console: bool = False,
    explicit_env: bool = False,
) -> int:
    """Resolve effective terminal width from overrides, env, and console.

    Args:
        resolved_env: Pre-resolved environment settings.
        console: Console to read width from as fallback.
        force_width: Explicit width override.
        prefer_configured_width: Whether the console's configured
            (``_width``) attribute may short-circuit env resolution.
        injected_console: When ``True`` (the caller passed an explicit
            ``console=`` argument) AND no explicit ``env=`` was passed,
            the console's own width is AUTHORITATIVE: the
            caller-injected console is the source of truth.
        explicit_env: When ``True`` the caller passed ``env=...`` to
            :func:`make_display_context`. In that mode a ``COLUMNS``
            value in the explicit env wins over both Ralph-built and
            caller-injected consoles (because the operator who set
            the env meant to control the rendered width). When
            ``False`` (default; the caller did NOT pass ``env=``),
            host-shell ``COLUMNS`` inherited via ``os.environ`` is
            NOT authoritative -- the injected console's width is
            instead, so a 40-column test fixture is not silently
            widened to whatever the host terminal reported.

    Returns:
        Effective terminal width in characters.
    """
    if force_width is not None and force_width > 0:
        return force_width
    return _compute_width_uncached(
        resolved_env,
        console,
        prefer_configured_width=prefer_configured_width,
        injected_console=injected_console,
        explicit_env=explicit_env,
    )


def _compute_width_uncached(
    resolved_env: _ResolvedEnv,
    console: Console,
    *,
    prefer_configured_width: bool,
    injected_console: bool,
    explicit_env: bool,
) -> int:
    """Body of :func:`_compute_width` after the ``force_width`` short-circuit.

    Extracted to keep the parent function under the
    ``PLR0911`` (too-many-return-statements) cap while
    preserving the per-rule return so the precedence order
    reads top-to-bottom.

    Precedence (top to bottom):

    1. Explicit ``COLUMNS`` env override -- wins over both Ralph-built
       and caller-injected consoles ONLY when the caller passed
       ``env=...`` to :func:`make_display_context` (because the operator
       who set the env meant to control the rendered width regardless
       of which console Ralph happened to be holding).
    2. Caller-injected ``console=`` -- the caller's own width is the
       source of truth when no explicit env override was provided.
    3. The console's ``_width`` attribute when ``prefer_configured_width``
       is set (Ralph-built console path).
    4. The console's live width attribute.
    5. ``80`` as a final fallback when nothing else resolves.

    Note: the *previous* precedence order consulted
    ``resolved_env.columns`` BEFORE the injected console's width
    regardless of whether the caller passed ``env=...``. That
    silently widened a 40-column test fixture (which used
    ``make_display_context(console=...)`` with no explicit ``env=``)
    to whatever the host shell reported for ``COLUMNS``, breaking
    the ``test_truncation_stability_across_widths`` invariant under
    non-default environments. The fix below gates the
    ``COLUMNS``-wins branch on ``explicit_env=True`` so the two
    invariants are mutually compatible:
    * ``test_columns_env_overrides_console_width`` -- caller passes
      ``env={"COLUMNS": "40"}`` so ``explicit_env=True`` and
      ``resolved_env.columns`` wins over the console's injected width.
    * ``test_truncation_stability_across_widths`` -- caller passes
      no ``env=`` so ``explicit_env=False``; host-shell ``COLUMNS``
      is ignored and the injected console's width is authoritative.
    """
    if explicit_env and resolved_env.columns is not None:
        return resolved_env.columns
    if injected_console:
        # The caller passed an explicit console; its own width is the
        # source of truth when no explicit env override was provided.
        configured_width = cast("object", getattr(console, "_width", None))
        if isinstance(configured_width, int) and configured_width > 0:
            return configured_width
        if console.width > 0:
            return console.width
        return 80
    configured_width = cast("object", getattr(console, "_width", None))
    if prefer_configured_width and isinstance(configured_width, int) and configured_width > 0:
        return configured_width
    return console.width or 80


def _set_injected_console_width(console: Console, width: int) -> None:
    """Align Rich's render width with Ralph's resolved injected-console width."""
    with contextlib.suppress(Exception):
        height = cast("object", getattr(console, "_height", None))
        if not isinstance(height, int) or height <= 0:
            height = console.size.height
        console._width = width
        console._height = height


def _compute_height(console: Console, force_height: int | None) -> int | None:
    """Resolve effective terminal height using the documented precedence.

    P0 (wt-028-display S-6 / AC-03): the height is resolved the same
    way as width so ``refreshed()`` can recompute it on every cycle
    (SIGWINCH on POSIX, poll on Windows) without losing the
    ``force_height`` override the operator pinned at construction
    time. Precedence:

    1. Explicit ``force_height > 0`` -- the operator override.
    2. Explicit ``force_height`` of zero or negative -- the legacy
       opt-out: ``None`` disables height-aware rendering.
    3. The Console's own ``_height`` attribute when set and positive.
    4. The Console's live ``size.height`` when positive.
    5. ``None`` when nothing else resolves (the legacy contract).

    Args:
        console: Console to read height from as fallback.
        force_height: Operator override (``None`` to opt out).

    Returns:
        Effective terminal height in rows, or ``None`` when no source
        resolves a positive integer.
    """
    if force_height is not None and force_height > 0:
        return force_height
    if force_height is not None:
        # Explicit zero / negative -> legacy opt-out.
        return None
    configured_height = cast("object", getattr(console, "_height", None))
    if isinstance(configured_height, int) and configured_height > 0:
        return configured_height
    size_obj: object = getattr(console, "size", None)
    if size_obj is not None:
        candidate: object = getattr(size_obj, "height", None)
        if isinstance(candidate, int) and candidate > 0:
            return candidate
    return None


def _compute_default_mode() -> Literal["default"]:
    """Return the single display mode.

    wt-028-display: Ralph Workflow exposes ONE display mode. The
    historical ``compact`` / ``medium`` / ``wide`` modes are removed.
    """
    return cast("Literal['default']", DEFAULT_MODE)


@dataclass(frozen=True)
class DisplayContext:
    """Immutable container for all display configuration and dependencies.

    This is the single source of truth for display behavior. No renderer
    may construct its own Console. Obtain one via make_display_context().

    Attributes:
        console: Rich Console instance for all rendering.
        theme: Rich Theme with Ralph's Monokai-derived color palette.
        width: Effective terminal width in characters.
        mode: Display mode. Always ``'default'`` (the single mode).
        color_enabled: True when color output is enabled.
        glyphs_enabled: True when Unicode glyphs should be used, False for ASCII fallbacks.
        headline_max_chars: Max characters for condensed headlines.
        condenser_soft_limit: Soft limit for content condensation.
        condenser_hard_limit: Hard limit for content condensation.
        streaming_checkpoint_chars: Chars between streaming checkpoints.
        streaming_checkpoint_fragments: Emit checkpoint every N fragments.
        streaming_dedup_enabled: Whether to deduplicate consecutive identical fragments.
        streaming_checkpoints_enabled: Whether to emit streaming checkpoints.
        thinking_preview_min_chars: Min chars for thinking preview.
        tool_result_headline_min_chars: Min chars for tool result headline.
    """

    console: Console
    theme: Theme
    width: int
    mode: Literal["default"]
    color_enabled: bool
    glyphs_enabled: bool
    headline_max_chars: int
    condenser_soft_limit: int
    condenser_hard_limit: int
    streaming_checkpoint_chars: int
    streaming_checkpoint_fragments: int
    streaming_dedup_enabled: bool
    streaming_checkpoints_enabled: bool
    thinking_preview_min_chars: int
    tool_result_headline_min_chars: int
    # P1 (wt-028-display S-5 / AC-04): maximum comfortable column
    # count for prose and log body text. Consumers that render
    # prose-shaped content (paragraphs, log body, single-line tool
    # summaries) cap the visible width at this value; rules, tables,
    # and aligned columns continue to use the full ``width`` because
    # they have their own width negotiation. Default 100 keeps prose
    # readable on a 250-column monitor; the value is also the upper
    # bound of ``body_measure()`` so the cap is honored on any width.
    body_measure_cap: int = 100
    # Resolved once at context construction so every renderer selects the
    # same accessible palette without probing the terminal independently.
    terminal_background_is_light: bool | None = None
    terminal_background_hex: str | None = None
    # P0 (wt-028-display S-5 / AC-06): the vertical dimension of the
    # terminal is consumed by height-aware presentation -- boxed
    # panels degrade to unboxed headed text below a row threshold
    # so the working area remains usable on a 12-row split pane.
    # ``None`` disables height-aware rendering (the legacy contract).
    height: int | None = None
    # Captured env mapping used to resolve flags; excluded from equality and hash
    env: Mapping[str, str] = field(default_factory=dict, compare=False, repr=False)
    # Stored overrides for refreshed() — excluded from equality and hash
    _resolved_env: _ResolvedEnv = field(
        default_factory=lambda: _ResolvedEnv(
            no_color=False,
            force_color=False,
            columns=None,
            force_ascii=False,
            streaming_dedup_enabled=True,
            streaming_checkpoints_enabled=True,
        ),
        repr=False,
        compare=False,
    )
    _force_width: int | None = field(default=None, repr=False, compare=False)
    _force_glyphs: bool | None = field(default=None, repr=False, compare=False)
    _force_height: int | None = field(default=None, repr=False, compare=False)
    _explicit_env: bool = field(default=False, repr=False, compare=False)

    def glyph_for(self, name: str) -> str:
        """Return the glyph string for the given logical name.

        Args:
            name: Logical glyph name (e.g., 'success', 'error', 'milestone', 'arrow').

        Returns:
            Unicode glyph when glyphs_enabled is True, ASCII fallback otherwise.

        Raises:
            KeyError: If name is not a known glyph key.
        """
        if name not in UNICODE_GLYPHS:
            known = ", ".join(sorted(UNICODE_GLYPHS))
            raise KeyError(f"Unknown glyph {name!r}. Known glyphs: {known}")
        if self.glyphs_enabled:
            return UNICODE_GLYPHS[name]
        return ASCII_GLYPHS[name]

    def is_height_constrained(self, threshold: int = 12) -> bool:
        """Return ``True`` when the height is known and at-or-below ``threshold``.

        P0 (wt-028-display S-6 / AC-05 / DA-005): on a short
        terminal (``height <= threshold``) framed presentation
        degrades to unboxed headed text. The threshold defaults to
        12 rows (the canonical split-pane size where the working
        area shrinks enough that a bordered panel would crowd the
        scrollback). When ``height`` is ``None`` (the legacy
        opt-out) the constraint check returns ``False`` so a
        caller without a known height keeps the full boxed
        presentation -- the height is required to make the
        degradation decision at all.

        The check is at-or-below (``<=``) rather than strict
        less-than (``<``) so the canonical 12-row floor activates
        the constrained presentation -- a 12-row split pane is
        the documented accessibility path (large-text / magnified /
        braille displays) and the framed panels must degrade
        there, not one row later.

        Args:
            threshold: The row count at or below which the
                presentation is considered "height-constrained".
                Defaults to 12 (the canonical short-terminal
                threshold).

        Returns:
            ``True`` when ``self.height`` is set and is at-or-below
            ``threshold``; ``False`` when ``self.height`` is
            ``None`` or is ``> threshold``.
        """
        if self.height is None:
            return False
        return self.height <= threshold

    def body_measure(self) -> int:
        """Return the cap for prose / log body text on this console.

        P1 (wt-028-display S-5 / AC-04): one measure rule for prose
        and log body text. On a very wide terminal (e.g. 250 cols)
        the cap keeps the line at a comfortable measure (the
        ``body_measure_cap`` constant) so the operator's eye does
        not have to track a 250-char line. On a narrow console
        (e.g. 80 cols) the cap is a no-op because the full width
        is already at or below the cap. Rules, tables, and aligned
        columns use ``self.width`` directly; they are not prose.

        The minimum of 40 columns is preserved even on the narrowest
        consoles so a single token wider than the floor still wraps
        through the line wrap machinery instead of crashing on a
        zero-or-negative effective measure.
        """
        return max(40, min(self.width, self.body_measure_cap))

    def refreshed(self) -> DisplayContext:
        """Return a new DisplayContext with refreshed terminal width AND height.

        Re-resolves width using the same precedence rules as
        make_display_context(), preserving any active overrides (COLUMNS,
        force_width) stored at construction time. P0 (wt-028-display
        S-6 / AC-03): height is RE-RESOLVED from the live Console on
        every cycle so a SIGWINCH or poll refresher observes a vertical
        resize, while the ``force_height`` override the operator pinned
        at construction time continues to win. The console identity,
        theme, color_enabled, glyphs_enabled, and adaptive limits are
        unchanged. Mode is always ``'default'``.

        Returns:
            New DisplayContext with updated width and height.
        """
        new_width = _compute_width(
            self._resolved_env,
            self.console,
            self._force_width,
            prefer_configured_width=False,
            explicit_env=self._explicit_env,
        )
        # P0 (wt-028-display S-6 / AC-03): re-resolve height the same
        # way ``make_display_context`` does so a vertical resize is
        # observed. ``_force_height`` is preserved (the operator's
        # construction-time override continues to win) and the
        # Console's own ``size.height`` is the source of truth for
        # non-overridden callers.
        new_height = _compute_height(self.console, self._force_height)

        return DisplayContext(
            console=self.console,
            theme=self.theme,
            width=new_width,
            mode=cast("Literal['default']", DEFAULT_MODE),
            color_enabled=self.color_enabled,
            glyphs_enabled=self.glyphs_enabled,
            headline_max_chars=self.headline_max_chars,
            condenser_soft_limit=self.condenser_soft_limit,
            condenser_hard_limit=self.condenser_hard_limit,
            streaming_checkpoint_chars=self.streaming_checkpoint_chars,
            streaming_checkpoint_fragments=self.streaming_checkpoint_fragments,
            streaming_dedup_enabled=self.streaming_dedup_enabled,
            streaming_checkpoints_enabled=self.streaming_checkpoints_enabled,
            thinking_preview_min_chars=self.thinking_preview_min_chars,
            tool_result_headline_min_chars=self.tool_result_headline_min_chars,
            body_measure_cap=self.body_measure_cap,
            terminal_background_is_light=self.terminal_background_is_light,
            terminal_background_hex=self.terminal_background_hex,
            height=new_height,
            _resolved_env=self._resolved_env,
            env=self.env,
            _force_width=self._force_width,
            _force_glyphs=self._force_glyphs,
            _force_height=self._force_height,
            _explicit_env=self._explicit_env,
        )


def make_display_context(
    *,
    env: Mapping[str, str] | None = None,
    console: Console | None = None,
    output_stream: TextIO | None = None,
    force_width: int | None = None,
    force_glyphs: bool | None = None,
    force_height: int | None = None,
) -> DisplayContext:
    """Create a DisplayContext with resolved terminal metrics and adaptive limits.

    Args:
        env: Environment mapping (defaults to os.environ).
        console: Console to use (defaults to make_console() with env-aware color policy).
        output_stream: Destination used when Ralph constructs the console. Defaults to stdout.
        force_width: Override terminal width detection.
        force_glyphs: Override glyph detection (True=Unicode, False=ASCII, None=auto-detect).
        force_height: Override terminal height detection. ``None``
            falls back to ``console.size.height`` (the canonical
            Rich Console contract); ``None`` disables height-aware
            rendering for callers that don't consume the height
            field.

    Returns:
        Fully initialised DisplayContext.
    """
    if console is not None and output_stream is not None:
        msg = "output_stream cannot be combined with an injected console"
        raise ValueError(msg)
    env_was_provided = env is not None
    env_dict: dict[str, str] = dict(os.environ if env is None else env)
    resolved_env = _resolve_env(env_dict)
    resolved_background: bool | None = None
    resolved_background_hex: str | None = None
    with contextlib.suppress(Exception):
        resolved_background = detect_terminal_background_is_light(env_dict)
        resolved_background_hex = detect_terminal_background_hex(env_dict)
    if console is None:
        injected_console = False
        resolved_console = _build_console(
            resolved_env,
            resolved_background,
            surface_hex=resolved_background_hex,
            output_stream=sys.stdout if output_stream is None else output_stream,
        )
    else:
        from rich.console import Console

        injected_console = True
        resolved_console = console
        _normalize_injected_console_color(resolved_console, resolved_env)
        if isinstance(resolved_console, Console):
            # Every injected console must receive Ralph's resolved theme. A
            # caller-owned theme may contain stale Rich style caches (the
            # historical all-colour-off regression), so push a fresh palette
            # even when a Ralph role already exists.
            resolved_console.push_theme(
                theme_for_background(resolved_background, surface_hex=resolved_background_hex)
            )
    width = _compute_width(
        resolved_env,
        resolved_console,
        force_width,
        injected_console=injected_console,
        explicit_env=env_was_provided,
    )
    if injected_console:
        _set_injected_console_width(resolved_console, width)
    mode = _compute_default_mode()
    limits = _DEFAULT_LIMITS
    # P0 (wt-028-display S-5 / AC-06): height is resolved the same
    # way as width -- an explicit ``force_height`` wins, otherwise we
    # read the console's own ``size.height`` (Rich keeps it fresh).
    # A non-positive or missing height disables height-aware
    # rendering (the legacy contract); callers that want the
    # explicit ``None`` contract pass ``force_height=-1`` so the
    # resolved value is ``None``.
    if force_height is not None and force_height > 0:
        height_value = force_height
    elif force_height is not None:
        # Explicit ``force_height=0`` or negative is treated as the
        # legacy ``None`` opt-out (callers that want to disable
        # height-aware rendering). ``force_height=None`` falls
        # through to the Console's own ``size.height``.
        height_value = None
    else:
        size_obj: object = getattr(resolved_console, "size", None)
        height_attr: int | None = None
        if size_obj is not None:
            candidate: object = getattr(size_obj, "height", None)
            if isinstance(candidate, int) and candidate > 0:
                height_attr = candidate
        positive_height: int | None = height_attr
        height_value = positive_height

    # NO_COLOR wins over FORCE_COLOR per CLI conventions.
    color_enabled = not resolved_env.no_color and not _console_has_no_color(
        resolved_console,
        injected_console=injected_console,
    )

    # Glyph capability detection: force_glyphs > RALPH_FORCE_ASCII > stream encoding > TERM=dumb
    if force_glyphs is not None:
        glyphs_enabled = force_glyphs
    elif injected_console and not env_was_provided and not resolved_env.force_ascii:
        glyphs_enabled = True
    else:
        glyph_file: object = resolved_console.file
        glyphs_enabled = detect_glyph_capability(
            glyph_file if glyph_file is not None else sys.stdout,
            env_dict,
        )

    return DisplayContext(
        console=resolved_console,
        theme=theme_for_background(resolved_background, surface_hex=resolved_background_hex),
        width=width,
        mode=mode,
        height=height_value,
        color_enabled=color_enabled,
        glyphs_enabled=glyphs_enabled,
        headline_max_chars=limits.headline_max_chars,
        condenser_soft_limit=limits.condenser_soft_limit,
        condenser_hard_limit=limits.condenser_hard_limit,
        streaming_checkpoint_chars=limits.streaming_checkpoint_chars,
        streaming_checkpoint_fragments=_STREAMING_CHECKPOINT_FRAGMENTS,
        streaming_dedup_enabled=resolved_env.streaming_dedup_enabled,
        streaming_checkpoints_enabled=resolved_env.streaming_checkpoints_enabled,
        thinking_preview_min_chars=limits.thinking_preview_min_chars,
        tool_result_headline_min_chars=limits.tool_result_headline_min_chars,
        body_measure_cap=limits.body_measure_cap,
        terminal_background_is_light=resolved_background,
        terminal_background_hex=resolved_background_hex,
        env=env_dict,
        _resolved_env=resolved_env,
        _force_width=force_width,
        _force_glyphs=force_glyphs,
        _force_height=force_height,
        _explicit_env=env_was_provided,
    )


def install_sigwinch_refresher(
    ctx_holder: list[DisplayContext],
    on_refresh: Callable[[DisplayContext], None] | None = None,
) -> None:
    """Install a SIGWINCH handler that refreshes DisplayContext on terminal resize.

    On POSIX systems, this installs a signal handler that replaces the
    DisplayContext in ctx_holder[0] with a refreshed version that reflects
    the new terminal size. An optional callback can keep any long-lived
    display objects synced with that refreshed context.

    On non-POSIX systems (Windows), this function is a no-op.

    Args:
        ctx_holder: A single-element list whose 0th element is the DisplayContext
            to refresh on SIGWINCH. The handler replaces ctx_holder[0] with
            ctx_holder[0].refreshed().
        on_refresh: Optional callback invoked with the refreshed context after
            ctx_holder[0] is replaced.

    Note:
        This function must be called from the main thread, as signal.signal
        only works in the main thread. If called from a non-main thread,
        the function returns silently without installing the handler.
    """
    if sys.platform == "win32":
        return
    if threading.main_thread() is not threading.current_thread():
        return

    def handler(_signum: int, _frame: object) -> None:
        refreshed = ctx_holder[0].refreshed()
        ctx_holder[0] = refreshed
        if on_refresh is not None:
            on_refresh(refreshed)

    signal.signal(signal.SIGWINCH, handler)


def install_poll_refresher(
    ctx_holder: list[DisplayContext],
    interval_seconds: float = 2.0,
    on_refresh: Callable[[DisplayContext], None] | None = None,
) -> Callable[[], None]:
    """Start a daemon thread that periodically refreshes DisplayContext.

    This provides a fallback for non-POSIX platforms (Windows) where SIGWINCH
    is not available, or when called from a non-main thread.

    Args:
        ctx_holder: A single-element list whose 0th element is the DisplayContext
            to refresh periodically. The thread replaces ctx_holder[0] with
            ctx_holder[0].refreshed() every interval_seconds.
        interval_seconds: How often to refresh (default 2.0s).
        on_refresh: Optional callback invoked with the refreshed context after
            ctx_holder[0] is replaced.

    Returns:
        A stop() callable that signals the thread to exit and joins it (1s timeout).
    """
    stop_event = threading.Event()

    def poll_loop() -> None:
        while not stop_event.wait(interval_seconds):
            refreshed = ctx_holder[0].refreshed()
            ctx_holder[0] = refreshed
            if on_refresh is not None:
                on_refresh(refreshed)

    thread = threading.Thread(target=poll_loop, daemon=True)
    thread.start()

    def stop() -> None:
        stop_event.set()
        thread.join(timeout=1.0)

    return stop


def install_width_refresher(
    ctx_holder: list[DisplayContext],
    on_refresh: Callable[[DisplayContext], None] | None = None,
) -> Callable[[], None]:
    """Install a width refresher using the best available strategy.

    On POSIX main thread: uses SIGWINCH signal handler (install_sigwinch_refresher).
    On Windows or non-main thread: falls back to poll-based refresher (install_poll_refresher).

    Args:
        ctx_holder: A single-element list whose 0th element is the DisplayContext
            to refresh on resize.
        on_refresh: Optional callback invoked with the refreshed context after
            ctx_holder[0] is replaced.

    Returns:
        A stop() callable (for poll-based refresher; SIGWINCH handler has no cleanup).
    """
    if sys.platform != "win32" and threading.main_thread() is threading.current_thread():
        install_sigwinch_refresher(ctx_holder, on_refresh)
        # SIGWINCH handler cannot be uninstalled, return no-op stop
        return lambda: None
    return install_poll_refresher(ctx_holder, interval_seconds=2.0, on_refresh=on_refresh)


__all__ = [
    "DisplayContext",
    "install_poll_refresher",
    "install_sigwinch_refresher",
    "install_width_refresher",
    "make_display_context",
]


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: D8
# ladder rung: 4
# ----- end AC-14 catalog evidence -----
