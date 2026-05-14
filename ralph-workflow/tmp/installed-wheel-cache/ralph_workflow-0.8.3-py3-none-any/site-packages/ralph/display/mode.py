"""Terminal mode detection constants for Ralph's transcript output.

Defines the column-width thresholds used to select between narrow, medium, and
wide rendering modes for transcript and display output:

- ``NARROW_THRESHOLD`` (60 columns) - below this the display switches to a
  compact single-column layout with abbreviated labels.
- ``MEDIUM_THRESHOLD`` (100 columns) - between the two thresholds a balanced
  layout is used; above this the full wide layout is used.

These constants are read by ``ralph.display`` components that adapt their
formatting based on the current terminal width.
"""

from __future__ import annotations

NARROW_THRESHOLD: int = 60
MEDIUM_THRESHOLD: int = 100

__all__ = ["MEDIUM_THRESHOLD", "NARROW_THRESHOLD"]
