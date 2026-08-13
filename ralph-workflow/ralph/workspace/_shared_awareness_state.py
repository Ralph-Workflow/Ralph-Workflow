"""Typed snapshot returned by :meth:`SharedAwarenessSidecar.poll`.

Extracted from :mod:`ralph.workspace._shared_awareness` so each module
defines exactly one public top-level class (repo-structure audit).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SharedAwarenessState"]


@dataclass(frozen=True)
class SharedAwarenessState:
    """Typed snapshot returned by :meth:`SharedAwarenessSidecar.poll`."""

    epoch: int
    paths: list[str]
    overflowed: bool
    owner_id: str
    changed: bool
