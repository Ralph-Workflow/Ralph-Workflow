"""WebSearchError — raised when a web-search backend fails."""

from __future__ import annotations


class WebSearchError(RuntimeError):
    """Raised when a web-search backend fails."""


__all__ = ["WebSearchError"]
