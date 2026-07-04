"""Display mode constants for Ralph's terminal output.

After the wt-028-display consolidation, Ralph Workflow exposes exactly ONE
display mode: ``default``. There are no narrow / medium / wide tiers,
no width-based adaptive limits, and no width-derived dispatch. The
persistent bottom Status Bar is the single owner of run-level layout,
color, spacing, truncation, and live-update behavior. Width-driven
degradation happens in a documented order (see
:mod:`ralph.display.status_bar`):

1. Long paths middle-truncate to absorb excess length on long paths.
2. Long phase labels tail-truncate to absorb excess length on labels.
3. Iteration label form degrades canonical (``Dev 1/3`` /
   ``Analysis 2/5``) -> compact (``D1/3`` / ``A2/5``) -> minimal
   (``1/3`` / ``2/5``) below the canonical-fit threshold (40 cols).
4. The phase marker is dropped below the marker-fit threshold.
5. Per-iteration glyphs are dropped below the glyph-fit threshold.
6. Iteration segments drop one at a time (outer_dev first, then
   inner_analysis, then both) below the iteration-visibility
   threshold (14 cols). Below that threshold the bar degrades
   cleanly to whatever subset of phase + path fits, and the
   ``len(plain) <= ctx.width`` invariant holds at every width.

The historical width-tier threshold constants and the historical
``RALPH_FORCE_NARROW`` environment variable are removed; the env var is
silently ignored.
"""

from __future__ import annotations

DEFAULT_MODE: str = "default"

__all__ = ["DEFAULT_MODE"]
