"""Display mode constants for Ralph's terminal output.

After the wt-028-display consolidation, Ralph Workflow exposes exactly ONE
display mode: ``default``. There are no narrow / medium / wide tiers,
no width-based adaptive limits, and no width-derived dispatch. The
persistent bottom Status Bar always renders all applicable fields
(working directory, active phase, applicable outer development
iteration, and applicable inner analysis iteration) regardless of
terminal width — only the long-path middle-truncation and long-phase
tail-truncation adapt to width.

The historical ``NARROW_THRESHOLD`` (60 cols) and ``MEDIUM_THRESHOLD``
(100 cols) constants are removed; importing either raises
:data:`ImportError`. The historical ``RALPH_FORCE_NARROW`` environment
variable is silently ignored. The historical ``force_mode`` keyword
argument to :func:`make_display_context` raises
:data:`NotImplementedError` if a non-``None`` value is passed.
"""

from __future__ import annotations

DEFAULT_MODE: str = "default"

__all__ = ["DEFAULT_MODE"]
