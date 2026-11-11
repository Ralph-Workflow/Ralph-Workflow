from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

WHITEBOX_CATEGORIES: tuple[str, ...] = (
    "setattr_private",
    "private_import",
    "private_attr",
)

BASELINE_DRIFT_CATEGORIES: frozenset[str] = frozenset(
    {
        "whitebox-baseline-removed",
        "whitebox-baseline-stale",
        "whitebox-baseline-unlowered",
    }
)


class WhiteboxDrift(NamedTuple):
    path: str
    category: str
    detail: str


def reconcile_whitebox_baseline(
    inventory: Mapping[str, Mapping[str, int]],
    baseline: Mapping[str, Mapping[str, int]],
    *,
    existing_paths: Collection[str] | None = None,
) -> list[WhiteboxDrift]:
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
