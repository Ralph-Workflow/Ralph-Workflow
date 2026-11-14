from __future__ import annotations

import contextlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Final, Protocol, cast, runtime_checkable
from uuid import uuid4

from ralph.pipeline._integration_ladder_evidence import _LADDER_ACTION_VALUES
from ralph.pipeline.integration_decision import (
    LADDER_ORDER,
    MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION,
    EscalationRung,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "ATTEMPT_LEDGER_FILENAME",
    "CURRENT_SCHEMA_VERSION",
    "GLOBAL_ACTION_BOUND",
    "TOMBSTONE_FILENAME",
    "AttemptLedger",
    "AttemptLedgerState",
    "LedgerLoadStatus",
    "LedgerRebuildFacts",
    "attempt_ledger_path",
    "rebase_resolution_signature",
    "rebuild_state_from_git_facts",
    "repository_ledger_path",
    "shared_budget_dir",
    "shared_rung_result_key",
    "tombstone_path",
]


ATTEMPT_LEDGER_FILENAME = "integration_attempt_ledger.json"

TOMBSTONE_FILENAME = "integration_attempt_tombstone.json"

SHARED_BUDGET_DIRNAME = "ralph"

CURRENT_SCHEMA_VERSION = 1

GLOBAL_ACTION_BOUND: Final[int] = 2

REPEAT_CAP = 1024

_MAX_SATURATED_REPEATS: Final[int] = (1 << 31) - 1

_REPEAT_KEY_TUPLE_LEN = 2

_LADDER_EVIDENCE_CAP: Final[int] = 64
_SHARED_RUNG_RESULTS_CAP: Final[int] = 256

_TOMBSTONE_SCHEMA_VERSION: Final[int] = 1

_LOCK_SUFFIX = ".lock"
_QUARANTINE_SUFFIX = ".quarantine"

_LOCK_TIMEOUT_SECONDS: Final[float] = 5.0
_LOCK_POLL_SECONDS: Final[float] = 0.005

_RUNG_VALUES: Final[frozenset[str]] = frozenset(rung.value for rung in LADDER_ORDER)


@dataclass(frozen=True)
class LedgerRebuildFacts:
    proven_rung: EscalationRung | None = None

    proven_repeats: tuple[tuple[str, str, int], ...] = ()


class LedgerLoadStatus(Enum):
    FRESH = "fresh"

    RESTORED = "restored"

    UNKNOWABLE = "unknowable"


@dataclass(frozen=True)
class AttemptLedgerState:
    rung: EscalationRung = EscalationRung.REPAIR
    repeat_counts: OrderedDict[tuple[str, str], int] = field(
        default_factory=OrderedDict,
    )
    unknown_attempts: int = 0
    unknown_budget: int = 3
    repeat_threshold: int = 2
    version: int = CURRENT_SCHEMA_VERSION
    saturated_repeats: int = 0

    global_actions_charged: int = 0

    ladder_evidence: dict[str, str] = field(
        default_factory=dict,
    )

    shared_rung_results: dict[str, str] = field(
        default_factory=dict,
    )

    def __post_init__(self) -> None:
        if self.unknown_attempts < 0:
            raise ValueError("unknown_attempts must be >= 0")
        if self.unknown_budget < 1:
            raise ValueError("unknown_budget must be >= 1")
        if self.repeat_threshold < 1:
            raise ValueError("repeat_threshold must be >= 1")
        if self.version < 1:
            raise ValueError("version must be >= 1")
        if self.saturated_repeats < 0:
            raise ValueError("saturated_repeats must be >= 0")
        if self.global_actions_charged < 0:
            raise ValueError("global_actions_charged must be >= 0")


def attempt_ledger_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".agent" / ATTEMPT_LEDGER_FILENAME


def rebase_resolution_signature(
    pre_feature_sha: str | None,
    pre_target_sha: str | None,
    stop_sha: str | None,
) -> str:
    return (
        f"rebase-resolution|feature={pre_feature_sha or 'unknown'}"
        f"|target={pre_target_sha or 'unknown'}|stop={stop_sha or 'unknown'}"
    )


