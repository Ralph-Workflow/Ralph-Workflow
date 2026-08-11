"""Ralph visual capture and design-verdict package.

The ``ralph.visual`` package is the single source of truth for:

* parsing design-system policy facts into typed, validated structures
  (:mod:`ralph.visual.policy_facts`),
* describing the capture matrix for a visual run (:mod:`ralph.visual.capture_request`,
  :mod:`ralph.visual.capture_cell`),
* holding run-owned capture evidence (:mod:`ralph.visual.capture_set`),
* describing reviewer observations (:mod:`ralph.visual.visual_finding`),
* and producing the signed-off design verdict artifact (:mod:`ralph.visual.design_verdict`).

Every layer in this package enforces one rule: visual coverage is
never a single screenshot and never a single ``pass`` based on the
agent's intuition. The package exists so the rest of Ralph can treat
visual review as a typed contract with explicit, falsifiable inputs.
"""

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

# Backwards-compat alias used in some downstream consumers. Declared
# here (after the canonical ``Viewport`` symbol) so a typo in the
# alias name surfaces immediately during import.
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
