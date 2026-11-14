from __future__ import annotations

from ralph.pipeline.resolution_outcome_config_failure import ConfigFailure
from ralph.pipeline.resolution_outcome_exhausted import Exhausted
from ralph.pipeline.resolution_outcome_needs_operator import NeedsOperator
from ralph.pipeline.resolution_outcome_resolved import Resolved
from ralph.pipeline.resolution_outcome_retry_later import RetryLater

__all__ = ["ResolutionOutcomeType"]

type ResolutionOutcomeType = Resolved | NeedsOperator | RetryLater | Exhausted | ConfigFailure