def rebuild_state_from_git_facts(
    facts: LedgerRebuildFacts,
    *,
    unknown_budget: int = 3,
    repeat_threshold: int = 2,
) -> AttemptLedgerState:
    repeats: OrderedDict[tuple[str, str], int] = OrderedDict()
    for signature, action, count in facts.proven_repeats:
        if count < 1:
            continue
        repeats[(signature, action)] = count
    repeats, saturated = AttemptLedger._clamp_repeats(repeats, saturated=0)
    rung = facts.proven_rung if facts.proven_rung is not None else EscalationRung.REPAIR
    return AttemptLedgerState(
        rung=rung,
        repeat_counts=repeats,
        unknown_attempts=0,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
        saturated_repeats=saturated,
    )


def shared_budget_dir(common_dir: Path) -> Path:
    return Path(common_dir) / SHARED_BUDGET_DIRNAME


def repository_ledger_path(common_dir: Path) -> Path:
    return shared_budget_dir(common_dir) / ATTEMPT_LEDGER_FILENAME


def tombstone_path(ledger_path: Path) -> Path:
    return Path(ledger_path).with_name(TOMBSTONE_FILENAME)


def shared_rung_result_key(
    target_tip: str,
    vendor_pointer: str | None,
    conflicting_paths: tuple[str, ...],
) -> str:
    paths = "\0".join(sorted(set(conflicting_paths)))
    identity = f"{target_tip}\0{vendor_pointer or ''}\0{paths}".encode()
    return f"rung-result:{sha256(identity).hexdigest()}"


def _saturating_add(total: int, increment: int) -> int:
    return min(total + increment, _MAX_SATURATED_REPEATS)


def _advance_rung(
    current: EscalationRung,
    *,
    new_count: int,
    threshold: int,
    signature_attempts: int,
) -> EscalationRung:
    index = LADDER_ORDER.index(current)
    if index >= len(LADDER_ORDER) - 1:
        return current
    if new_count < threshold or signature_attempts < MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION:
        return current
    return LADDER_ORDER[index + 1]


def _further_rung(left: EscalationRung, right: EscalationRung) -> EscalationRung:
    if LADDER_ORDER.index(left) >= LADDER_ORDER.index(right):
        return left
    return right


@runtime_checkable
class _HasFileno(Protocol):
    def fileno(self) -> int: ...


def _take_lock(handle: object, deadline: float) -> bool:
    try:
        import fcntl
    except ImportError:
        return True
    if not isinstance(handle, _HasFileno):
        return True
    wait_event = Event()
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            wait_event.wait(_LOCK_POLL_SECONDS)


def _release_lock(handle: object) -> None:
    try:
        import fcntl
    except ImportError:
        return
    if not isinstance(handle, _HasFileno):
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return


