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

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline._integration_action import (
    RebaseOutcomeKind,
)

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord


_VALID_COLLECTOR_EVIDENCE_EXACT_KEYS: frozenset[str] = frozenset(
    {
        "head",
        "worktree_path",
        "ownership_fingerprint",
        "unstaged_work",
        "commit_controls",
        "merge_object_resolvable",
        "rebase_object_resolvable",
    }
)
_VALID_COLLECTOR_EVIDENCE_PREFIXES: tuple[str, ...] = (
    "index_scope:",
    "rebase_state_files:",
    "merge_state_files:",
    "unstaged_work:",
    "commit_controls:",
)


def _is_valid_collector_evidence_key(key: str) -> bool:
    """Return True if the key is recognized and produced by the current collector."""
    if key in _VALID_COLLECTOR_EVIDENCE_EXACT_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in _VALID_COLLECTOR_EVIDENCE_PREFIXES)


def _parse_raw_evidence_pairs(raw_evidence: object) -> tuple[tuple[str, str], ...] | None:
    """Extract (key, value) pairs from list, tuple, or dict raw evidence."""
    if isinstance(raw_evidence, dict):
        raw_evidence = list(raw_evidence.items())
    if not isinstance(raw_evidence, (list, tuple)):
        return None
    pairs: list[tuple[str, str]] = []
    for item in raw_evidence:
        if isinstance(item, (list, tuple)):
            match item:
                case [k, v] if isinstance(k, str) and isinstance(v, str):
                    pairs.append((k, v))
        elif isinstance(item, dict):
            k = item.get("key") or item.get("name")
            v = item.get("value") or item.get("content")
            if isinstance(k, str) and isinstance(v, str):
                pairs.append((k, v))
    return tuple(pairs)


def _coerce_payload_failure_cause(data_raw: dict[str, object]) -> dict[str, object]:
    """Ensure last_failure_cause is populated on the payload."""
    if "last_failure_cause" in data_raw and data_raw.get("last_failure_cause") is not None:
        return data_raw
    last_reason = str(data_raw.get("last_reason") or "")
    repair_err = str(data_raw.get("repair_last_error") or "")
    if last_reason == "resolution_agent_failure" or repair_err == "resolution_agent_failure":
        return {**data_raw, "last_failure_cause": ResolutionFailureCause.AGENT_FAILURE}
    return {**data_raw, "last_failure_cause": ResolutionFailureCause.NONE}


#: Default load-time clamp target for diagnostic repair attempts.
#: Coerces loaded values >= cap to cap - 1. Mirrored here to avoid import cycles.
_LOAD_TIME_DIAGNOSTIC_COUNTER_CAP: int = 5

#: Prefix of the typed "diagnostic budget spent" marker
#: (:data:`ralph.pipeline.auto_integrate_transition.DIAGNOSTIC_BUDGET_SPENT_REASON`),
#: mirrored here to avoid import cycles. A payload carrying the marker
#: is a FRESH typed outcome (not a stuck legacy counter), so its counter
#: is preserved at the cap: the next diagnostic seam reads ``>= cap``
#: and escalates straight into the autonomous fallback ladder without
#: spending another agent call.
_LOAD_TIME_BUDGET_SPENT_PREFIX: str = "diagnostic repair budget spent"


def _clamp_loaded_attempt_counters(data_raw: dict[str, object]) -> dict[str, object]:
    """Clamp pre-existing over-cap attempt counters on a loaded payload.

    Inclusive ``>= cap`` check means clamping to ``cap - 1`` allows automated
    recovery on next run. Records written at or above cap (e.g. 200/541
    from stuck records) are clamped to cap - 1 so the diagnostic check
    evaluates to False and the record proceeds to automated recovery on
    next run. The clamp does NOT touch a payload that carries the typed
    "diagnostic budget spent" marker: that is the durable signal of a
    spent budget, and preserving the counter at the cap routes the next
    seam to the fallback ladder instead of re-spending the rotation.
    """
    cap_reason = data_raw.get("repair_cap_reason")
    if isinstance(cap_reason, str) and cap_reason.startswith(_LOAD_TIME_BUDGET_SPENT_PREFIX):
        return data_raw
    raw = data_raw.get("merge_commit_repair_attempts", 0)
    loaded = _safe_int_from_raw(raw)
    if loaded >= _LOAD_TIME_DIAGNOSTIC_COUNTER_CAP:
        return {
            **data_raw,
            "merge_commit_repair_attempts": _LOAD_TIME_DIAGNOSTIC_COUNTER_CAP - 1,
        }
    return data_raw


