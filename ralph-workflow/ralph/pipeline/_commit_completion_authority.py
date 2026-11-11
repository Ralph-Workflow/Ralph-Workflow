
from __future__ import annotations

from dataclasses import dataclass

from ralph.pipeline.auto_integrate_record import (
    PROMOTE_TO_INTEGRATED,
    IntegrationRecord,
)


@dataclass(frozen=True)
class CommitCompletion:

    record: IntegrationRecord
    integrated_feature_sha: str | None


def complete_commit(
    record: IntegrationRecord,
    *,
    integrated_feature_sha: str | None,
    **extra: object,
) -> CommitCompletion:
    if not integrated_feature_sha or not integrated_feature_sha.strip():
        raise ValueError(
            "cannot promote integration record to integrated without a valid integrated_feature_sha"
        )
    kwargs: dict[str, object] = dict(extra)
    kwargs.setdefault("phase", "integrated")
    kwargs.setdefault("integrated_feature_sha", integrated_feature_sha.strip())
    return CommitCompletion(
        record=record.with_transition(PROMOTE_TO_INTEGRATED, **kwargs),
        integrated_feature_sha=integrated_feature_sha.strip(),
    )
