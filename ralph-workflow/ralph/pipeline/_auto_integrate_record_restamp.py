"""Load-time lease re-stamp for :class:`IntegrationRecord` (GAP-5 / AC-4).

The held diagnostic lease stores only ``<fingerprint>:<held_at>``.
When the record is reloaded with a fingerprint that no longer
matches the freshly-computed ``identity_fingerprint``, the loader
cannot distinguish between ``"schema changed"`` (the diagnostic
path owns the migration) and ``"external party rewrote the
record"`` (the diagnostic path must escalate). The fail-open
rule therefore re-stamps the record to a clean state on
fingerprint mismatch: held evidence ``()``, ownership ``None``,
intervention flag ``False``, repair-cap reason cleared. The
diagnostic path then re-derives state from Git on the next
seam, exactly as it does for the legacy ``for-each-ref`` /
``ls-files`` shape.

The runtime verdict path
(:func:`ralph.pipeline._integration_diagnostic.diagnostic_evidence_verdict`)
still returns ``CHANGED`` for a live fingerprint mismatch on the
running seam -- the load-time re-stamp is the fail-open migration
path that turns a stale lease into a fresh ``UNCHANGED`` verdict.
"""

from __future__ import annotations

from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    _clear_legacy_ownership_and_intervention,
)


def parse_lease(ownership: str | None) -> tuple[str | None, float | None]:
    """Parse a ``<fingerprint>:<held_at_unix>`` lease string.

    Returns ``(fingerprint, held_at)`` for a well-formed lease, or
    ``(None, None)`` for ``None``, an empty string, any value without
    a single ``:`` separator (the legacy single-string fingerprint
    form), an empty fingerprint or held_at, or a lease whose
    ``held_at`` is not numeric. The diagnostic path interprets the
    ``(None, None)`` outcome as either:

    * a **legacy migrated record** (``diagnostic_evidence`` non-empty
      and ``diagnostic_ownership`` empty) -- the verdict is
      ``CHANGED`` and the migrated observation is preserved on disk
      because the ``CHANGED`` branch returns a skip verdict and
      never mutates the record.
    * a **non-empty unparseable ownership** (the lease is no longer
      a valid durable identity) -- the verdict is ``CHANGED`` and
      the caller escalates for operator review.

    The expired-lease branch is a separate code path (lease parseable
    and ``time.time() - held_at > EVIDENCE_LEASE_SECONDS``) that
    returns ``UNKNOWN`` and fails open to auto-release.
    """
    if not isinstance(ownership, str) or not ownership:
        return None, None
    if ":" not in ownership:
        return None, None
    fingerprint, _, held_at_str = ownership.partition(":")
    if not fingerprint or not held_at_str:
        return None, None
    try:
        held_at = float(held_at_str)
    except (TypeError, ValueError):
        return None, None
    return fingerprint, held_at


def restamp_fingerprint_mismatch(
    record: IntegrationRecord,
    with_cause: dict[str, object],
) -> IntegrationRecord:
    """Re-stamp a record whose held lease's fingerprint does not match.

    Returns the cleared record when the lease parses to a
    fingerprint that no longer matches ``identity_fingerprint``;
    otherwise returns ``record`` unchanged.
    """
    from ralph.pipeline.operation_evidence import identity_fingerprint

    raw_ownership = record.diagnostic_ownership
    if not isinstance(raw_ownership, str) or not raw_ownership:
        return record
    parsed_fingerprint, _ = parse_lease(raw_ownership)
    if parsed_fingerprint is None:
        return record
    if parsed_fingerprint == identity_fingerprint(record):
        return record
    cleared = _clear_legacy_ownership_and_intervention(with_cause)
    try:
        return IntegrationRecord.model_validate(cleared)
    except Exception:
        return record
