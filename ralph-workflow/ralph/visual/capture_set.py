from __future__ import annotations

from dataclasses import dataclass, field

from ralph.visual.capture_cell import CaptureCell


@dataclass(frozen=True, slots=True, eq=False)
class CaptureSet:
    target: str
    cells: tuple[CaptureCell, ...]
    run_id: str
    _ids: frozenset[str] = field(
        default_factory=frozenset[str],
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("CaptureSet.target must be a non-empty string")
        if self.target != self.target.strip():
            raise ValueError("CaptureSet.target must not carry leading/trailing whitespace")
        if not isinstance(self.cells, tuple):
            raise ValueError("CaptureSet.cells must be a tuple of CaptureCell instances")
        if not self.cells:
            raise ValueError(
                "CaptureSet.cells must contain at least one cell; "
                "a single-screenshot run produces no CaptureSet"
            )
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("CaptureSet.run_id must be a non-empty string")
        for cell in self.cells:
            if not isinstance(cell, CaptureCell):
                raise ValueError("CaptureSet.cells must contain only CaptureCell instances")
            if cell.target != self.target:
                raise ValueError(
                    f"CaptureSet target mismatch: cell targets {cell.target!r} "
                    f"but set declares {self.target!r}"
                )
        seen: set[str] = set()
        duplicates: list[str] = []
        for cell in self.cells:
            if cell.cell_id in seen:
                duplicates.append(cell.cell_id)
            seen.add(cell.cell_id)
        if duplicates:
            raise ValueError(
                f"CaptureSet.cells contains duplicate cell ids {duplicates!r}; "
                "a run cannot produce two cells for the same (target, viewport, theme, state)"
            )
        object.__setattr__(self, "_ids", frozenset[str](seen))

    @property
    def cell_ids(self) -> frozenset[str]:
        return self._ids

    def has_cell(self, cell_id: str) -> bool:
        return cell_id in self._ids

    def get(self, cell_id: str) -> CaptureCell | None:
        for cell in self.cells:
            if cell.cell_id == cell_id:
                return cell
        return None

    def by_state(self, state: str) -> tuple[CaptureCell, ...]:
        return tuple(cell for cell in self.cells if cell.state == state)

    def by_viewport(self, viewport_name: str) -> tuple[CaptureCell, ...]:
        return tuple(cell for cell in self.cells if cell.viewport.name == viewport_name)

    def states_covered(self) -> frozenset[str]:
        return frozenset(cell.state for cell in self.cells)

    def viewports_covered(self) -> frozenset[str]:
        return frozenset(cell.viewport.name for cell in self.cells)

    def themes_covered(self) -> frozenset[str]:
        return frozenset(cell.theme for cell in self.cells)

    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)

    def __len__(self) -> int:
        return len(self.cells)


__all__ = ["CaptureSet"]
