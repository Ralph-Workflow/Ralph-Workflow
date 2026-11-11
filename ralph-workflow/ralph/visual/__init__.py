
from __future__ import annotations

from ralph.visual.capture_cell import CaptureCell
from ralph.visual.capture_request import (
    MIN_MATRIX_CELLS,
    REQUIRED_STATES,
    VIEWPORT_DEFAULT_HEIGHT_NARROW,
    VIEWPORT_DEFAULT_HEIGHT_WIDE,
    VIEWPORT_DEFAULT_WIDTH_NARROW,
    VIEWPORT_DEFAULT_WIDTH_WIDE,
    CaptureRequest,
)
from ralph.visual.capture_set import CaptureSet
from ralph.visual.design_verdict import (
    VERDICT_BLOCKED,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_VALUES,
    DesignVerdict,
)
from ralph.visual.policy_facts import (
    DEFAULT_THEMES,
    PolicyFacts,
    Viewport,
    parse_policy_facts,
)
from ralph.visual.visual_finding import (
    REGION_MAX_PIXELS,
    REGION_MIN_PIXELS,
    VISUAL_DIMENSIONS,
    VISUAL_SEVERITIES,
    Region,
    SeverityT,
    VisualDimensionT,
    VisualFinding,
    VisualSeverityT,
)

Viewpoint = Viewport

__all__ = [
    "DEFAULT_THEMES",
    "MIN_MATRIX_CELLS",
    "REGION_MAX_PIXELS",
    "REGION_MIN_PIXELS",
    "REQUIRED_STATES",
    "VERDICT_BLOCKED",
    "VERDICT_FAIL",
    "VERDICT_PASS",
    "VERDICT_VALUES",
    "VIEWPORT_DEFAULT_HEIGHT_NARROW",
    "VIEWPORT_DEFAULT_HEIGHT_WIDE",
    "VIEWPORT_DEFAULT_WIDTH_NARROW",
    "VIEWPORT_DEFAULT_WIDTH_WIDE",
    "VISUAL_DIMENSIONS",
    "VISUAL_SEVERITIES",
    "CaptureCell",
    "CaptureRequest",
    "CaptureSet",
    "DesignVerdict",
    "PolicyFacts",
    "Region",
    "SeverityT",
    "Viewpoint",
    "Viewport",
    "VisualDimensionT",
    "VisualFinding",
    "VisualSeverityT",
    "parse_policy_facts",
]
