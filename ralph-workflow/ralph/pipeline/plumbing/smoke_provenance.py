from __future__ import annotations

from enum import IntEnum

__all__ = ["Provenance"]


class Provenance(IntEnum):
    ABSENT = 0
    HOST_SYNTHESIZED = 1
    WORKSPACE_EFFECT = 2
    TRANSCRIPT = 3
    WIRE = 4
