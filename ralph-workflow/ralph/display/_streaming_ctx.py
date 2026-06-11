"""Context bundle for streaming block management.

Internal leaf module (wt-007-consolidate-display). Re-exports
:class:`_StreamingCtx` from the previous
``ralph.display.plain_renderer._streaming_ctx`` location.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _StreamingCtx:
    """Context bundle for streaming block management helpers."""

    unit_id: str
    kind: str
    content: str
    base_tag: str
    timestamp: str
