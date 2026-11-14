from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline._integration_relation import IntegrationRelation
    from ralph.pipeline._integration_work_shape import IntegrationWorkShape

__all__ = ["ObservedIntegration"]


@dataclass(frozen=True, slots=True)
class ObservedIntegration:
    relation: IntegrationRelation
    work_shape: IntegrationWorkShape
