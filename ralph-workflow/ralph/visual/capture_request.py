
from __future__ import annotations

from dataclasses import dataclass, field

from ralph.visual.capture_cell import CaptureCell
from ralph.visual.policy_facts import (
    DEFAULT_THEMES,
    REQUIRED_STATES,
    VIEWPORT_DEFAULT_HEIGHT_NARROW,
    VIEWPORT_DEFAULT_HEIGHT_WIDE,
    VIEWPORT_DEFAULT_WIDTH_NARROW,
    VIEWPORT_DEFAULT_WIDTH_WIDE,
    Viewport,
)

MIN_MATRIX_CELLS: int = 2

MIN_VIEWPORTS: int = 2


def _default_viewports() -> tuple[Viewport, ...]:
    return (
        Viewport(
            name="narrow",
            width=VIEWPORT_DEFAULT_WIDTH_NARROW,
            height=VIEWPORT_DEFAULT_HEIGHT_NARROW,
        ),
        Viewport(
            name="wide",
            width=VIEWPORT_DEFAULT_WIDTH_WIDE,
            height=VIEWPORT_DEFAULT_HEIGHT_WIDE,
        ),
    )




def _validate_string_tuple(
    *,
    field_name: str,
    values: object,
    error_prefix: str,
) -> None:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{error_prefix} must be a non-empty tuple of strings")
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} entries must be non-empty strings")


def _validate_viewports(viewports: object) -> None:
    if not isinstance(viewports, tuple):
        raise ValueError("CaptureRequest.viewports must be a tuple of Viewport instances")
    if len(viewports) < MIN_VIEWPORTS:
        raise ValueError(
            f"CaptureRequest must cover at least {MIN_VIEWPORTS} viewports "
            f"(narrow + wide); got {len(viewports)}"
        )
    for viewport in viewports:
        if not isinstance(viewport, Viewport):
            raise ValueError("CaptureRequest.viewports must contain only Viewport instances")


def _validate_matrix_cells(matrix: object) -> None:
    if not isinstance(matrix, tuple):
        raise ValueError("CaptureRequest.matrix must be a tuple of CaptureCell instances")
    for cell in matrix:
        if not isinstance(cell, CaptureCell):
            raise ValueError("CaptureRequest.matrix must contain only CaptureCell instances")


def _expected_matrix(
    *,
    target: str,
    viewports: tuple[Viewport, ...],
    themes: tuple[str, ...],
    states: tuple[str, ...],
) -> dict[tuple[str, str, int, int, str, str], CaptureCell]:
    expected: dict[tuple[str, str, int, int, str, str], CaptureCell] = {}
    for viewport in viewports:
        for theme in themes:
            for state in states:
                cell = CaptureCell.mint(
                    target=target,
                    viewport=viewport,
                    theme=theme,
                    state=state,
                )
                expected[cell.key] = cell
    return expected


def _verify_matrix_shape(
    *,
    target: str,
    viewports: tuple[Viewport, ...],
    themes: tuple[str, ...],
    states: tuple[str, ...],
    matrix: tuple[CaptureCell, ...],
) -> None:
    expected = _expected_matrix(
        target=target,
        viewports=viewports,
        themes=themes,
        states=states,
    )
    present = {cell.key: cell for cell in matrix}

    if present.keys() != expected.keys():
        missing = sorted(set(expected.keys()) - set(present.keys()))
        extra = sorted(set(present.keys()) - set(expected.keys()))
        problems: list[str] = []
        if missing:
            problems.append(f"missing cells: {missing}")
        if extra:
            problems.append(f"unexpected cells: {extra}")
        raise ValueError(
            "CaptureRequest.matrix does not match the cartesian product "
            f"viewports x themes x states; {'; '.join(problems)}"
        )

    for key, cell in present.items():
        expected_cell = expected[key]
        if cell.cell_id != expected_cell.cell_id:
            raise ValueError(
                f"CaptureRequest cell {key} has cell_id {cell.cell_id!r}; "
                f"expected server-minted id {expected_cell.cell_id!r}"
            )


def _verify_required_states(states: tuple[str, ...]) -> None:
    declared = set(states)
    missing = [state for state in REQUIRED_STATES if state not in declared]
    if missing:
        raise ValueError(
            "CaptureRequest.states must include the full canonical set "
            f"{list(REQUIRED_STATES)}; missing {missing} — "
            "single-screenshot defaults are rejected"
        )




@dataclass(frozen=True)
class CaptureRequest:

    target: str
    viewports: tuple[Viewport, ...]
    themes: tuple[str, ...]
    states: tuple[str, ...]
    matrix: tuple[CaptureCell, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("CaptureRequest.target must be a non-empty string")
        if self.target != self.target.strip():
            raise ValueError("CaptureRequest.target must not carry leading/trailing whitespace")

        _validate_viewports(self.viewports)
        _validate_string_tuple(
            field_name="CaptureRequest.themes",
            values=self.themes,
            error_prefix="CaptureRequest.themes",
        )
        _validate_string_tuple(
            field_name="CaptureRequest.states",
            values=self.states,
            error_prefix="CaptureRequest.states",
        )
        _validate_matrix_cells(self.matrix)

        if len(self.matrix) < MIN_MATRIX_CELLS:
            raise ValueError(
                f"CaptureRequest.matrix must cover at least {MIN_MATRIX_CELLS} cells; "
                f"got {len(self.matrix)} — single-screenshot defaults are rejected"
            )

        _verify_matrix_shape(
            target=self.target,
            viewports=self.viewports,
            themes=self.themes,
            states=self.states,
            matrix=self.matrix,
        )
        _verify_required_states(self.states)

    @classmethod
    def build(
        cls,
        *,
        target: str,
        viewports: tuple[Viewport, ...] | None = None,
        themes: tuple[str, ...] | None = None,
        states: tuple[str, ...] | None = None,
    ) -> CaptureRequest:
        resolved_viewports = viewports if viewports is not None else _default_viewports()
        resolved_themes = themes if themes is not None else DEFAULT_THEMES
        resolved_states = states if states is not None else REQUIRED_STATES
        matrix = tuple(
            CaptureCell.mint(
                target=target,
                viewport=viewport,
                theme=theme,
                state=state,
            )
            for viewport in resolved_viewports
            for theme in resolved_themes
            for state in resolved_states
        )
        return cls(
            target=target,
            viewports=resolved_viewports,
            themes=resolved_themes,
            states=resolved_states,
            matrix=matrix,
        )

    @property
    def cell_ids(self) -> frozenset[str]:
        return frozenset(cell.cell_id for cell in self.matrix)

    def cells_for(self, *, state: str | None = None) -> tuple[CaptureCell, ...]:
        if state is None:
            return self.matrix
        return tuple(cell for cell in self.matrix if cell.state == state)


__all__ = [
    "MIN_MATRIX_CELLS",
    "MIN_VIEWPORTS",
    "REQUIRED_STATES",
    "VIEWPORT_DEFAULT_HEIGHT_NARROW",
    "VIEWPORT_DEFAULT_HEIGHT_WIDE",
    "VIEWPORT_DEFAULT_WIDTH_NARROW",
    "VIEWPORT_DEFAULT_WIDTH_WIDE",
    "CaptureRequest",
]
