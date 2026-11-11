
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ralph.visual.policy_facts import Viewport

_CELL_ID_HEX_LEN: int = 64

_HASH_SEP: str = "\x00"

_HASH_INPUT_TEMPLATE: str = (
    "{target}{sep}{vp_name}{sep}{vp_width}x{vp_height}{sep}{theme}{sep}{state}"
)


def _hash_cell(*, target: str, viewport: Viewport, theme: str, state: str) -> str:
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

    target: str
    viewport: Viewport
    theme: str
    state: str
    cell_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("CaptureCell.target must be a non-empty string")
        if not isinstance(self.viewport, Viewport):
            raise ValueError("CaptureCell.viewport must be a Viewport instance")
        if not isinstance(self.theme, str) or not self.theme.strip():
            raise ValueError("CaptureCell.theme must be a non-empty string")
        if not isinstance(self.state, str) or not self.state.strip():
            raise ValueError("CaptureCell.state must be a non-empty string")
        if not isinstance(self.cell_id, str) or len(self.cell_id) != _CELL_ID_HEX_LEN:
            raise ValueError(f"CaptureCell.cell_id must be a {_CELL_ID_HEX_LEN}-char hex string")
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
        return (
            self.target,
            self.viewport.name,
            self.viewport.width,
            self.viewport.height,
            self.theme,
            self.state,
        )


__all__ = ["CaptureCell"]
