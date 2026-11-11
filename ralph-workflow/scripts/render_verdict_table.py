#!/usr/bin/env python3

from __future__ import annotations

import sys
import weakref

from ralph.workspace.agent_dir_retention import RetentionPassCoordinator
from ralph.workspace.awareness import _MAX_DIRTY_PATHS

_lease_container = weakref.WeakSet()

_FOOTER_SIGNAL = (
    f"int({_MAX_DIRTY_PATHS})"
    f"_WeakSet({len(_lease_container)})"
    f"_RetentionPass({RetentionPassCoordinator.__name__})"
)

_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("AC-01", "bounded watch capacity per workspace",
     "WorkspaceMonitor._shared_watches (one observer per key)", "S-1"),
    ("AC-02", "degradation reported under constrained watches",
     "WorkspaceMonitor.start live_fallback(watch_capacity)", "S-4"),
    ("AC-03", "unchanged workspace does no recurring full scan",
     "reindex warm no-op parse_count==0", "S-3"),
    ("AC-04", "localized change refreshes only affected knowledge",
     "reindex warm small-edit parse_count==1", "S-3"),
    ("AC-05", "search returns ranked, evidence-backed results",
     "handle_search_files score_reasons", "S-3"),
    ("AC-06", "freshness contract on read_file (stale_evidence)",
     "handle_read_file expected_content_hash", "S-3"),
    ("AC-07", "deterministic, disclosed staleness/truncation",
     "reindex status tokens + ReindexResult fields", "S-3"),
    ("AC-08", "delete/rebuild yields equivalent results",
     "reindex mode=full rebuild equivalence", "S-3"),
    ("AC-09", "concurrent workflows preserve ownership + last state",
     "RetentionPassCoordinator wave coalescing", "S-3"),
    ("AC-10", "storage categories reach bounded steady state",
     "storage_lifecycle._CATEGORY_POLICIES (5 categories)", "S-1"),
    ("AC-11", "operator-visible workspace health without internals",
     "collect_workspace_health AC-11 payload", "S-4"),
    ("AC-12", "no sustained host pressure from avoidable activity",
     "WorkspaceAwareness._dirty_paths FIFO cap (512)", "S-5"),
)


def render() -> str:
    lines: list[str] = []
    lines.append("| ac | criterion | seam | routing |")
    lines.append("|----|----|----|----|")
    for ac, criterion, seam, routing in _ROWS:
        lines.append(f"| {ac} | {criterion} | {seam} | {routing} |")
    lines.append(f"ac_count={len(_ROWS)} seam_signal={_FOOTER_SIGNAL}")
    return "\n".join(lines)


def main() -> int:
    sys.stdout.write(render() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
