"""Bounded zero-progress guard for the agent recovery loop.

The recovery loop (``effect_executor._invoke_agent_with_recovery``) retries a
failed agent up to ``max_recovery_attempts``. Total attempts are already bounded
by that count and by the session wall-clock ceiling, but nothing stopped the loop
from re-running an *identically failing* attempt up to that bound — making zero
forward progress while consuming the entire retry/time budget. That is the wedge
that surfaces as an endless ``Retrying ... (N/10)`` loop that restarts from
scratch each time.

This module makes the zero-progress case provably bounded. Each failure is
collapsed to a normalized signature (reason + fingerprinted output tail), and the
loop must STOP once the same signature repeats ``MAX_IDENTICAL_RETRY_ATTEMPTS``
times in a row. The bound is independent of ``max_recovery_attempts``, so it holds
regardless of configuration. A signature change — a genuinely different failure,
or forward progress reflected in the output — resets the streak, so a slow but
progressing agent still gets its full retry budget.

The guard adds a bound; it never removes the existing ones. The loop therefore
terminates after at most ``min(max_recovery_attempts + 1, <bounded by signatures>)``
attempts, and cannot execute more than ``MAX_IDENTICAL_RETRY_ATTEMPTS`` attempts
sharing one signature.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ralph.agents.idle_watchdog.repetition_tracker import RepetitionTracker
from ralph.pipeline._restart_from_scratch import structural_restart_fingerprint

#: After the base fingerprint, collapse EVERY numeric token — in any textual form
#: — to a single ``<n>``. RepetitionTracker.fingerprint only normalizes integers
#: of 7+ digits and ISO timestamps that carry a date; a doomed retry that prints a
#: short duration ("300s"), a bare clock ("14:33:53"), a PID, a decimal ("1.5s"),
#: a version/IP ("1.2.3"), scientific notation ("1e-9"), short hex ("0x1f"), or an
#: incrementing counter would otherwise vary the signature every attempt and slip
#: past the bound. Matching the WHOLE numeric form (not just digit runs) means a
#: number that changes value OR form (int↔decimal, sign flip, hex letters) still
#: collapses identically, so only NON-numeric content — real, word-level progress
#: — can change the signature and reset the streak. (Operates on the lowercased
#: output of RepetitionTracker.fingerprint, so ``0x``/``e`` are lowercase.)
_NUMERIC_TOKEN = re.compile(r"0x[0-9a-f]+|\d+(?:[.:]\d+)*(?:e[+-]?\d+)?")

#: Maximum number of consecutive retries that may share one failure signature
#: before the loop aborts. Small by design: a real spiral repeats the same
#: signature, so a tight cap fails fast; genuine progress changes the signature
#: and resets the streak. An import-time guard (``if``/``raise``, not ``assert``,
#: so ``python -O`` cannot strip it) keeps the bound a positive integer.
MAX_IDENTICAL_RETRY_ATTEMPTS = 3

if not isinstance(MAX_IDENTICAL_RETRY_ATTEMPTS, int) or MAX_IDENTICAL_RETRY_ATTEMPTS < 1:
    raise RuntimeError("MAX_IDENTICAL_RETRY_ATTEMPTS must be a positive integer")


def retry_failure_signature(reason: str, rendered_output: Sequence[str]) -> str:
    """Collapse a failed attempt to a normalized signature.

    Combines the canonical retry reason with the rendered output tail, normalizes
    via :meth:`RepetitionTracker.fingerprint` (timestamps, uuids, hex blobs), then
    collapses EVERY numeric token in any form (see ``_NUMERIC_TOKEN``). After this,
    no number — a clock, an elapsed-seconds counter, a PID, a short duration, a
    decimal, a version/IP, scientific notation, or short hex — can vary the
    signature by changing its value OR its textual form, so a doomed loop that
    merely prints a changing number cannot evade the bound. Only NON-numeric
    content differs between attempts, so a signature change means genuine
    word-level progress and legitimately resets the streak.

    Structural-dominant composition: when the rendered output opens with a
    known restart-from-scratch narrative pattern (see
    :mod:`ralph.pipeline._restart_from_scratch`), the structural fingerprint
    is the dominant signal — it collapses opening-narrative patterns across
    attempts even when the concrete tokens (file names, paths) differ. The
    literal fingerprint cannot collapse path tokens (e.g. ``/tmp/PROMPT.md``
    vs ``/tmp/PROMPT_v2.md`` differ in the chars beyond the ``_NUMERIC_TOKEN``
    vocabulary), so concatenating the two sides with a separator would let
    the literal divergence prevent the structural-collapse-on-3rd from
    happening. When no restart pattern is detected, fall back to the literal
    fingerprint so existing tests that assert on signature equality across
    non-narrative pairs still pass.
    """
    joined = "\n".join((reason, *rendered_output))
    literal = _NUMERIC_TOKEN.sub("<n>", RepetitionTracker.fingerprint(joined))
    structural = structural_restart_fingerprint(rendered_output)
    if structural != "restart:none":
        return structural
    return literal


@dataclass
class RetryProgressGuard:
    """Track consecutive identical failure signatures and force a stop at the cap.

    Durable and restart-surviving: supports state serialization via
    :meth:`snapshot` and restoration via :meth:`from_dict`. Exhaustion then
    restart does not replenish attempts.
    """

    max_identical: int = MAX_IDENTICAL_RETRY_ATTEMPTS
    last_signature: str | None = None
    streak: int = 0
    failed_closed: bool = False

    @property
    def _last_signature(self) -> str | None:
        return self.last_signature

    @_last_signature.setter
    def _last_signature(self, value: str | None) -> None:
        self.last_signature = value

    @property
    def _streak(self) -> int:
        return self.streak

    @_streak.setter
    def _streak(self, value: int) -> None:
        self.streak = value

    @property
    def is_exhausted(self) -> bool:
        """Whether identical retries have hit or exceeded the max cap."""
        return self.streak >= self.max_identical or self.failed_closed

    @property
    def requires_operator_intervention(self) -> bool:
        """Whether identical retries or malformed state require operator intervention."""
        return self.is_exhausted or self.failed_closed

    @property
    def has_failed_closed(self) -> bool:
        """Whether the guard failed closed due to malformed or contradictory state."""
        return self.failed_closed

    def record(self, signature: str) -> bool:
        """Record a failed attempt that has already executed.

        Called after an attempt fails and before the loop decides to retry.
        Returns ``True`` when retrying MUST stop because the same signature has
        now repeated ``max_identical`` times in a row (zero forward progress).
        Because it is called post-execution, exactly ``max_identical`` identical
        attempts run before the stop — the guard prevents the next one.
        """
        if self.is_exhausted:
            return True
        if signature == self.last_signature:
            self.streak += 1
        else:
            self.last_signature = signature
            self.streak = 1
        return self.is_exhausted

    def reset(self) -> None:
        """Reset streak on genuine forward progress."""
        self.last_signature = None
        self.streak = 0
        self.failed_closed = False

    def snapshot(self) -> dict[str, object]:
        """Serialize guard state for checkpointing."""
        return {
            "max_identical": self.max_identical,
            "last_signature": self.last_signature,
            "streak": self.streak,
            "is_exhausted": self.is_exhausted,
            "failed_closed": self.failed_closed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | object) -> RetryProgressGuard:
        """Restore guard state from checkpointed data.

        Fails closed to operator intervention / exhaustion if the data is
        malformed, missing required fields, has negative streaks, or has
        contradictory flags.
        """
        fallback_max = MAX_IDENTICAL_RETRY_ATTEMPTS
        if not isinstance(data, Mapping):
            return cls(
                max_identical=fallback_max,
                last_signature=None,
                streak=fallback_max,
                failed_closed=True,
            )

        try:
            if "streak" not in data or "max_identical" not in data:
                raise ValueError("Missing required fields")

            raw_max = data["max_identical"]
            if isinstance(raw_max, bool) or not isinstance(raw_max, (int, str)):
                raise TypeError("Invalid max_identical type")
            max_identical = int(raw_max)
            if max_identical < 1:
                raise ValueError("max_identical < 1")

            raw_streak = data["streak"]
            if isinstance(raw_streak, bool) or not isinstance(raw_streak, (int, str)):
                raise TypeError("Invalid streak type")
            streak = int(raw_streak)
            if streak < 0:
                raise ValueError("Negative streak")

            raw_sig = data.get("last_signature")
            if raw_sig is not None and not isinstance(raw_sig, str):
                raise TypeError("Invalid last_signature type")
            last_signature = raw_sig

            raw_exhausted = data.get("is_exhausted")
            if raw_exhausted is not None:
                if not isinstance(raw_exhausted, bool):
                    raise TypeError("Invalid is_exhausted type")
                if raw_exhausted and streak < max_identical:
                    raise ValueError("Contradictory is_exhausted: True when streak < max")
                if not raw_exhausted and streak >= max_identical:
                    raise ValueError("Contradictory is_exhausted: False when streak >= max")

            raw_failed_closed = bool(data.get("failed_closed", False))
            is_failed = raw_failed_closed or streak >= max_identical
            return cls(
                max_identical=max_identical,
                last_signature=last_signature,
                streak=max(streak, max_identical) if is_failed else streak,
                failed_closed=is_failed,
            )
        except Exception:
            return cls(
                max_identical=fallback_max,
                last_signature=None,
                streak=fallback_max,
                failed_closed=True,
            )


__all__ = [
    "MAX_IDENTICAL_RETRY_ATTEMPTS",
    "RetryProgressGuard",
    "retry_failure_signature",
]
