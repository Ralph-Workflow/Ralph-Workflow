"""Typed CaptureRequest: the immutable plan for a single visual capture run.

The request carries the target plus the full cartesian product of
``viewports x themes x states``. It is the single source of truth that
the resulting :class:`~ralph.visual.capture_set.CaptureSet` must
satisfy — any capture set whose cell set is missing from the matrix is
invalid, because visual coverage cannot be summarised out of order.

A :class:`CaptureRequest` rejects the single-screenshot default:
every request must declare at least one narrow + one wide viewport and
every state declared in :data:`REQUIRED_STATES`. A request that omits
any required state is a misconfiguration, not a preference.
"""

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

# Minimum number of cells a CaptureRequest must cover. The default
# policy produces 2 viewports x 2 themes x 5 states = 20 cells; we
# reject anything below the bare 2-cell floor because a one-cell
# matrix is a single-screenshot request in disguise.
MIN_MATRIX_CELLS: int = 2

# Minimum number of viewports. Narrow + wide is the contract.
MIN_VIEWPORTS: int = 2


def _default_viewports() -> tuple[Viewport, ...]:
    """Return the canonical narrow + wide viewport pair."""
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


# ---------------------------------------------------------------------------
# Validation helpers — split out of __post_init__ to keep the dataclass
# entry point short and let ruff's PLR0912 (too-many-branches) gate pass
# without weakening lint enforcement.
# ---------------------------------------------------------------------------


def _validate_string_tuple(
    *,
    field_name: str,
    values: object,
    error_prefix: str,
) -> None:
    """Validate a tuple-of-non-empty-strings field."""
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{error_prefix} must be a non-empty tuple of strings")
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} entries must be non-empty strings")


def _validate_viewports(viewports: object) -> None:
    """Validate the viewports tuple."""
    if not isinstance(viewports, tuple):
        raise ValueError("CaptureRequest.viewports must be a tuple of Viewport instances")
    if len(viewports) < MIN_VIEWPORTS:
        raise ValueError(
            f"CaptureRequest must cover at least {MIN_VIEWPORTS} viewports "
            f"(narrow + wide); got {len(viewports)}"
        )
    for viewport in viewports:
        if not isinstance(viewport, Viewport):
            raise ValueError(
                "CaptureRequest.viewports must contain only Viewport instances"
            )


def _validate_matrix_cells(matrix: object) -> None:
    """Validate the matrix is a non-empty tuple of CaptureCell instances."""
    if not isinstance(matrix, tuple):
        raise ValueError("CaptureRequest.matrix must be a tuple of CaptureCell instances")
    for cell in matrix:
        if not isinstance(cell, CaptureCell):
            raise ValueError(
                "CaptureRequest.matrix must contain only CaptureCell instances"
            )


def _expected_matrix(
    *, target: str, viewports: tuple[Viewport, ...],
    themes: tuple[str, ...], states: tuple[str, ...],
) -> dict[tuple[str, str, int, int, str, str], CaptureCell]:
    """Build the canonical cartesian-product cell set keyed by cell.key."""
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
    """Verify the matrix equals the full cartesian product of the declared axes."""
    expected = _expected_matrix(
        target=target, viewports=viewports, themes=themes, states=states,
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

    # Verify every present cell has the server-minted id for its key —
    # i.e. no caller-supplied id can sneak past us.
    for key, cell in present.items():
        expected_cell = expected[key]
        if cell.cell_id != expected_cell.cell_id:
            raise ValueError(
                f"CaptureRequest cell {key} has cell_id {cell.cell_id!r}; "
                f"expected server-minted id {expected_cell.cell_id!r}"
            )


def _verify_required_states(states: tuple[str, ...]) -> None:
    """Reject a request whose states miss any canonical state."""
    declared = set(states)
    missing = [state for state in REQUIRED_STATES if state not in declared]
    if missing:
        raise ValueError(
            "CaptureRequest.states must include the full canonical set "
            f"{list(REQUIRED_STATES)}; missing {missing} — "
            "single-screenshot defaults are rejected"
        )


# ---------------------------------------------------------------------------
# Typed structure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptureRequest:
    """Immutable plan for one visual capture run."""

    target: str
    viewports: tuple[Viewport, ...]
    themes: tuple[str, ...]
    states: tuple[str, ...]
    matrix: tuple[CaptureCell, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("CaptureRequest.target must be a non-empty string")
        if self.target != self.target.strip():
            raise ValueError(
                "CaptureRequest.target must not carry leading/trailing whitespace"
            )

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
        """Construct a CaptureRequest with the canonical defaults filled in.

        ``viewports`` defaults to the narrow + wide canonical pair;
        ``themes`` defaults to :data:`DEFAULT_THEMES`; ``states``
        defaults to :data:`REQUIRED_STATES`. The matrix is minted as
        the full cartesian product so callers cannot accidentally
        pass a half-built one.
        """
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
        """Return the set of server-minted cell ids covered by this request."""
        return frozenset(cell.cell_id for cell in self.matrix)

    def cells_for(self, *, state: str | None = None) -> tuple[CaptureCell, ...]:
        """Return the matrix cells, optionally filtered by state."""
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
