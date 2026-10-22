"""Durable, restart-proof, repository-wide attempt ledger for Ralph's auto-integration.

The decision table in :mod:`ralph.pipeline.integration_decision` is pure
and total; the only state it consumes is the ledger. Without durability
that state lives in process memory, and any restart, new session or
record reload wipes the budget — which is exactly what failed every
previous fix (the commit-loop root cause in O1a: a budget that resets
on crash, loads below its cap, or transfers when ownership changes).

This module publishes a single, restart-proof ledger contract:

* :class:`AttemptLedgerState` — frozen dataclass carrying the current
  escalation rung, the per-``(state signature, action)`` repeat counts,
  the unknown-attempt budget, the saturating aggregate that absorbs
  evicted repeat entries, and the monotone global-action charge.
* :class:`AttemptLedger` — the on-disk owner. Every charge is written
  through an exclusive lock plus an atomic write-temp + ``os.replace``,
  and is **charged before** the caller's action runs: a crash between
  the charge and the effect still counts the attempt, so a spent rung
  never resurrects on restart. Reads are lenient: a missing file is a
  first run, and a corrupt, unreadable or foreign-version ledger still
  parses best-effort so it never blocks dispatch.
* **One repository-wide budget.** Passing ``common_dir`` anchors the
  ledger, its lock and its tombstone under the git common directory,
  so every worktree of one repository charges the *same* budget. A
  per-worktree copy would be exactly the "counters clamped back below
  their cap when ownership changes" leak from the criteria.
* **One fixed global action bound.** When prior usage is *unknowable*
  — the ledger was corrupt / unreadable / written by another schema, or
  the charge could not be persisted — the ledger is quarantined and
  replaced by one born at the durable exhausted/pending rung. Each such
  birth spends one unit of :data:`GLOBAL_ACTION_BOUND`, recorded in a
  tombstone beside the ledger. The bound is tiny, monotone and survives
  restarts, so a repeated corrupt-ledger / write-failure / restart
  history is bounded by one fixed global action count and can never
  recreate allowance. If even the tombstone cannot be written the
  process advances straight to the exhausted/pending rung and stays
  there for its lifetime: never skip a charge, never block, never
  restore allowance on restart.
* Storage stays bounded: per-``(signature, action)`` keys live in an
  :class:`collections.OrderedDict` capped at :data:`REPEAT_CAP`.
  Evicted entries are folded into :attr:`AttemptLedgerState.saturated_repeats`
  rather than discarded, so eviction never restores budget. There is no
  clamp-on-load: the durable aggregates only ever increase.

The contract is the public API for the director, the gate, and the
model fault-injection test. The on-disk format is JSON; the schema is
the :class:`AttemptLedgerState` dataclass. New fields are additive and
defaulted, so older payloads migrate forward through
:meth:`AttemptLedger._migrate_payload` without a version bump. A future
incompatible change bumps :attr:`AttemptLedgerState.version`.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, cast, runtime_checkable

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
    "attempt_ledger_path",
    "repository_ledger_path",
    "shared_budget_dir",
    "tombstone_path",
]


#: Default on-disk file name; lives under ``.agent/`` per workspace root.
ATTEMPT_LEDGER_FILENAME = "integration_attempt_ledger.json"

#: Name of the durable tombstone that owns the fixed global action bound.
TOMBSTONE_FILENAME = "integration_attempt_tombstone.json"

#: Directory segment holding the repository-wide budget under the git
#: common directory, so every worktree of one repository shares it.
SHARED_BUDGET_DIRNAME = "ralph"

#: Current schema version. The shape only ever grows by *defaulted*
#: fields, so this constant stays put; a future incompatible change
#: bumps it and adds a branch to :meth:`AttemptLedger._migrate_payload`.
CURRENT_SCHEMA_VERSION = 1

#: Fixed, tiny bound on how many times the boundary may be *re-born* at
#: the exhausted/pending rung because prior ledger usage was unknowable
#: (corrupt / unreadable / foreign-schema ledger, or a charge that could
#: not be persisted). It is deliberately tiny: the exhausted/pending rung
#: keeps the pipeline moving, and no environment or state history may
#: hand out a fresh allowance. The charge is persisted in
#: :data:`TOMBSTONE_FILENAME` beside the ledger and is never reset, so
#: repeated corruption / write failure / restart can only spend the
#: bound down, never recreate it.
GLOBAL_ACTION_BOUND: Final[int] = 2

#: Hard cap on per-``(signature, action)`` repeat counts. The bounded
#: accumulator lives in
#: :attr:`AttemptLedgerState.repeat_counts` (an
#: :class:`collections.OrderedDict`); the cap is exceeded only when one
#: workspace produces more than 1024 distinct repeat keys, which is well
#: above the bounded signature/action matrix. Evicted counts fold into
#: :attr:`AttemptLedgerState.saturated_repeats`.
REPEAT_CAP = 1024

#: Ceiling for the saturating aggregate; the aggregate is monotonic, so
#: it must saturate rather than grow without bound over a long run.
_MAX_SATURATED_REPEATS: Final[int] = (1 << 31) - 1

#: Number of fields a repeat_counts entry must carry on disk.
_REPEAT_KEY_TUPLE_LEN = 2

#: Schema version of the tombstone payload.
_TOMBSTONE_SCHEMA_VERSION: Final[int] = 1

#: Sidecar suffixes: advisory lock, quarantine for a rejected ledger.
_LOCK_SUFFIX = ".lock"
_QUARANTINE_SUFFIX = ".quarantine"

#: Bounded wait for the cross-process advisory lock. Charging must never
#: block the pipeline, so the wait is capped and a timeout degrades to
#: the in-memory charge path (which still records the charge).
_LOCK_TIMEOUT_SECONDS: Final[float] = 5.0
_LOCK_POLL_SECONDS: Final[float] = 0.005

_RUNG_VALUES: Final[frozenset[str]] = frozenset(rung.value for rung in LADDER_ORDER)


class LedgerLoadStatus(Enum):
    """How much of the durable ledger a load could actually trust."""

    FRESH = "fresh"
    """No ledger file exists yet — a genuine first run."""

    RESTORED = "restored"
    """A ledger written by this schema was read back verbatim."""

    UNKNOWABLE = "unknowable"
    """Prior usage cannot be proven: corrupt, unreadable or foreign schema.

    A :meth:`AttemptLedger.read` still returns the best-effort parsed
    state so no gate blocks, but :meth:`AttemptLedger.charge` treats
    this as "no fresh allowance is ever granted".
    """


@dataclass(frozen=True)
class AttemptLedgerState:
    """Immutable record of the integration boundary's escalation ladder position.

    The :class:`AttemptLedgerState` is the only carry-over between
    successive calls to :func:`ralph.pipeline.integration_decision.decide`:
    it tracks the current rung in
    :data:`~ralph.pipeline.integration_decision.LADDER_ORDER`, the
    per-``(signature, action)`` repeat counts used to drive ladder
    advancement, and the unknown-attempt budget that governs the
    ``WAIT_FOR_ENVIRONMENT`` policy.

    The dataclass is the consumer-visible handoff between this module
    and the integration decision table; the table consumes
    ``observation`` plus this state and returns the next
    :class:`~ralph.pipeline.integration_decision.NextAction`.
    """

    rung: EscalationRung = EscalationRung.REPAIR
    repeat_counts: OrderedDict[tuple[str, str], int] = field(
        default_factory=OrderedDict,
    )  # bounded-accumulator-ok: FIFO-capped by REPEAT_CAP on every persist
    unknown_attempts: int = 0
    unknown_budget: int = 3
    repeat_threshold: int = 2
    version: int = CURRENT_SCHEMA_VERSION
    saturated_repeats: int = 0
    """Saturating sum of every repeat count evicted by :data:`REPEAT_CAP`.

    Folded, never dropped, so an evicted entry can never hand its
    allowance back to a later load.
    """

    global_actions_charged: int = 0
    """Monotone count of exhausted/pending births spent from :data:`GLOBAL_ACTION_BOUND`."""

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
    """Return the per-workspace attempt-ledger path anchored to ``workspace_root``."""
    return Path(workspace_root) / ".agent" / ATTEMPT_LEDGER_FILENAME


def shared_budget_dir(common_dir: Path) -> Path:
    """Return the repository-wide budget directory under a git common directory."""
    return Path(common_dir) / SHARED_BUDGET_DIRNAME


def repository_ledger_path(common_dir: Path) -> Path:
    """Return the repository-wide ledger path shared by every worktree."""
    return shared_budget_dir(common_dir) / ATTEMPT_LEDGER_FILENAME


def tombstone_path(ledger_path: Path) -> Path:
    """Return the global-action-bound tombstone path beside ``ledger_path``."""
    return Path(ledger_path).with_name(TOMBSTONE_FILENAME)


def _saturating_add(total: int, increment: int) -> int:
    """Return ``total + increment`` clamped to :data:`_MAX_SATURATED_REPEATS`."""
    return min(total + increment, _MAX_SATURATED_REPEATS)


def _advance_rung(
    current: EscalationRung,
    *,
    new_count: int,
    threshold: int,
    signature_attempts: int,
) -> EscalationRung:
    """Return the rung this charge moves to — exactly one step, or ``current``.

    Mirrors :meth:`ralph.pipeline.integration_decision.AttemptState.should_advance`
    so the durable ledger and the in-memory dataclass never disagree about
    when the ladder moves. The terminal rung never moves.
    """
    index = LADDER_ORDER.index(current)
    if index >= len(LADDER_ORDER) - 1:
        return current
    if new_count < threshold or signature_attempts < MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION:
        return current
    return LADDER_ORDER[index + 1]


def _further_rung(left: EscalationRung, right: EscalationRung) -> EscalationRung:
    """Return whichever rung sits further along the ladder (never a reset)."""
    if LADDER_ORDER.index(left) >= LADDER_ORDER.index(right):
        return left
    return right


@runtime_checkable
class _HasFileno(Protocol):
    def fileno(self) -> int: ...


def _take_lock(handle: object, deadline: float) -> bool:
    """Take an exclusive advisory lock on ``handle`` before ``deadline``.

    ``fcntl`` is POSIX-only; where it is unavailable the in-process
    ordering stands and the lock is reported as taken. Returns ``False``
    when the bounded window elapsed, which the caller degrades to the
    in-memory charge path rather than blocking the pipeline.
    """
    try:
        import fcntl  # PLC0415: POSIX-only; optional at runtime
    except ImportError:
        return True
    if not isinstance(handle, _HasFileno):
        return True
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            # filesystem-poll-ok: bounded advisory-lock acquisition deadline
            time.sleep(_LOCK_POLL_SECONDS)


def _release_lock(handle: object) -> None:
    """Release the advisory lock taken by :func:`_take_lock`."""
    try:
        import fcntl  # PLC0415: POSIX-only; optional at runtime
    except ImportError:
        return
    if not isinstance(handle, _HasFileno):
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return


class AttemptLedger:
    """Crash-safe, repository-wide owner of :class:`AttemptLedgerState`.

    Every write uses the same atomic write-temp + ``os.replace`` pattern
    the durable auto-integrate record uses
    (:mod:`ralph.pipeline.auto_integrate_record`), taken under an
    exclusive sidecar lock. A torn write — a crash between the
    temp-file write and the rename — leaves the on-disk ledger holding
    the prior committed state, so a spent rung never silently
    resurrects on restart. :meth:`read` is lenient: the highest readable
    rung is preserved; a corrupt or empty ledger does not block dispatch
    and does not reset rungs upward.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        ledger_path: Path | None = None,
        unknown_budget: int = 3,
        repeat_threshold: int = 2,
        common_dir: Path | None = None,
    ) -> None:
        """Bind an attempt-ledger owner to a workspace root.

        Args:
            workspace_root: The worktree / workspace this owner
                observes. Used for the default per-workspace path.
            ledger_path: Optional explicit path override; defaults to
                :func:`attempt_ledger_path` applied to
                ``workspace_root``, or :func:`repository_ledger_path`
                applied to ``common_dir`` when one is supplied.
            unknown_budget: Default unknown-attempt budget applied to
                a freshly-loaded state when the on-disk file is absent
                or unreadable and no fragment yielded a budget.
            repeat_threshold: Default repeat-threshold applied under
                the same freshly-loaded branch.
            common_dir: The repository's git common directory. When
                given, the ledger, its lock and its tombstone all live
                under :func:`shared_budget_dir`, so every worktree of
                the repository charges ONE budget.
        """
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
        # In-memory mirrors of the durable monotone aggregates. They can
        # only ever grow, so a restart cannot hand allowance back.
        self._global_charged = 0
        self._saturated_repeats = 0
        self._unknowable = False
        self._forced_pending = False

    @property
    def ledger_path(self) -> Path:
        """The on-disk file this owner reads and writes."""
        return self._ledger_path

    @property
    def tombstone_path(self) -> Path:
        """The fixed global-action-bound tombstone beside :attr:`ledger_path`."""
        return tombstone_path(self._ledger_path)

    @property
    def is_repository_wide(self) -> bool:
        """True when this ledger is anchored to a git common directory."""
        return self._common_dir is not None

    @property
    def forced_pending(self) -> bool:
        """True once this process has permanently pinned itself to the exhausted rung."""
        return self._forced_pending

    def _default_state(self) -> AttemptLedgerState:
        """The first-run state when no ledger has ever persisted."""
        return AttemptLedgerState(
            rung=EscalationRung.REPAIR,
            repeat_counts=OrderedDict(),
            unknown_attempts=0,
            unknown_budget=self._default_unknown_budget,
            repeat_threshold=self._default_repeat_threshold,
        )

    @staticmethod
    def _exhausted_state(
        state: AttemptLedgerState, *, global_actions_charged: int | None = None
    ) -> AttemptLedgerState:
        """Return the durable, fact-independent exhausted/pending state.

        Born at :attr:`EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING`
        with the unknown budget already spent, so an unreadable probe
        yields *continue with integration pending* immediately instead of
        waiting forever for an environment that is never proven broken.
        """
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
        )

    @staticmethod
    def _payload_status(payload: Mapping[str, object]) -> LedgerLoadStatus:
        """Classify a parsed ledger payload by how much it proves."""
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
        """Return the leniently-parsed state plus how far it can be trusted."""
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
        """Return the current :class:`AttemptLedgerState`.

        Lenient parsing rules — this method never blocks and never
        raises:

        * a missing file yields the first-run default state;
        * a corrupt or unreadable file yields the default state but
          never a rung higher than ``REPAIR`` — the only safe rung when
          no rung was readable from any fragment;
        * an older ``version`` is migrated forward via
          :meth:`_migrate_payload`; the on-disk rung is preserved as
          the highest readable rung;
        * a corrupted ``repeat_counts`` block is treated as empty so a
          single bad key cannot poison the whole map.

        Use :meth:`charge` to consume budget: only the charge path
        treats an untrustworthy load as "no fresh allowance".
        """
        state, _status = self._load()
        return state

    def with_attempt(
        self,
        signature: str,
        action: str,
        *,
        is_unknown: bool,
    ) -> AttemptLedgerState:
        """Return the next :class:`AttemptLedgerState` after one cycle.

        Thin alias for :meth:`charge`; both names describe the same
        single atomic, write-ahead, budget-consuming operation. A raw
        ``(signature, action)`` string pair is accepted so this module
        can stand alone for fault-injection tests; the integration
        decision table is the canonical consumer that builds the
        signature from the observation.
        """
        return self.charge(signature, action, is_unknown=is_unknown)

    def charge(
        self,
        signature: str,
        action: str,
        *,
        is_unknown: bool,
    ) -> AttemptLedgerState:
        """Charge one attempt against the repository-wide budget.

        The charge is durable **before** this method returns, so the
        caller's action can only ever run against an already-recorded
        attempt. Crash between the charge and the effect: the attempt
        still counts.

        Bump rules (mirroring
        :meth:`ralph.pipeline.integration_decision.AttemptState.with_attempt`):

        * ``is_unknown=True`` consumes only the unknown-attempt budget;
        * a non-unknown attempt increments the per-``(signature, action)``
          repeat count and, once the signature has *repeated* and the
          count reaches :attr:`AttemptLedgerState.repeat_threshold`,
          advances the rung by exactly one (never resets downward).

        Adversarial histories are bounded, never restored:

        * an untrustworthy load (corrupt / unreadable / foreign schema)
          or a charge that could not be persisted means prior usage is
          unknowable. The rejected ledger is quarantined and replaced by
          one born at the exhausted/pending rung, and one unit of
          :data:`GLOBAL_ACTION_BOUND` is spent on a tombstone that
          survives restarts. Once the bound is spent, no process ever
          receives a fresh allowance again;
        * if even the tombstone cannot be written, this process pins
          itself to the exhausted/pending rung for its lifetime.
        """
        with self._locked() as acquired:
            state, status = self._load()
            charged = self._global_charged_total()
            if self._forced_pending or charged >= GLOBAL_ACTION_BOUND:
                self._forced_pending = True
                return self._exhausted_state(state)
            if status is LedgerLoadStatus.FRESH and charged > 0:
                # A ledger existed (the tombstone proves it charged) and
                # is now gone: prior usage is unknowable.
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
                # The bounded lock window elapsed. Charge in memory and
                # take the persistence-failure path rather than block.
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
        """Return ``state`` with one attempt folded into the counters."""
        if is_unknown:
            return AttemptLedgerState(
                rung=state.rung,
                repeat_counts=OrderedDict(state.repeat_counts),
                unknown_attempts=state.unknown_attempts + 1,
                unknown_budget=state.unknown_budget,
                repeat_threshold=state.repeat_threshold,
                saturated_repeats=state.saturated_repeats,
                global_actions_charged=state.global_actions_charged,
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
        )

    def _on_persist_failure(
        self, charged: AttemptLedgerState, disk: AttemptLedgerState
    ) -> AttemptLedgerState:
        """Handle a charge whose durable write failed.

        The in-memory charge stands (a charge is never skipped), a
        tombstone is attempted, and when even that cannot be written the
        process is pinned to the exhausted/pending rung for its lifetime.
        """
        self._unknowable = True
        charged_total, granted = self._spend_global_allowance(self._global_charged_total())
        if granted:
            return charged
        self._forced_pending = True
        return self._exhausted_state(
            self._merge_monotone(charged, disk),
            global_actions_charged=charged_total,
        )

    def persist(self, state: AttemptLedgerState) -> None:
        """Write ``state`` atomically to the ledger path.

        Public so callers (the director, the gate, the model
        fault-injection test) can persist a fresh state without
        re-reading the ledger. The write is monotone: the durable rung,
        the saturating aggregate and the global charge only ever move
        forward, so a caller that projected a partial view can never
        hand allowance back. The on-disk representation mirrors
        :meth:`_migrate_payload`'s inverse.
        """
        disk, _status = self._load()
        self._write_state(state, disk)

    def _write_state(self, state: AttemptLedgerState, disk: AttemptLedgerState) -> None:
        """Atomically persist the monotone merge of ``state`` over ``disk``."""
        merged = self._merge_monotone(state, disk)
        path = self._ledger_path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, self._serialize(merged))

    @staticmethod
    def _merge_monotone(state: AttemptLedgerState, disk: AttemptLedgerState) -> AttemptLedgerState:
        """Merge ``state`` over ``disk`` without ever restoring a budget.

        The rung, the saturating aggregate and the global charge take
        the further/higher value; a *raised* ``repeat_threshold`` would
        hand every repeat more headroom, so the lower one wins; repeat
        counts are unioned per key at their maximum so a lost update
        cannot erase a charged attempt.
        """
        merged_counts: OrderedDict[tuple[str, str], int] = OrderedDict(disk.repeat_counts)
        for key, count in state.repeat_counts.items():
            merged_counts[key] = max(merged_counts.get(key, 0), count)
        merged_counts, saturated = AttemptLedger._clamp_repeats(
            merged_counts,
            saturated=max(state.saturated_repeats, disk.saturated_repeats),
        )
        return AttemptLedgerState(
            rung=_further_rung(state.rung, disk.rung),
            repeat_counts=merged_counts,
            unknown_attempts=max(state.unknown_attempts, disk.unknown_attempts),
            unknown_budget=state.unknown_budget,
            repeat_threshold=min(state.repeat_threshold, disk.repeat_threshold),
            version=CURRENT_SCHEMA_VERSION,
            saturated_repeats=saturated,
            global_actions_charged=max(state.global_actions_charged, disk.global_actions_charged),
        )

    @staticmethod
    def _serialize(state: AttemptLedgerState) -> str:
        """Render ``state`` to the canonical JSON wire form."""
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
        }
        return json.dumps(payload, indent=2, sort_keys=False)

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> None:
        """Write ``payload`` to ``path`` via temp file + fsync + rename."""
        temp_path = path.with_suffix(path.suffix + ".staging")
        # filesystem-write-ok: auto-integrate attempt ledger under .agent/, runtime state not tracked
        with temp_path.open("w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            descriptor = stream.fileno()
            if isinstance(descriptor, int):
                os.fsync(descriptor)  # filesystem-write-ok: durable atomic ledger replacement
        temp_path.replace(path)
        directory = path.parent
        try:
            directory_fd = os.open(  # filesystem-write-ok: durable rename; resource-lifecycle-ok: adjacent finally closes fd
                str(directory),
                os.O_RDONLY,
            )
        except OSError:
            return
        try:
            os.fsync(directory_fd)  # filesystem-write-ok: persist ledger rename metadata
        finally:
            os.close(directory_fd)

    @staticmethod
    def _clamp_repeats(
        repeats: OrderedDict[tuple[str, str], int],
        *,
        cap: int = REPEAT_CAP,
        saturated: int = 0,
    ) -> tuple[OrderedDict[tuple[str, str], int], int]:
        """Clamp ``repeats`` to ``cap`` entries, folding evictions into ``saturated``.

        Returns the clamped map plus the updated saturating aggregate.
        Dropping an entry is only safe when its count is added to the
        aggregate — otherwise an evicted entry would restore budget the
        next time it is written under a different key order.
        """
        while len(repeats) > cap:
            _evicted_key, evicted_count = repeats.popitem(last=False)
            saturated = _saturating_add(saturated, evicted_count)
        return repeats, saturated

    def _quarantine_ledger(self) -> None:
        """Move the rejected ledger aside so the replacement starts clean."""
        path = self._ledger_path
        quarantine = path.with_name(path.name + _QUARANTINE_SUFFIX)
        try:
            path.replace(quarantine)
        except OSError:
            return

    def _global_charged_total(self) -> int:
        """Return the durable global charge, never lower than the in-memory one."""
        return min(max(self._tombstone_charged(), self._global_charged), GLOBAL_ACTION_BOUND)

    def _tombstone_charged(self) -> int:
        """Return the durable global-action charge recorded in the tombstone.

        A genuinely absent tombstone means the boundary has never spent a
        global allowance (``0``). A tombstone that exists but cannot be
        parsed reports the *bound* — prior usage is unknowable, and the
        safe answer is that every allowance is already spent.
        """
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
        """Spend one unit of the fixed global action bound.

        Returns ``(charged_total, granted)``. ``granted`` is ``False``
        once the bound is spent, or when the tombstone could not be
        written — an unrecorded charge is an allowance nobody can later
        prove was spent, so it is treated as spent.
        """
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
        """Persist the monotone global-action charge beside the ledger."""
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
        """Hold the sidecar advisory lock; yield whether it was acquired."""
        lock_path = self._ledger_path.with_name(self._ledger_path.name + _LOCK_SUFFIX)
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open(  # filesystem-write-ok: advisory sidecar lock file
                "a+", encoding="utf-8"
            )
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
        """Convert a parsed ledger JSON body into the current shape.

        The lenient parser never resets a rung upward: when no rung is
        readable the resulting state carries
        :attr:`EscalationRung.REPAIR` (the first rung of the finite
        ladder), which is the LEAST permissive safe default — the next
        attempt starts at the bottom, never at a rung the bytes did
        not prove had been reached.
        """
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
        )

    @staticmethod
    def _coerce_repeat_entry(
        entry: object,
    ) -> tuple[tuple[str, str], int] | None:
        """Coerce one ``repeat_counts`` JSON entry to ``((sig, action), count)``."""
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


def _non_negative_int(payload: Mapping[str, object], key: str) -> int:
    """Read ``key`` from ``payload`` as a non-negative int (0 when absent/bad)."""
    raw = payload.get(key, 0)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        return 0
    return min(raw, _MAX_SATURATED_REPEATS)
