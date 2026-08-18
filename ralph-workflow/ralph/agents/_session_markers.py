"""Session boundary markers shared between the invoke and parser packages.

These constants are deliberately kept in a standalone module with no
upstream dependencies so that parsers can recognize session boundaries
without importing the full ``ralph.agents.invoke`` package (which would
create a circular import through ``ralph.agents.catalog``).
"""

from __future__ import annotations

#: Marker emitted between interactive transport turns; it belongs in the
#: verbatim capture, so parsers and the corruption detector must recognize
#: it instead of reporting a NON_JSONL break for every interactive-transport
#: run.
TURN_BOUNDARY_MARKER: str = "[claude turn boundary]"

__all__ = ["TURN_BOUNDARY_MARKER"]
