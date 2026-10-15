"""Single authority for commit-completion promotion of an integration record.

Every transition to ``phase="integrated"`` must route through
:func:`complete_commit` so the promotion is owned by exactly one place.
Callers that need a bare :class:`IntegrationRecord` unwrap
:attr:`CommitCompletion.record`; the reducer's application seam applies the
typed result without duplicating the promotion.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.pipeline.auto_integrate_record import (
    PROMOTE_TO_INTEGRATED,
    IntegrationRecord,
)


@dataclass(frozen=True)
class CommitCompletion:
    """Typed result of a proved commit-completion promotion."""

    record: IntegrationRecord
    integrated_feature_sha: str | None


def complete_commit(
    record: IntegrationRecord,
    *,
    integrated_feature_sha: str | None,
    **extra: object,
) -> CommitCompletion:
    """Promote ``record`` to the integrated phase via the single transition."""
    # Caller kwargs override nothing the authority owns: phase and
    # integrated_feature_sha are fixed here; any other with_transition kwarg
    # is forwarded.
    kwargs: dict[str, object] = dict(extra)
    kwargs.setdefault("phase", "integrated")
    kwargs.setdefault("integrated_feature_sha", integrated_feature_sha)
    return CommitCompletion(
        record=record.with_transition(PROMOTE_TO_INTEGRATED, **kwargs),
        integrated_feature_sha=integrated_feature_sha,
    )