def _safe_int_from_raw(raw: object) -> int:
    """Best-effort coercion of an on-disk counter to ``int``."""
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return 0
    return _safe_int_from_float(raw) if isinstance(raw, float) else 0


def _safe_int_from_float(raw: float) -> int:
    """Coerce a float to ``int`` with explicit overflow/value handling."""
    try:
        return int(raw)
    except (ValueError, OverflowError):
        return 0


def _clear_legacy_ownership_and_intervention(
    data_raw: dict[str, object],
) -> dict[str, object]:
    """Clear legacy formats: evidence to (), ownership to None, intervention to False.

    Used by :func:`_migrate_payload_evidence` for legacy formats
    (such as repository-global evidence or unparseable lease ownership)
    so the post-migration record is in a clean state: ownership is None,
    owner PID/boot-id are cleared, evidence is an empty tuple, and any
    persisted intervention flag or stale repair-exhausted reason is cleared.
    """
    result = dict(data_raw)
    result["diagnostic_evidence"] = ()
    result["diagnostic_ownership"] = None
    result["diagnostic_owner_pid"] = None
    result["diagnostic_owner_boot_id"] = None
    result["diagnostic_intervention_required"] = False
    last_outcome = result.get("last_outcome")
    if last_outcome in (
        RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
        RebaseOutcomeKind.INTERVENTION_REQUIRED,
        "diagnostic_intervention_required",
        "intervention_required",
    ):
        result["last_outcome"] = None
    cap_reason = result.get("repair_cap_reason")
    if isinstance(cap_reason, str) and (
        "exhausted" in cap_reason.lower() or "operator intervention required" in cap_reason.lower()
    ):
        result["repair_cap_reason"] = None
    return result


def current_boot_id() -> str | None:
    """Return the system boot ID if readable (e.g. on Linux), else None."""
    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    try:
        # filesystem-read-ok: read host kernel boot ID from /proc; not a workspace path
        if boot_id_path.exists():
            # filesystem-read-ok: read host kernel boot ID from /proc; not a workspace path
            return boot_id_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return None


def _pid_is_alive(pid: int) -> bool:
    """True when the OS reports the PID is alive (signal 0 succeeded)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def is_owner_dead(record: IntegrationRecord) -> bool:
    """Return True if the record carries owner identity and that owner is gone."""
    if record.diagnostic_owner_boot_id is not None:
        boot_id = current_boot_id()
        if boot_id is not None and record.diagnostic_owner_boot_id != boot_id:
            return True
    return bool(
        record.diagnostic_owner_pid is not None and not _pid_is_alive(record.diagnostic_owner_pid)
    )


def parse_lease(ownership: str | None) -> tuple[str | None, float | None]:
    """Parse a ``<fingerprint>:<held_at_unix>`` lease string.

    Returns ``(fingerprint, held_at)`` for a well-formed lease, or
    ``(None, None)`` for ``None``, an empty string, any value without
    a single ``:`` separator (the legacy single-string fingerprint
    form), an empty fingerprint or held_at, or a lease whose
    ``held_at`` is not numeric. The diagnostic path interprets the
    ``(None, None)`` outcome (missing, legacy, or malformed lease)
    as returning an ``UNKNOWN`` verdict, which fails open to
    auto-release so state can be re-derived from Git.

    An expired lease (parseable lease where
    ``time.time() - held_at > EVIDENCE_LEASE_SECONDS``) likewise
    returns ``UNKNOWN`` and fails open to auto-release. Only a valid,
    unexpired lease whose fingerprint differs from the record's current
    identity yields ``CHANGED``, preserving the record on disk.
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
    """Re-stamp a record whose held lease's fingerprint does not match or owner is dead.

    Returns the cleared record when the lease parses to a
    fingerprint that no longer matches ``identity_fingerprint``,
    or when the recorded diagnostic owner process/boot is dead;
    otherwise returns ``record`` unchanged.
    """
    from ralph.pipeline.operation_evidence import identity_fingerprint

    should_clear = False
    raw_ownership = record.diagnostic_ownership
    if not isinstance(raw_ownership, str) or not raw_ownership:
        should_clear = is_owner_dead(record)
    else:
        parsed_fingerprint, _ = parse_lease(raw_ownership)
        if (
            parsed_fingerprint is not None and parsed_fingerprint != identity_fingerprint(record)
        ) or is_owner_dead(record):
            should_clear = True

    if should_clear:
        cleared = _clear_legacy_ownership_and_intervention(with_cause)
        try:
            from ralph.pipeline.auto_integrate_record import IntegrationRecord as _Rec

            return _Rec.model_validate(cleared)
        except Exception:
            return record
    return record
