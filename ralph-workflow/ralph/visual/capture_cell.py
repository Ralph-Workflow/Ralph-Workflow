"""Single cell in a visual capture matrix.

A :class:`CaptureCell` is the atomic ``(target, viewport, theme, state)``
tuple the visual pipeline produces and consumes. The ``cell_id`` is a
server-minted SHA-256 digest of the canonical key — never caller-supplied —
so a server can cross-reference cells across runs without trusting the
caller's id generator. Two cells with the same key always mint the same
id; two cells with different keys always mint different ids (modulo
unrelated SHA-256 collisions, which are not treated as a contract).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ralph.visual.policy_facts import Viewport

# SHA-256 hex digest length. Pinned here so a future swap to SHA-3 or
# a truncated digest changes the cell-id width in one place.
_CELL_ID_HEX_LEN: int = 64

# Canonical separator inside the hash input. NUL is illegal in all
# input fields (target names, viewport labels, themes, states) so it
# cannot appear inside a field and therefore cannot produce a
# collision between e.g. ``("ab", "c")`` and ``("a", "bc")``.
_HASH_SEP: str = "\x00"

_HASH_INPUT_TEMPLATE: str = (
    "{target}{sep}{vp_name}{sep}{vp_width}x{vp_height}{sep}{theme}{sep}{state}"
)


def _hash_cell(
    *, target: str, viewport: Viewport, theme: str, state: str
) -> str:
    """Compute the canonical cell id for a (target, viewport, theme, state)."""
    payload = _HASH_INPUT_TEMPLATE.format(
        target=target,
        sep=_HASH_SEP,
        vp_name=viewport.name,
        vp_width=viewport.width,
        vp_height=viewport.height,
        theme=theme,
        state=state,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CaptureCell:
    """Atomic (target, viewport, theme, state) entry in a capture matrix."""

    target: str
    viewport: Viewport
    theme: str
    state: str
    cell_id: str

    def __post_init__(self) -> None:
        # No ``Any`` and no runtime duck-typing: every field is
        # validated up front so a malformed CaptureCell cannot slip
        # into a CaptureSet and silently break a verdict.
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("CaptureCell.target must be a non-empty string")
        if not isinstance(self.viewport, Viewport):
            raise ValueError("CaptureCell.viewport must be a Viewport instance")
        if not isinstance(self.theme, str) or not self.theme.strip():
            raise ValueError("CaptureCell.theme must be a non-empty string")
        if not isinstance(self.state, str) or not self.state.strip():
            raise ValueError("CaptureCell.state must be a non-empty string")
        if not isinstance(self.cell_id, str) or len(self.cell_id) != _CELL_ID_HEX_LEN:
            raise ValueError(
                f"CaptureCell.cell_id must be a {_CELL_ID_HEX_LEN}-char hex string"
            )
        if self.target != self.target.strip():
            raise ValueError("CaptureCell.target must not carry leading/trailing whitespace")
        if self.theme != self.theme.strip():
            raise ValueError("CaptureCell.theme must not carry leading/trailing whitespace")
        if self.state != self.state.strip():
            raise ValueError("CaptureCell.state must not carry leading/trailing whitespace")

    @classmethod
    def mint(
        cls,
        *,
        target: str,
        viewport: Viewport,
        theme: str,
        state: str,
    ) -> CaptureCell:
        """Server-mint a new CaptureCell with a deterministic id.

        Use this constructor whenever the caller does NOT already hold a
        server-issued id; the resulting cell is content-addressable,
        so two callers minting the same key produce equal cells.
        """
        cell_id = _hash_cell(target=target, viewport=viewport, theme=theme, state=state)
        return cls(
            target=target,
            viewport=viewport,
            theme=theme,
            state=state,
            cell_id=cell_id,
        )

    @property
    def key(self) -> tuple[str, str, int, int, str, str]:
        """Return the canonical (target, viewport.name, w, h, theme, state) tuple."""
        return (
            self.target,
            self.viewport.name,
            self.viewport.width,
            self.viewport.height,
            self.theme,
            self.state,
        )


__all__ = ["CaptureCell"]
