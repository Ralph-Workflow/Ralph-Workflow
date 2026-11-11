
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
    if key in _VALID_COLLECTOR_EVIDENCE_EXACT_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in _VALID_COLLECTOR_EVIDENCE_PREFIXES)


def _parse_raw_evidence_pairs(raw_evidence: object) -> tuple[tuple[str, str], ...] | None:
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
    if "last_failure_cause" in data_raw and data_raw.get("last_failure_cause") is not None:
        return data_raw
    last_reason = str(data_raw.get("last_reason") or "")
    repair_err = str(data_raw.get("repair_last_error") or "")
    if last_reason == "resolution_agent_failure" or repair_err == "resolution_agent_failure":
        return {**data_raw, "last_failure_cause": ResolutionFailureCause.AGENT_FAILURE}
    return {**data_raw, "last_failure_cause": ResolutionFailureCause.NONE}


_LOAD_TIME_DIAGNOSTIC_COUNTER_CAP: int = 5

_LOAD_TIME_BUDGET_SPENT_PREFIX: str = "diagnostic repair budget spent"


def _clamp_loaded_attempt_counters(data_raw: dict[str, object]) -> dict[str, object]:
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
    try:
        return int(raw)
    except (ValueError, OverflowError):
        return 0


def _clear_legacy_ownership_and_intervention(
    data_raw: dict[str, object],
) -> dict[str, object]:
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
    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    try:
        if boot_id_path.exists():
            return boot_id_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return None


def _pid_is_alive(pid: int) -> bool:
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
    if record.diagnostic_owner_boot_id is not None:
        boot_id = current_boot_id()
        if boot_id is not None and record.diagnostic_owner_boot_id != boot_id:
            return True
    return bool(
        record.diagnostic_owner_pid is not None and not _pid_is_alive(record.diagnostic_owner_pid)
    )


def parse_lease(ownership: str | None) -> tuple[str | None, float | None]:
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
