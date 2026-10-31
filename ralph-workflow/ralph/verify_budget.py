"""Contention-aware accounting for the combined ``make verify`` test budget.

``ralph/verify.py`` charges every budget-tracked test step against two
limits, because wall clock alone cannot tell a slower suite from a busier
machine:

* **CPU** -- the core-seconds the suite's own processes consumed, read from
  ``getrusage(RUSAGE_CHILDREN)`` deltas. Those include every reaped
  descendant, so the short-lived git and child interpreters a process
  sampler misses are counted too. This is the work the suite itself does;
  other load on the host barely moves it, so exceeding the CPU budget is
  always a failure.

* **Wall clock** -- the 60 s target a developer actually waits for. On a
  shared host it also measures everything else that ran. While the tests
  ran, the host spent ``host busy - suite CPU`` core-seconds on other
  processes; spread across the host's cores, that is the most wall time
  that foreign load can have taken from the suite (the *contention
  credit*). A run over the wall budget that fits once the credit is
  subtracted is reported as a host-contention WARNING; a run still over
  is a failure. On an idle host the credit is ~0 and the wall budget
  applies exactly as written.

The credit is an upper bound -- it assumes every foreign core-second
displaced the suite -- which is why the CPU budget is a hard limit: a
suite that grows can shelter behind contention on wall clock, never on
CPU. Host busy time counts only ``user + system`` (no iowait, no nice), so
the credit can be under-estimated but never inflated by idle I/O waits.
"""

from __future__ import annotations

import resource
import sys
from dataclasses import dataclass
from typing import Literal

import psutil

SuiteBudgetOutcome = Literal[
    "within_budget",
    "host_contention",
    "over_wall_budget",
    "over_cpu_budget",
]


def sample_suite_resources() -> tuple[float, float]:
    """Return ``(children CPU seconds, host busy CPU seconds)`` at this instant.

    Both are monotonic counters; a budget-tracked step is charged the
    difference between a sample taken before it and one taken after it.
    """
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    host = psutil.cpu_times()
    return usage.ru_utime + usage.ru_stime, host.user + host.system


@dataclass(frozen=True, slots=True)
class SuiteBudgetReport:
    """What the budget-tracked test steps cost, and the verdict on it.

    Attributes:
        wall_seconds: Cumulative wall clock across the tracked steps.
        suite_cpu_seconds: CPU the steps' own process trees consumed.
        host_busy_seconds: CPU the whole host spent busy meanwhile.
        cpu_count: Logical CPUs the host has.
        wall_budget_seconds: The wall-clock budget.
        cpu_budget_seconds: The CPU budget, in core-seconds.
    """

    wall_seconds: float
    suite_cpu_seconds: float
    host_busy_seconds: float
    cpu_count: int
    wall_budget_seconds: float
    cpu_budget_seconds: float

    @property
    def foreign_cpu_seconds(self) -> float:
        """Core-seconds other processes used while the tests ran."""
        return max(0.0, self.host_busy_seconds - self.suite_cpu_seconds)

    @property
    def contention_credit_seconds(self) -> float:
        """The most wall time foreign load can have taken from the suite."""
        return self.foreign_cpu_seconds / max(1, self.cpu_count)

    @property
    def adjusted_wall_seconds(self) -> float:
        """Wall clock with the contention credit subtracted."""
        return max(0.0, self.wall_seconds - self.contention_credit_seconds)

    @property
    def outcome(self) -> SuiteBudgetOutcome:
        """The verdict: CPU first (the suite owns it), then wall clock."""
        if self.suite_cpu_seconds > self.cpu_budget_seconds:
            return "over_cpu_budget"
        if self.wall_seconds <= self.wall_budget_seconds:
            return "within_budget"
        if self.adjusted_wall_seconds <= self.wall_budget_seconds:
            return "host_contention"
        return "over_wall_budget"

    def announce(self) -> str | None:
        """Print a host-contention WARNING when due; return the failure, if any.

        Returns:
            ``None`` when the suite is within budget (with or without the
            contention credit), else the reason the budget was exhausted.
        """
        outcome = self.outcome
        if outcome == "host_contention":
            print(
                "WARNING: the tests took longer than the wall-clock budget only "
                "because other processes were using this host's CPUs; net of that "
                "contention the suite is within budget. Re-run on a quieter host "
                "to measure it alone.",
                file=sys.stderr,
                flush=True,
            )
            return None
        if outcome == "over_cpu_budget":
            return "suite CPU exceeded"
        if outcome == "over_wall_budget":
            return "wall clock exceeded even after crediting host contention"
        return None

    def describe(self) -> str:
        """One human-readable line with every number the verdict used."""
        foreign_cores = self.foreign_cpu_seconds / self.wall_seconds if self.wall_seconds else 0.0
        return (
            f"wall {self.wall_seconds:.2f}s / {self.wall_budget_seconds:.1f}s "
            f"(contention-adjusted {self.adjusted_wall_seconds:.2f}s: other processes "
            f"kept {foreign_cores:.1f} of {self.cpu_count} cores busy), "
            f"suite CPU {self.suite_cpu_seconds:.1f} / {self.cpu_budget_seconds:.1f} core-s"
        )


__all__ = ["SuiteBudgetOutcome", "SuiteBudgetReport", "sample_suite_resources"]
