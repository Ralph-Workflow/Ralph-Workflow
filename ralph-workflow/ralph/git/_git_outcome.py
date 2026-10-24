"""One typed git outcome: its closed kind and optional cause.

:class:`GitOutcome` pairs the closed outcome kind
(:class:`ralph.git._git_outcome_kind.GitOutcomeKind`) with the cause that
produced it. The pair is a value object, so two outcomes with the same kind
and cause compare equal — the property every outcome assertion relies on.

The type lives in its own module so that every module in the git layer
defines at most ONE public top-level class (the repo-structure audit
enforces that rule), while :mod:`ralph.git.git_run_result` keeps
re-exporting the name for every caller that already imports it from there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from ralph.git._git_outcome_kind import GitOutcomeKind

__all__ = ["GitOutcome"]


class GitOutcome(NamedTuple):
    """One typed git outcome: its closed kind and optional cause."""

    kind: GitOutcomeKind
    cause: str | None = None
