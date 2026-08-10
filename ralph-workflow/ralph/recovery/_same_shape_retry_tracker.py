"""Bound the same-shape retry loop (R6 of the ``wt-04-claude-parsing`` spec).

The tracker fingerprints every consecutive ``AgentInactivityTimeoutError``
resume on four signals:

  - ``last_fire_reason`` -- the watchdog's reported reason (e.g.
    ``NO_OUTPUT_DEADLINE``). Two fires with different reasons are NOT
    the same shape.
  - ``fire_diagnostic_signature`` -- the classifier's verdict (e.g.
    ``StuckKind.SILENT_SUBAGENT``) carried into the fire. Two fires
    with the same reason but different classifier verdicts are NOT
    the same shape (a state transition happened in between).
  - ``no_new_artifact_since_prior`` -- True when no new artifact landed
    between the prior and current fire. A new artifact breaks the
    shape even when the other three signals match.
  - ``workspace_change_since_prior`` -- True when the workspace grew
    by at least one new file. A workspace change breaks the shape too;
    a subagent writing files is forward progress.

When the consecutive counter reaches ``limit`` the tracker raises
:class:`SameShapeRetryLoopError` instead of resuming, carrying the
fingerprint, the consecutive count, and the effective ``limit`` as
structured evidence so the operator can diagnose the loop without
re-deriving it from logs.

Why these four signals and not just ``last_fire_reason``:
  - ``last_fire_reason`` alone fires on every other watchdog kill and
    is too coarse -- a genuinely-improving run that hits two idle
    deadlines in a row (e.g. one while exploring, one while summarizing)
    would trip the bound even though the shape changed.
  - The diagnostic signature captures the watchdog's own classifier
    verdict, so a state change that flips the verdict (e.g. from
    ``THINKING`` to ``SILENT_SUBAGENT`` after the activity channel
    went stale) resets the counter. A genuine stall stays stalled and
    keeps the verdict; the counter advances.
  - New artifacts and workspace changes are forward progress even when
    the watchdog kills the run. The two booleans capture those without
    the tracker having to know about the artifact/workspace surface.

The tracker is pure (no I/O, no clock reads, no module-level mutable
state) so it is trivially testable with FakeClock-like callers and
runs under ``make verify`` deterministically. The only stateful field
is the consecutive counter and the prior fingerprint tuple.

Defaults:
  - ``limit`` is :data:`SAME_SHAPE_RETRY_DEFAULT` (3) when no override is
    provided. The default is intentionally small: the four-cycle 25-minute
    burn that motivated this task would have been caught after the
    3rd consecutive identical fire, leaving one full retry of headroom.
  - The configuration surface is single-sourced via
    ``[general] agent_max_same_shape_resumes`` in ``ralph-workflow.toml``,
    routed through ``RecoveryControllerOptions.same_shape_retry_limit``
    so operators can extend the leash without code changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.recovery._same_shape_retry_error import SameShapeRetryLoopError

if TYPE_CHECKING:
    from collections.abc import Mapping


#: Canonical fingerprint tuple type for the tracker. The four-tuple is
#: hashable so it can be stored as a ``dict`` key or a set element if a
#: future caller wants to inspect distinct shapes.
RetryFingerprint = tuple[str, str, bool, bool]


@dataclass(frozen=True)
class SameShapeRetryTracker:
    """Bound consecutive identical-fire resumes at the configured ceiling.

    The tracker is intentionally stateless beyond the prior fingerprint
    and the consecutive counter; construction is cheap and the tracker
    can be re-built on every recovery-controller construction without
    ceremony. Callers (the recovery controller) invoke
    :meth:`record_fire` once per ``AgentInactivityTimeoutError`` fire
    and catch :class:`SameShapeRetryLoopError` to terminate the
    loop instead of resuming.
    """

    limit: int = 3

    def __post_init__(self) -> None:
        if self.limit < 1:
            msg = (
                f"SameShapeRetryTracker.limit must be >= 1; got {self.limit}"
                " (a bound of 0 disables R6 entirely, which would convert an"
                " infinite loop into a fast, quiet failure of a healthy agent)"
            )
            raise ValueError(msg)

    @staticmethod
    def _coerce_signal(value: object) -> str:
        """Normalize a ``fire_reason`` / ``diagnostic_signature`` to a string.

        Accepts ``str`` (returned verbatim), ``Enum`` (returns
        ``value.value``), and any other object (returns ``str(value)``).
        The coercion is permissive so a future caller can pass an enum
        or a wrapped string without first normalizing, but the result
        is always a stable hashable string so fingerprint equality is
        well-defined.
        """
        if isinstance(value, str):
            return value
        # The ``value`` attribute is the canonical enum-source signal
        # (e.g. ``Enum`` subclasses). Use ``getattr`` with the default
        # so non-enum callers fall through to ``str(value)`` below.
        value_attr: str | None = None
        candidate_attr: object = getattr(value, "value", None)
        if isinstance(candidate_attr, str):
            value_attr = candidate_attr
        if value_attr is not None:
            return value_attr
        return str(value)

    def record_fire(
        self,
        *,
        fire_reason: str,
        diagnostic_signature: str,
        no_new_artifact_since_prior: bool,
        workspace_change_since_prior: bool,
        prior_fingerprint: RetryFingerprint | None = None,
        prior_consecutive: int = 0,
    ) -> tuple[RetryFingerprint, int]:
        """Advance the tracker for one new fire and return the next fingerprint/count.

        Args:
            fire_reason: The watchdog's reported reason (e.g.
                ``WatchdogFireReason.NO_OUTPUT_DEADLINE.value``).
            diagnostic_signature: The classifier's verdict for this fire
                (e.g. ``StuckKind.SILENT_SUBAGENT.value``).
            no_new_artifact_since_prior: True when no new artifact
                landed between the prior and current fire.
            workspace_change_since_prior: True when the workspace did
                NOT gain any new files between the prior and current fire.
            prior_fingerprint: The fingerprint recorded on the prior
                fire. ``None`` for the first fire in a session.
            prior_consecutive: The consecutive count carried into this
                fire from the prior call. ``0`` for the first fire.

        Returns:
            A ``(fingerprint, consecutive)`` tuple the caller records in
            its own state so the NEXT fire can use them as the
            ``prior_*`` arguments. The fingerprint is the new
            four-tuple; the consecutive is the advanced counter (or
            ``1`` when this fire did not match the prior shape).

        Raises:
            SameShapeRetryLoopError: When the consecutive count after
                advancing equals the configured ``limit``. The exception
                carries the fingerprint, the consecutive count, and the
                effective limit as structured evidence.
        """
        current: RetryFingerprint = (
            self._coerce_signal(fire_reason),
            self._coerce_signal(diagnostic_signature),
            bool(no_new_artifact_since_prior),
            bool(workspace_change_since_prior),
        )
        if prior_fingerprint is not None and current == prior_fingerprint:
            consecutive = prior_consecutive + 1
        else:
            consecutive = 1
        if consecutive >= self.limit:
            raise SameShapeRetryLoopError(
                fingerprint=current,
                consecutive=consecutive,
                limit=self.limit,
            )
        return current, consecutive


def fingerprint_from_mapping(
    mapping: Mapping[str, object],
) -> RetryFingerprint:
    """Build a tracker fingerprint from a dict-like mapping.

    Convenience helper for callers that already hold the four signals
    in a ``dict`` (e.g. the recovery controller's per-failure state).
    The helper is permissive on types -- any non-string values are
    stringified via ``str()`` so a caller does not have to normalize
    before invoking the tracker -- but the booleans must already be
    bools because there is no defensible default for "no progress".
    """
    try:
        fire_reason: object = mapping["fire_reason"]
        diagnostic_signature: object = mapping["diagnostic_signature"]
        no_new_artifact: object = mapping["no_new_artifact_since_prior"]
        workspace_change: object = mapping["workspace_change_since_prior"]
    except KeyError as exc:
        msg = (
            f"fingerprint_from_mapping missing required key {str(exc)!r}; "
            "expected keys: fire_reason, diagnostic_signature,"
            " no_new_artifact_since_prior, workspace_change_since_prior"
        )
        raise KeyError(msg) from exc
    return (
        str(fire_reason),
        str(diagnostic_signature),
        bool(no_new_artifact),
        bool(workspace_change),
    )


__all__ = [
    "RetryFingerprint",
    "SameShapeRetryLoopError",
    "SameShapeRetryTracker",
    "fingerprint_from_mapping",
]
