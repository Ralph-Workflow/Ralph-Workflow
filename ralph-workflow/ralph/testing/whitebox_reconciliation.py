"""Whole-tree reconciliation between the frozen white-box baseline and reality.

The frozen baseline (:mod:`ralph.testing.test_whitebox_baseline`) records how
much white-box coupling every test file still has. The per-file ratchet in
:mod:`ralph.testing.audit_test_policy` can only ever look at ONE file at a
time, so it is structurally blind to the drift that matters most for the
exception ledger: a baseline row that no longer corresponds to any coupling.

Two shapes of that drift exist:

``whitebox-baseline-removed``
    The baseline names a test file that no longer exists in the tree. The row
    is a leftover of a deleted file and must be deleted from the baseline.

``whitebox-baseline-stale``
    The baseline names a file that still exists but whose recorded coupling
    has been fully converted away. The row claims an exception that the file
    no longer needs, so the exception ledger and the baseline are lying.

``whitebox-baseline-unlowered``
    The baseline ceiling for a file is above its fresh count. The ceiling is
    a snapshot of the retained coupling, so it must be lowered to the fresh
    count; a ceiling that is too high keeps the ledger claiming coupling that
    is no longer there.

``whitebox-regression``
    The fresh count is above the frozen ceiling. This is the ratchet's own
    direction and is already reported per file by
    :func:`ralph.testing.audit_test_policy.audit_whitebox_ratchet`; the
    whole-tree check reports it too so that the reconciliation is complete on
    its own, but the audit does not print it twice.

The reconciliation is a pure function over two mappings, so it can be
exercised against fixtures without touching the filesystem.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

#: The three white-box detector categories tracked by the baseline.
WHITEBOX_CATEGORIES: tuple[str, ...] = (
    "setattr_private",
    "private_import",
    "private_attr",
)

#: Drift categories the whole-tree reconciliation owns. The regression
#: category is deliberately absent: the per-file ratchet already reports it.
BASELINE_DRIFT_CATEGORIES: frozenset[str] = frozenset(
    {
        "whitebox-baseline-removed",
        "whitebox-baseline-stale",
        "whitebox-baseline-unlowered",
    }
)


class WhiteboxDrift(NamedTuple):
    """One divergence between the frozen baseline and the fresh inventory."""

    path: str
    category: str
    detail: str


def reconcile_whitebox_baseline(
    inventory: Mapping[str, Mapping[str, int]],
    baseline: Mapping[str, Mapping[str, int]],
    *,
    existing_paths: Collection[str] | None = None,
) -> list[WhiteboxDrift]:
    """Return every divergence between ``inventory`` and ``baseline``.

    Args:
        inventory: Fresh white-box counts keyed by project-relative test path.
            Only files with at least one non-zero count are present.
        baseline: The frozen ceilings keyed by project-relative test path.
        existing_paths: When provided, the set of test paths that still exist
            in the tree. It distinguishes a removed file from a file whose
            coupling was fully converted away.

    Returns:
        One :class:`WhiteboxDrift` per divergence, ordered by path then
        category so the report is deterministic.
    """
    drifts: list[WhiteboxDrift] = []
    for path in sorted(baseline):
        ceiling = baseline[path]
        current = inventory.get(path)
        if current is None:
            if existing_paths is not None and path not in existing_paths:
                drifts.append(
                    WhiteboxDrift(
                        path,
                        "whitebox-baseline-removed",
                        "baseline records a test file that no longer exists; "
                        "delete the baseline row",
                    )
                )
            else:
                drifts.append(
                    WhiteboxDrift(
                        path,
                        "whitebox-baseline-stale",
                        "baseline records white-box coupling the file no longer has; "
                        "delete the baseline row and its ledger entry",
                    )
                )
            continue
        for category in WHITEBOX_CATEGORIES:
            fresh = current.get(category, 0)
            allowed = ceiling.get(category, 0)
            if fresh > allowed:
                drifts.append(
                    WhiteboxDrift(
                        path,
                        "whitebox-regression",
                        f"{category} count {fresh} exceeds frozen ceiling {allowed}",
                    )
                )
            elif fresh < allowed:
                drifts.append(
                    WhiteboxDrift(
                        path,
                        "whitebox-baseline-unlowered",
                        f"{category} ceiling {allowed} is above the current count {fresh}; "
                        "lower the baseline row to the current count",
                    )
                )
    return drifts
