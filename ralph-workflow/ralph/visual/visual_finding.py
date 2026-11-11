
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal


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

VISUAL_SEVERITIES: Final[tuple[str, ...]] = (
    "blocker",
    "major",
    "minor",
    "nit",
)

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

SeverityT = VisualSeverityT

REGION_MIN_PIXELS: Final[int] = 1
REGION_MAX_PIXELS: Final[int] = 32_000_000

_NARRATIVE_MAX_LEN: Final[int] = 8 * 1024




@dataclass(frozen=True, slots=True)
class Region:

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
            raise ValueError(f"Region.w must be >= {REGION_MIN_PIXELS} pixel, got {self.w}")
        if self.h < REGION_MIN_PIXELS:
            raise ValueError(f"Region.h must be >= {REGION_MIN_PIXELS} pixel, got {self.h}")
        if self.w * self.h > REGION_MAX_PIXELS:
            raise ValueError(
                f"Region area {self.w * self.h} exceeds REGION_MAX_PIXELS={REGION_MAX_PIXELS}; "
                "split into smaller findings"
            )

    @property
    def area(self) -> int:
        return self.w * self.h

    def contains(self, other: Region) -> bool:
        return (
            other.x >= self.x
            and other.y >= self.y
            and other.x + other.w <= self.x + self.w
            and other.y + other.h <= self.y + self.h
        )


@dataclass(frozen=True, slots=True)
class _VisualFinding:

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
