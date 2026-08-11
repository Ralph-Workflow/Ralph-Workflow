"""CaptureSet: the immutable result of a single visual capture run.

A :class:`CaptureSet` is the run-owned evidence for one target. Equality
is on identity (the run that produced it), NOT on cell contents: two
runs over the same matrix produce two distinct CaptureSets because the
agent contract is that the before/after evidence is run-scoped — sharing
a set across runs would silently mix evidence from different
invocations.

The set is immutable (``frozen=True``, ``slots=True``) so a CaptureSet
that moves out of the agent's hand cannot be retroactively edited; any
mutation has to mint a new run-owned set, which the verdict layer
detects as a substituted baseline and rejects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.visual.capture_cell import CaptureCell


@dataclass(frozen=True, slots=True, eq=False)
class CaptureSet:
    """Run-owned, identity-keyed collection of capture cells for one target."""

    target: str
    cells: tuple[CaptureCell, ...]
    run_id: str
    # ``_ids`` is a cached projection of the cell-id set so callers can
    # membership-test without iterating ``cells`` every time. We mark
    # it with ``eq=False`` via ``compare=False`` so the dataclass
    # identity-equality does not see it. The explicit type parameter
    # keeps mypy from inferring ``Any`` from the default factory.
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
        # Reject duplicate cells inside the same run. A run cannot
        # produce two cells with the same key; if it does, the agent
        # is shipping ambiguous evidence and the verdict layer cannot
        # tell which cell is canonical.
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
        # Materialise the cached id set. ``object.__setattr__`` is
        # required because the dataclass is frozen; we cannot use
        # ``self._ids = ...`` directly. The cast is necessary because
        # ``object.__setattr__`` is typed as accepting ``Any`` and the
        # assignment would otherwise leak ``Any`` into the field type.
        object.__setattr__(self, "_ids", frozenset[str](seen))

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def cell_ids(self) -> frozenset[str]:
        """Return the set of cell ids inside this run."""
        return self._ids

    def has_cell(self, cell_id: str) -> bool:
        """Return True if this run produced a cell with the given id."""
        return cell_id in self._ids

    def get(self, cell_id: str) -> CaptureCell | None:
        """Return the cell with the given id, or None if absent."""
        for cell in self.cells:
            if cell.cell_id == cell_id:
                return cell
        return None

    def by_state(self, state: str) -> tuple[CaptureCell, ...]:
        """Return every cell captured for the given state."""
        return tuple(cell for cell in self.cells if cell.state == state)

    def by_viewport(self, viewport_name: str) -> tuple[CaptureCell, ...]:
        """Return every cell captured for the given viewport name."""
        return tuple(cell for cell in self.cells if cell.viewport.name == viewport_name)

    def states_covered(self) -> frozenset[str]:
        """Return the set of states covered by this run."""
        return frozenset(cell.state for cell in self.cells)

    def viewports_covered(self) -> frozenset[str]:
        """Return the set of viewport names covered by this run."""
        return frozenset(cell.viewport.name for cell in self.cells)

    def themes_covered(self) -> frozenset[str]:
        """Return the set of themes covered by this run."""
        return frozenset(cell.theme for cell in self.cells)

    # ------------------------------------------------------------------
    # Identity-keyed equality
    # ------------------------------------------------------------------

    # Equality on identity: two CaptureSets are equal only if they ARE
    # the same Python object. This is the agent-visible contract —
    # "the same CaptureSet" means "from the same run", never "the same
    # cell contents from a different run".
    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)

    def __len__(self) -> int:
        return len(self.cells)


__all__ = ["CaptureSet"]