class AttemptLedger:
    def __init__(
        self,
        workspace_root: Path,
        *,
        ledger_path: Path | None = None,
        unknown_budget: int = 3,
        repeat_threshold: int = 2,
        common_dir: Path | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._common_dir: Path | None = None if common_dir is None else Path(common_dir)
        if ledger_path is None:
            if self._common_dir is None:
                self._ledger_path: Path = attempt_ledger_path(self._workspace_root)
            else:
                self._ledger_path = repository_ledger_path(self._common_dir)
        else:
            self._ledger_path = Path(ledger_path)
        if unknown_budget < 1:
            raise ValueError("unknown_budget must be >= 1")
        if repeat_threshold < 1:
            raise ValueError("repeat_threshold must be >= 1")
        self._default_unknown_budget = unknown_budget
        self._default_repeat_threshold = repeat_threshold
        self._global_charged = 0
        self._saturated_repeats = 0
        self._unknowable = False
        self._forced_pending = False

    @property
    def ledger_path(self) -> Path:
        return self._ledger_path

    @property
    def tombstone_path(self) -> Path:
        return tombstone_path(self._ledger_path)

    @property
    def is_repository_wide(self) -> bool:
        return self._common_dir is not None

    @property
    def forced_pending(self) -> bool:
        return self._forced_pending

    def _default_state(self) -> AttemptLedgerState:
        return AttemptLedgerState(
            rung=EscalationRung.REPAIR,
            repeat_counts=OrderedDict(),
            unknown_attempts=0,
            unknown_budget=self._default_unknown_budget,
            repeat_threshold=self._default_repeat_threshold,
            ladder_evidence={},
        )

    @staticmethod
    def _exhausted_state(
        state: AttemptLedgerState, *, global_actions_charged: int | None = None
    ) -> AttemptLedgerState:
        charged = (
            state.global_actions_charged
            if global_actions_charged is None
            else max(state.global_actions_charged, global_actions_charged)
        )
        return AttemptLedgerState(
            rung=EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING,
            repeat_counts=OrderedDict(),
            unknown_attempts=state.unknown_budget,
            unknown_budget=state.unknown_budget,
            repeat_threshold=state.repeat_threshold,
            saturated_repeats=state.saturated_repeats,
            global_actions_charged=charged,
            ladder_evidence=dict(state.ladder_evidence),
            shared_rung_results=dict(state.shared_rung_results),
        )

    @staticmethod
    def _payload_status(payload: Mapping[str, object]) -> LedgerLoadStatus:
        version = payload.get("version")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != CURRENT_SCHEMA_VERSION
        ):
            return LedgerLoadStatus.UNKNOWABLE
        rung_value = payload.get("rung")
        if rung_value is not None and (
            not isinstance(rung_value, str) or rung_value not in _RUNG_VALUES
        ):
            return LedgerLoadStatus.UNKNOWABLE
        if not isinstance(payload.get("repeat_counts", []), list):
            return LedgerLoadStatus.UNKNOWABLE
        return LedgerLoadStatus.RESTORED

    def _load(self) -> tuple[AttemptLedgerState, LedgerLoadStatus]:
        path = self._ledger_path
        try:
            raw_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._default_state(), LedgerLoadStatus.FRESH
        except (OSError, UnicodeError):
            return self._default_state(), LedgerLoadStatus.UNKNOWABLE

        try:
            payload = cast("object", json.loads(raw_text))
        except (ValueError, RecursionError):
            return self._default_state(), LedgerLoadStatus.UNKNOWABLE

        if not isinstance(payload, dict):
            return self._default_state(), LedgerLoadStatus.UNKNOWABLE

        status = self._payload_status(payload)
        state = self._migrate_payload(payload)
        self._saturated_repeats = max(self._saturated_repeats, state.saturated_repeats)
        self._global_charged = max(self._global_charged, state.global_actions_charged)
        return state, status

    def read(self) -> AttemptLedgerState:
        state, _status = self._load()
        return state

    def with_attempt(
        self,
        signature: str,
        action: str,
        *,
        is_unknown: bool,
    ) -> AttemptLedgerState:
        return self.charge(signature, action, is_unknown=is_unknown)

    def charge_ladder_attempt(self, signature: str, action: str) -> AttemptLedgerState:
        if action not in _LADDER_ACTION_VALUES:
            raise ValueError(f"not a ladder action: {action!r}")
        return self.charge(signature, action, is_unknown=False)

    def charge(
        self,
        signature: str,
        action: str,
        *,
        is_unknown: bool,
    ) -> AttemptLedgerState:
        with self._locked() as acquired:
            state, status = self._load()
            charged = self._global_charged_total()
            if self._forced_pending or charged >= GLOBAL_ACTION_BOUND:
                self._forced_pending = True
                return self._exhausted_state(state)
            if status is LedgerLoadStatus.FRESH and charged > 0:
                status = LedgerLoadStatus.UNKNOWABLE
            if self._unknowable or status is LedgerLoadStatus.UNKNOWABLE:
                charged, granted = self._spend_global_allowance(charged)
                if not granted:
                    self._forced_pending = True
                    return self._exhausted_state(state)
                self._quarantine_ledger()
                state = self._exhausted_state(state, global_actions_charged=charged)
            bumped = self._apply_charge(state, signature, action, is_unknown=is_unknown)
            if not acquired:
                self._unknowable = True
                return self._on_persist_failure(bumped, state)
            try:
                self._write_state(bumped, state)
            except OSError:
                return self._on_persist_failure(bumped, state)
            return bumped

    def _apply_charge(
        self,
        state: AttemptLedgerState,
        signature: str,
        action: str,
        *,
        is_unknown: bool,
    ) -> AttemptLedgerState:
        if is_unknown:
            return AttemptLedgerState(
                rung=state.rung,
                repeat_counts=OrderedDict(state.repeat_counts),
                unknown_attempts=state.unknown_attempts + 1,
                unknown_budget=state.unknown_budget,
                repeat_threshold=state.repeat_threshold,
                saturated_repeats=state.saturated_repeats,
                global_actions_charged=state.global_actions_charged,
                ladder_evidence=dict(state.ladder_evidence),
                shared_rung_results=dict(state.shared_rung_results),
            )
        key = (signature, action)
        updated: OrderedDict[tuple[str, str], int] = OrderedDict(state.repeat_counts)
        new_count = updated.get(key, 0) + 1
        updated[key] = new_count
        updated, saturated = self._clamp_repeats(updated, saturated=state.saturated_repeats)
        signature_attempts = sum(
            count
            for (key_signature, _action), count in updated.items()
            if key_signature == signature
        )
        advanced_rung = _advance_rung(
            state.rung,
            new_count=new_count,
            threshold=state.repeat_threshold,
            signature_attempts=signature_attempts,
        )
        return AttemptLedgerState(
            rung=advanced_rung,
            repeat_counts=updated,
            unknown_attempts=0,
            unknown_budget=state.unknown_budget,
            repeat_threshold=state.repeat_threshold,
            saturated_repeats=saturated,
            global_actions_charged=state.global_actions_charged,
            ladder_evidence=dict(state.ladder_evidence),
            shared_rung_results=dict(state.shared_rung_results),
        )

    def _on_persist_failure(
        self, charged: AttemptLedgerState, disk: AttemptLedgerState
    ) -> AttemptLedgerState:
        self._unknowable = True
        charged_total, granted = self._spend_global_allowance(self._global_charged_total())
        if granted:
            return charged
        self._forced_pending = True
        return self._exhausted_state(
            self._merge_monotone(charged, disk),
            global_actions_charged=charged_total,
        )

    def rebuild_from_git_facts(self, facts: LedgerRebuildFacts) -> AttemptLedgerState:
        with self._locked() as acquired:
            if not acquired:
                raise OSError("integration attempt ledger lock unavailable")
            self._quarantine_ledger()
            rebuilt = rebuild_state_from_git_facts(
                facts,
                unknown_budget=self._default_unknown_budget,
                repeat_threshold=self._default_repeat_threshold,
            )
            self._write_state(rebuilt, self._default_state())
            return rebuilt

    def persist(self, state: AttemptLedgerState) -> None:
        with self._locked() as acquired:
            if not acquired:
                raise OSError("integration attempt ledger lock unavailable")
            disk, _status = self._load()
            self._write_state(state, disk)

    def publish_rung_result(self, key: str, value: str) -> AttemptLedgerState:
        if not key:
            raise ValueError("rung result key must not be empty")
        with self._locked() as acquired:
            if not acquired:
                raise OSError("integration attempt ledger lock unavailable")
            state, _status = self._load()
            shared = dict(state.shared_rung_results)
            shared[key] = value
            updated = AttemptLedgerState(
                rung=state.rung,
                repeat_counts=OrderedDict(state.repeat_counts),
                unknown_attempts=state.unknown_attempts,
                unknown_budget=state.unknown_budget,
                repeat_threshold=state.repeat_threshold,
                saturated_repeats=state.saturated_repeats,
                global_actions_charged=state.global_actions_charged,
                ladder_evidence=dict(state.ladder_evidence),
                shared_rung_results=shared,
            )
            self._write_state(updated, state)
            persisted, _status = self._load()
            return persisted

    def read_rung_result(self, key: str) -> str | None:
        return self.read().shared_rung_results.get(key)

    def _write_state(self, state: AttemptLedgerState, disk: AttemptLedgerState) -> None:
        merged = self._merge_monotone(state, disk)
        path = self._ledger_path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, self._serialize(merged))

    @staticmethod
    def _merge_monotone(state: AttemptLedgerState, disk: AttemptLedgerState) -> AttemptLedgerState:
        merged_counts: OrderedDict[tuple[str, str], int] = OrderedDict(disk.repeat_counts)
        for key, count in state.repeat_counts.items():
            merged_counts[key] = max(merged_counts.get(key, 0), count)
        merged_counts, saturated = AttemptLedger._clamp_repeats(
            merged_counts,
            saturated=max(state.saturated_repeats, disk.saturated_repeats),
        )
        merged_evidence = dict(disk.ladder_evidence)
        merged_evidence.update(state.ladder_evidence)
        merged_results = dict(disk.shared_rung_results)
        merged_results.update(state.shared_rung_results)
        return AttemptLedgerState(
            rung=_further_rung(state.rung, disk.rung),
            repeat_counts=merged_counts,
            unknown_attempts=max(state.unknown_attempts, disk.unknown_attempts),
            unknown_budget=state.unknown_budget,
            repeat_threshold=min(state.repeat_threshold, disk.repeat_threshold),
            version=CURRENT_SCHEMA_VERSION,
            saturated_repeats=saturated,
            global_actions_charged=max(state.global_actions_charged, disk.global_actions_charged),
            ladder_evidence=dict(list(merged_evidence.items())[:_LADDER_EVIDENCE_CAP]),
            shared_rung_results=dict(list(merged_results.items())[-_SHARED_RUNG_RESULTS_CAP:]),
        )

    @staticmethod
    def _serialize(state: AttemptLedgerState) -> str:
        repeat_counts_obj: object = [
            [list(key), value] for key, value in state.repeat_counts.items()
        ]
        payload: dict[str, object] = {
            "version": state.version,
            "rung": state.rung.value,
            "unknown_attempts": state.unknown_attempts,
            "unknown_budget": state.unknown_budget,
            "repeat_threshold": state.repeat_threshold,
            "repeat_counts": repeat_counts_obj,
            "saturated_repeats": state.saturated_repeats,
            "global_actions_charged": state.global_actions_charged,
            "ladder_evidence": dict(sorted(state.ladder_evidence.items())),
            "shared_rung_results": dict(sorted(state.shared_rung_results.items())),
        }
        return json.dumps(payload, indent=2, sort_keys=False)

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> None:
        temp_path = path.with_name(f"{path.name}.staging.{os.getpid()}.{uuid4().hex}")
        try:
            with temp_path.open("w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                descriptor = stream.fileno()
                if isinstance(descriptor, int):
                    os.fsync(descriptor)
            temp_path.replace(path)
            try:
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
            except OSError:
                return
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _clamp_repeats(
        repeats: OrderedDict[tuple[str, str], int],
        *,
        cap: int | None = None,
        saturated: int = 0,
    ) -> tuple[OrderedDict[tuple[str, str], int], int]:
        effective_cap = cap if cap is not None else REPEAT_CAP
        while len(repeats) > effective_cap:
            _evicted_key, evicted_count = repeats.popitem(last=False)
            saturated = _saturating_add(saturated, evicted_count)
        return repeats, saturated

    def _quarantine_ledger(self) -> None:
        path = self._ledger_path
        quarantine = path.with_name(path.name + _QUARANTINE_SUFFIX)
        try:
            path.replace(quarantine)
        except OSError:
            return

    def _global_charged_total(self) -> int:
        return min(max(self._tombstone_charged(), self._global_charged), GLOBAL_ACTION_BOUND)

    def _tombstone_charged(self) -> int:
        path = self.tombstone_path
        try:
            raw_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0
        except (OSError, UnicodeError):
            return GLOBAL_ACTION_BOUND
        try:
            payload: object = json.loads(raw_text)
        except (ValueError, RecursionError):
            return GLOBAL_ACTION_BOUND
        if not isinstance(payload, dict):
            return GLOBAL_ACTION_BOUND
        charged = payload.get("global_actions_charged")
        if not isinstance(charged, int) or isinstance(charged, bool) or charged < 0:
            return GLOBAL_ACTION_BOUND
        return min(charged, GLOBAL_ACTION_BOUND)

    def _spend_global_allowance(self, charged: int) -> tuple[int, bool]:
        if charged >= GLOBAL_ACTION_BOUND:
            return charged, False
        charged += 1
        self._global_charged = max(self._global_charged, charged)
        try:
            self._write_tombstone(charged)
        except OSError:
            return charged, False
        return charged, True

    def _write_tombstone(self, charged: int) -> None:
        path = self.tombstone_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tombstone: dict[str, object] = {
            "version": _TOMBSTONE_SCHEMA_VERSION,
            "global_actions_charged": charged,
            "global_action_bound": GLOBAL_ACTION_BOUND,
        }
        payload = json.dumps(
            tombstone,
            indent=2,
            sort_keys=False,
        )
        self._atomic_write(path, payload)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[bool]:
        lock_path = self._ledger_path.with_name(self._ledger_path.name + _LOCK_SUFFIX)
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+", encoding="utf-8")
        except OSError:
            yield False
            return
        acquired = _take_lock(handle, time.monotonic() + _LOCK_TIMEOUT_SECONDS)
        try:
            yield acquired
        finally:
            if acquired:
                _release_lock(handle)
            handle.close()

    def _migrate_payload(self, payload: Mapping[str, object]) -> AttemptLedgerState:
        rung_value = payload.get("rung")
        rung: EscalationRung
        if isinstance(rung_value, str):
            try:
                rung = EscalationRung(rung_value)
            except ValueError:
                rung = EscalationRung.REPAIR
        else:
            rung = EscalationRung.REPAIR

        unknown_attempts_raw = payload.get("unknown_attempts", 0)
        unknown_attempts = (
            int(unknown_attempts_raw)
            if isinstance(unknown_attempts_raw, int) and not isinstance(unknown_attempts_raw, bool)
            else 0
        )
        unknown_attempts = max(0, unknown_attempts)

        unknown_budget_raw = payload.get("unknown_budget", self._default_unknown_budget)
        unknown_budget = (
            int(unknown_budget_raw)
            if isinstance(unknown_budget_raw, int) and not isinstance(unknown_budget_raw, bool)
            else self._default_unknown_budget
        )
        if unknown_budget < 1:
            unknown_budget = self._default_unknown_budget

        repeat_threshold_raw = payload.get("repeat_threshold", self._default_repeat_threshold)
        repeat_threshold = (
            int(repeat_threshold_raw)
            if isinstance(repeat_threshold_raw, int) and not isinstance(repeat_threshold_raw, bool)
            else self._default_repeat_threshold
        )
        if repeat_threshold < 1:
            repeat_threshold = self._default_repeat_threshold

        repeats: OrderedDict[tuple[str, str], int] = OrderedDict()
        raw_repeats = payload.get("repeat_counts")
        if isinstance(raw_repeats, list):
            for entry in raw_repeats:
                key_pair = AttemptLedger._coerce_repeat_entry(entry)
                if key_pair is None:
                    continue
                repeats[key_pair[0]] = key_pair[1]
        repeats, saturated = self._clamp_repeats(repeats, saturated=0)
        saturated = _saturating_add(saturated, _non_negative_int(payload, "saturated_repeats"))

        return AttemptLedgerState(
            rung=rung,
            repeat_counts=repeats,
            unknown_attempts=unknown_attempts,
            unknown_budget=unknown_budget,
            repeat_threshold=repeat_threshold,
            version=CURRENT_SCHEMA_VERSION,
            saturated_repeats=saturated,
            global_actions_charged=_non_negative_int(payload, "global_actions_charged"),
            ladder_evidence=_parse_ladder_evidence(payload.get("ladder_evidence")),
            shared_rung_results=_parse_shared_rung_results(payload.get("shared_rung_results")),
        )

    @staticmethod
    def _coerce_repeat_entry(
        entry: object,
    ) -> tuple[tuple[str, str], int] | None:
        key_len = _REPEAT_KEY_TUPLE_LEN
        if not isinstance(entry, list) or len(entry) != key_len:
            return None
        key_raw, count_raw = entry
        if not isinstance(key_raw, list) or len(key_raw) != key_len:
            return None
        sig_raw, action_raw = key_raw
        if not isinstance(sig_raw, str) or not isinstance(action_raw, str):
            return None
        if not isinstance(count_raw, int) or isinstance(count_raw, bool):
            return None
        if count_raw < 0:
            return None
        return (sig_raw, action_raw), count_raw


def _parse_ladder_evidence(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    evidence: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        evidence[key] = value
        if len(evidence) >= _LADDER_EVIDENCE_CAP:
            break
    return evidence


def _parse_shared_rung_results(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    results: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        results[key] = value
        if len(results) >= _SHARED_RUNG_RESULTS_CAP:
            break
    return results


def _non_negative_int(payload: Mapping[str, object], key: str) -> int:
    raw = payload.get(key, 0)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        return 0
    return min(raw, _MAX_SATURATED_REPEATS)
