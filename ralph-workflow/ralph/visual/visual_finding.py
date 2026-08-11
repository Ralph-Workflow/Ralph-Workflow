"""VisualFinding: a single observation a design reviewer raises about a capture.

A :class:`VisualFinding` is the smallest atomic claim a verdict emits.
It pins the observation to a specific ``capture_id`` (one cell of a
:class:`~ralph.visual.capture_set.CaptureSet`) and a rectangular
``region`` inside that capture, names the dimension the reviewer is
calling out, and carries a free-text narrative that the agent
back-translates into an actionable change.

The dimension vocabulary is closed: every visual finding names one of
the eight canonical dimensions (hierarchy, alignment, spacing,
typography, legibility, density, completeness, clipping). Anything
else is rejected at construction time so the verdict layer can
group findings by dimension without spelling drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

# The eight canonical dimensions. Anything outside this set is a
# vocabulary drift; the verdict layer groups findings by dimension and
# cannot defend itself against untyped labels.
VISUAL_DIMENSIONS: Final[tuple[str, ...]] = (
    "hierarchy",
    "alignment",
    "spacing",
    "typography",
    "legibility",
    "density",
    "completeness",
    "clipping",
)

# Four canonical severities. ``blocker`` halts the verdict at ``fail``;
# ``major`` contributes to ``fail``; ``minor`` and ``nit`` contribute
# to ``pass`` with reviewer follow-up. Anything outside this set is
# rejected.
VISUAL_SEVERITIES: Final[tuple[str, ...]] = (
    "blocker",
    "major",
    "minor",
    "nit",
)

# Literal aliases for downstream callers that prefer ``Literal[...]``
# over a plain string field. Both names are exported so a consumer
# can pick the type form that suits them.
VisualDimensionT = Literal[
    "hierarchy",
    "alignment",
    "spacing",
    "typography",
    "legibility",
    "density",
    "completeness",
    "clipping",
]
VisualSeverityT = Literal["blocker", "major", "minor", "nit"]

# Backwards-compat alias kept for downstream imports that pre-date
# the split into ``VisualDimensionT`` / ``VisualSeverityT``. Both
# names point at the same set of literals.
SeverityT = VisualSeverityT

# Reasonable bounds on the rectangular region a finding cites. We
# deliberately allow zero-size regions in principle (a 1x1 point can
# legitimately be the entire finding, e.g. "this icon is the wrong
# glyph") but we cap the upper bound at 4Kx8K to keep findings from
# silently growing into "the whole screen" claims that should have
# been multiple findings instead.
REGION_MIN_PIXELS: Final[int] = 1
REGION_MAX_PIXELS: Final[int] = 32_000_000  # 4K x 8K, generous ceiling.

# Maximum narrative length. Cap is generous (8 KiB) but bounded so a
# runaway narrative cannot bloat a verdict payload.
_NARRATIVE_MAX_LEN: Final[int] = 8 * 1024


# ---------------------------------------------------------------------------
# Typed structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Region:
    """Rectangular region (x, y, w, h) inside a capture, in capture pixels."""

    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        if not isinstance(self.x, int) or isinstance(self.x, bool):
            raise ValueError(f"Region.x must be int, got {type(self.x).__name__}")
        if not isinstance(self.y, int) or isinstance(self.y, bool):
            raise ValueError(f"Region.y must be int, got {type(self.y).__name__}")
        if not isinstance(self.w, int) or isinstance(self.w, bool):
            raise ValueError(f"Region.w must be int, got {type(self.w).__name__}")
        if not isinstance(self.h, int) or isinstance(self.h, bool):
            raise ValueError(f"Region.h must be int, got {type(self.h).__name__}")
        if self.x < 0:
            raise ValueError(f"Region.x must be >= 0, got {self.x}")
        if self.y < 0:
            raise ValueError(f"Region.y must be >= 0, got {self.y}")
        if self.w < REGION_MIN_PIXELS:
            raise ValueError(
                f"Region.w must be >= {REGION_MIN_PIXELS} pixel, got {self.w}"
            )
        if self.h < REGION_MIN_PIXELS:
            raise ValueError(
                f"Region.h must be >= {REGION_MIN_PIXELS} pixel, got {self.h}"
            )
        if self.w * self.h > REGION_MAX_PIXELS:
            raise ValueError(
                f"Region area {self.w * self.h} exceeds REGION_MAX_PIXELS={REGION_MAX_PIXELS}; "
                "split into smaller findings"
            )

    @property
    def area(self) -> int:
        """Return the region's pixel area."""
        return self.w * self.h

    def contains(self, other: Region) -> bool:
        """Return True if ``other`` is fully inside this region."""
        return (
            other.x >= self.x
            and other.y >= self.y
            and other.x + other.w <= self.x + self.w
            and other.y + other.h <= self.y + self.h
        )


@dataclass(frozen=True, slots=True)
class _VisualFinding:
    """A reviewer observation pinned to one capture cell and one region."""

    capture_id: str
    region: Region
    dimension: str
    severity: str
    narrative: str

    def __post_init__(self) -> None:
        if not isinstance(self.capture_id, str) or not self.capture_id.strip():
            raise ValueError("VisualFinding.capture_id must be a non-empty string")
        if not isinstance(self.region, Region):
            raise ValueError("VisualFinding.region must be a Region instance")
        if self.dimension not in VISUAL_DIMENSIONS:
            raise ValueError(
                f"VisualFinding.dimension must be one of {list(VISUAL_DIMENSIONS)}; "
                f"got {self.dimension!r}"
            )
        if self.severity not in VISUAL_SEVERITIES:
            raise ValueError(
                f"VisualFinding.severity must be one of {list(VISUAL_SEVERITIES)}; "
                f"got {self.severity!r}"
            )
        if not isinstance(self.narrative, str) or not self.narrative.strip():
            raise ValueError("VisualFinding.narrative must be a non-empty string")
        if len(self.narrative) > _NARRATIVE_MAX_LEN:
            raise ValueError(
                f"VisualFinding.narrative length {len(self.narrative)} exceeds "
                f"_NARRATIVE_MAX_LEN={_NARRATIVE_MAX_LEN}"
            )


VisualFinding = _VisualFinding
VisualFinding.__name__ = "VisualFinding"
VisualFinding.__qualname__ = "VisualFinding"


__all__ = [
    "REGION_MAX_PIXELS",
    "REGION_MIN_PIXELS",
    "VISUAL_DIMENSIONS",
    "VISUAL_SEVERITIES",
    "Region",
    "SeverityT",
    "VisualDimensionT",
    "VisualFinding",
    "VisualSeverityT",
]
