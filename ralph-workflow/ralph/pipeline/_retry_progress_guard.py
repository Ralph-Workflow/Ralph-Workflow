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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog.repetition_tracker import RepetitionTracker

if TYPE_CHECKING:
    from collections.abc import Sequence

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
    """
    joined = "\n".join((reason, *rendered_output))
    return _NUMERIC_TOKEN.sub("<n>", RepetitionTracker.fingerprint(joined))


@dataclass
class RetryProgressGuard:
    """Track consecutive identical failure signatures and force a stop at the cap."""

    max_identical: int = MAX_IDENTICAL_RETRY_ATTEMPTS
    _last_signature: str | None = field(default=None, init=False)
    _streak: int = field(default=0, init=False)

    def record(self, signature: str) -> bool:
        """Record a failed attempt that has already executed.

        Called after an attempt fails and before the loop decides to retry.
        Returns ``True`` when retrying MUST stop because the same signature has
        now repeated ``max_identical`` times in a row (zero forward progress).
        Because it is called post-execution, exactly ``max_identical`` identical
        attempts run before the stop — the guard prevents the next one.
        """
        if signature == self._last_signature:
            self._streak += 1
        else:
            self._last_signature = signature
            self._streak = 1
        return self._streak >= self.max_identical


__all__ = [
    "MAX_IDENTICAL_RETRY_ATTEMPTS",
    "RetryProgressGuard",
    "retry_failure_signature",
]
