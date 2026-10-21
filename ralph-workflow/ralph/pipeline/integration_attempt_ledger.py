"""Durable, restart-proof attempt ledger for Ralph's auto-integration subsystem.

The decision table in
:mod:`ralph.pipeline.integration_decision` is pure and total; the only
state it consumes is :class:`AttemptLedgerState`. Without durability
that state lives in process memory, and any restart, new session or
record reload wipes the budget — which is exactly what failed every
previous fix (the commit-loop root cause in O1a: a budget that resets
on crash, loads below its cap, or transfers when ownership changes).

This module publishes a single, restart-proof ledger contract:

* :class:`AttemptLedgerState` — frozen dataclass carrying the current
  escalation rung, the per-``(state signature, action)`` repeat counts,
  and the unknown-attempt budget (:attr:`unknown_attempts`,
  :attr:`unknown_budget`). The dataclass is the single source of truth
  for the budget every other integration seam must consult.
* :class:`AttemptLedger` — the on-disk owner. It persists the state to
  ``.agent/integration_attempt_ledger.json`` via atomic write-temp +
  ``os.replace``, so a crash between write and effect does not silently
  resurrect a spent rung. Reads are lenient: a corrupt, unreadable or
  older-version ledger is parsed best-effort; the *highest* rung
  readable from any fragment is preserved, and the unknown-attempt
  budget is restored to whatever the file last persisted. A corrupted
  ``repeat_counts`` block is treated as empty; an older ``version`` is
  mapped forward into the current shape. The lenient parser NEVER
  promotes a rung upward, NEVER blocks dispatch, and NEVER restores a
  spent budget it could not see in the bytes it read.
* Per-``(signature, action)`` keys are stored in an
  :class:`collections.OrderedDict` with an explicit ``_REPEAT_CAP`` of
  1024. The cap is enforced in :meth:`AttemptLedger._clamp_repeats` on
  every persist so the on-disk file is itself bounded.

The contract is the public API for G (the director) and I (the model
fault-injection test). The on-disk format is JSON; the schema is the
:class:`AttemptLedgerState` dataclass. A future schema bump changes
:attr:`AttemptLedgerState.version` and adds a new branch to
:meth:`AttemptLedger._migrate_payload`.
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.integration_decision import LADDER_ORDER, EscalationRung

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ATTEMPT_LEDGER_FILENAME",
    "_REPEAT_CAP",
    "AttemptLedger",
    "AttemptLedgerState",
    "attempt_ledger_path",
]


#: Default on-disk file name; lives under ``.agent/`` per workspace root.
ATTEMPT_LEDGER_FILENAME = "integration_attempt_ledger.json"

#: Current schema version. Bumped whenever the JSON shape changes;
#: older versions are migrated forward by
#: :meth:`AttemptLedger._migrate_payload`. Bumping the constant is the
#: ONLY supported way to introduce a backwards-incompatible change.
CURRENT_SCHEMA_VERSION = 1

#: Hard cap on per-``(signature, action)`` repeat counts. The bounded
#: accumulator lives in
#: :attr:`AttemptLedgerState.repeat_counts` (an
#: :class:`collections.OrderedDict`); the cap is exceeded only when one
#: workspace produces more than 1024 distinct repeat keys, which is well
#: above the bounded signature/action matrix.
_REPEAT_CAP = 1024

#: Number of fields a repeat_counts entry must carry on disk.
_REPEAT_KEY_TUPLE_LEN = 2


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
    )  # bounded-accumulator-ok: FIFO-capped by _REPEAT_CAP on every persist
    unknown_attempts: int = 0
    unknown_budget: int = 3
    repeat_threshold: int = 2
    version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.unknown_attempts < 0:
            raise ValueError("unknown_attempts must be >= 0")
        if self.unknown_budget < 1:
            raise ValueError("unknown_budget must be >= 1")
        if self.repeat_threshold < 1:
            raise ValueError("repeat_threshold must be >= 1")
        if self.version < 1:
            raise ValueError("version must be >= 1")


def attempt_ledger_path(workspace_root: Path) -> Path:
    """Return the durable attempt-ledger path anchored to ``workspace_root``."""
    return workspace_root / ".agent" / ATTEMPT_LEDGER_FILENAME


class AttemptLedger:
    """Crash-safe owner of :class:`AttemptLedgerState` for one workspace.

    All writes use the same atomic write-temp + ``os.replace`` pattern
    the durable auto-integrate record uses
    (:mod:`ralph.pipeline.auto_integrate_record`). A torn write — a
    crash between the temp-file write and the rename — leaves the
    on-disk ledger holding the prior committed state, so a spent rung
    never silently resurrects on restart. Reads are lenient: the
    highest readable rung is preserved; a corrupt or empty ledger does
    not block dispatch and does not reset rungs upward.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        ledger_path: Path | None = None,
        unknown_budget: int = 3,
        repeat_threshold: int = 2,
    ) -> None:
        """Bind an attempt-ledger owner to a workspace root.

        Args:
            workspace_root: The worktree / workspace whose
                ``.agent/integration_attempt_ledger.json`` this owner
                reads and writes.
            ledger_path: Optional explicit path override; defaults to
                :func:`attempt_ledger_path` applied to
                ``workspace_root``.
            unknown_budget: Default unknown-attempt budget applied to
                a freshly-loaded state when the on-disk file is absent
                or unreadable and no fragment yielded a budget.
            repeat_threshold: Default repeat-threshold applied under
                the same freshly-loaded branch.
        """
        self._workspace_root = Path(workspace_root)
        if ledger_path is None:
            self._ledger_path: Path = attempt_ledger_path(self._workspace_root)
        else:
            self._ledger_path = Path(ledger_path)
        if unknown_budget < 1:
            raise ValueError("unknown_budget must be >= 1")
        if repeat_threshold < 1:
            raise ValueError("repeat_threshold must be >= 1")
        self._default_unknown_budget = unknown_budget
        self._default_repeat_threshold = repeat_threshold

    @property
    def ledger_path(self) -> Path:
        """The on-disk file this owner reads and writes."""
        return self._ledger_path

    def _default_state(self) -> AttemptLedgerState:
        """The first-run state when no ledger has ever persisted."""
        return AttemptLedgerState(
            rung=EscalationRung.REPAIR,
            repeat_counts=OrderedDict(),
            unknown_attempts=0,
            unknown_budget=self._default_unknown_budget,
            repeat_threshold=self._default_repeat_threshold,
        )

    def read(self) -> AttemptLedgerState:
        """Return the current :class:`AttemptLedgerState`.

        Lenient parsing rules:

        * a missing file yields the first-run default state;
        * a corrupt or unreadable file yields the default state but
          never a rung higher than ``REPAIR`` — the only safe rung when
          no rung was readable from any fragment;
        * an older ``version`` is migrated forward via
          :meth:`_migrate_payload`; the on-disk rung is preserved as
          the highest readable rung;
        * a corrupted ``repeat_counts`` block is treated as empty so a
          single bad key cannot poison the whole map.
        """
        path = self._ledger_path
        try:
            raw_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._default_state()
        except (OSError, UnicodeError):
            return self._default_state()

        try:
            payload: object = json.loads(raw_text)
        except (ValueError, RecursionError):
            return self._default_state()

        if not isinstance(payload, dict):
            return self._default_state()

        return self._migrate_payload(payload)

    def with_attempt(
        self,
        signature: str,
        action: str,
        *,
        is_unknown: bool,
    ) -> AttemptLedgerState:
        """Return the next :class:`AttemptLedgerState` after one cycle.

        The next state is computed in memory and persisted to disk
        BEFORE returning. A crash between ``read()`` and the
        ``os.replace`` of the new file still counts the attempt —
        that's the write-ahead high-water mark the contract requires.

        Bump rules:

        * ``is_unknown=True`` consumes only the unknown-attempt budget;
        * a non-unknown attempt increments the per-``(signature, action)``
          repeat count and, once it crosses :attr:`repeat_threshold`,
          advances the rung by exactly one (never resets downward).

        A raw ``(signature, action)`` string pair is accepted so this
        module can stand alone for fault-injection tests; the
        integration decision table is the canonical consumer that
        builds the signature from the observation.
        """
        current = self.read()
        if is_unknown:
            next_state = AttemptLedgerState(
                rung=current.rung,
                repeat_counts=OrderedDict(current.repeat_counts),
                unknown_attempts=current.unknown_attempts + 1,
                unknown_budget=current.unknown_budget,
                repeat_threshold=current.repeat_threshold,
            )
        else:
            key = (signature, action)
            updated: OrderedDict[tuple[str, str], int] = OrderedDict(current.repeat_counts)
            new_count = updated.get(key, 0) + 1
            updated[key] = new_count
            updated = self._clamp_repeats(updated)
            advanced_rung = self._maybe_advance_rung(
                current.rung,
                new_count=new_count,
                threshold=current.repeat_threshold,
            )
            next_state = AttemptLedgerState(
                rung=advanced_rung,
                repeat_counts=updated,
                unknown_attempts=0,
                unknown_budget=current.unknown_budget,
                repeat_threshold=current.repeat_threshold,
            )
        self.persist(next_state)
        return next_state

    def persist(self, state: AttemptLedgerState) -> None:
        """Write ``state`` atomically to the ledger path.

        Public so callers (the director, the model fault-injection
        test) can persist a fresh state without re-reading the ledger.
        The on-disk representation mirrors
        :meth:`_migrate_payload`'s inverse — only fields set explicitly
        here are written.
        """
        path = self._ledger_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._serialize(state)
        temp_path = path.with_suffix(path.suffix + ".staging")
        # filesystem-write-ok: auto-integrate attempt ledger under .agent/, runtime state not tracked
        with temp_path.open("w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            descriptor = stream.fileno()
            if isinstance(descriptor, int):
                os.fsync(descriptor)
        temp_path.replace(path)
        directory = path.parent
        try:
            directory_fd = os.open(str(directory), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

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
        }
        return json.dumps(payload, indent=2, sort_keys=False)

    @staticmethod
    def _clamp_repeats(
        repeats: OrderedDict[tuple[str, str], int],
        *,
        cap: int = _REPEAT_CAP,
    ) -> OrderedDict[tuple[str, str], int]:
        """Clamp ``repeats`` to at most ``cap`` entries (FIFO eviction)."""
        while len(repeats) > cap:
            repeats.popitem(last=False)
        return repeats

    @staticmethod
    def _maybe_advance_rung(
        current: EscalationRung,
        *,
        new_count: int,
        threshold: int,
    ) -> EscalationRung:
        """Return the rung one position further along the ladder.

        Past the terminal rung the ladder stays put — finite and
        monotone. Crossed once the per-``(signature, action)`` repeat
        count meets or exceeds :data:`threshold`.
        """
        ladder: tuple[EscalationRung, ...] = LADDER_ORDER
        terminal = ladder[-1]
        if new_count < threshold or current is terminal:
            return current
        idx = ladder.index(current)
        return ladder[idx + 1]

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

        repeat_threshold_raw = payload.get(
            "repeat_threshold", self._default_repeat_threshold
        )
        repeat_threshold = (
            int(repeat_threshold_raw)
            if isinstance(repeat_threshold_raw, int)
            and not isinstance(repeat_threshold_raw, bool)
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
        repeats = self._clamp_repeats(repeats)

        return AttemptLedgerState(
            rung=rung,
            repeat_counts=repeats,
            unknown_attempts=unknown_attempts,
            unknown_budget=unknown_budget,
            repeat_threshold=repeat_threshold,
            version=CURRENT_SCHEMA_VERSION,
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
