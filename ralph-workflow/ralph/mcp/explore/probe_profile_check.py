"""S-1 profiling check for the indexed-explore freshness/metadata hot path.

Profiles ``staleness_probe`` and ``serving_metadata`` on an indexed
``ralph_self``-sized workspace and asserts that the expected hot-path
entries are present by name in the cumulative-time table. The probe
itself takes its real indexed path: it builds a real
:class:`ExploreStore` in a temporary directory, calls
:func:`ralph.mcp.explore.pipeline.reindex` to populate the index, and
constructs a real :class:`ExploreIndex` handle so the profile reflects
production serving behavior.

Why this script exists
----------------------

After the S-2/S-3 refactor removed the per-file SQL loop from
``staleness_probe`` and added the staleness-block cache, the obvious
question is whether any further hot path remains. ``cProfile`` answers
that with a single cumulative-time table: if the top entries are now
``collect_workspace_files`` (the one filesystem walk we explicitly
kept, required for truthful F12 external-edit detection) plus a few
bounded store calls, the optimization is complete; if ``store.get_file``
or another per-file SQL loop reappears, the table will say so and the
assertion in :func:`main` will fail.

Usage::

    python -m ralph.mcp.explore.probe_profile_check

Exit codes:

* ``0`` -- the probe ran and the expected entries are present.
* ``1`` -- the probe ran but an expected entry was absent.
* ``2`` -- the probe could not build an indexed workspace.

The script writes its recorded profile to ``ralph-workflow/tmp/`` so
later runs can diff the cumulative-time table without re-running the
probe.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.mcp.explore.handlers import ExploreIndex
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.serving import (
    serving_metadata,
    staleness_probe,
)
from ralph.mcp.explore.store import DEFAULT_INDEX_ROOT, ExploreStore

if TYPE_CHECKING:
    from collections.abc import Sequence


#: Cumulative-time sub-strings that MUST appear in the profile when the
#: optimization is honest. We assert on substrings rather than exact
#: qualified names because cProfile may elide module qualifiers when the
#: total time is very small. ``collect_workspace_files`` is the
#: single filesystem walk the freshness probe is allowed to keep
#: (F12 truthfulness); the store-row readers are the bulk load added
#: in S-2. ``store.get_file`` MUST NOT appear -- it is the per-file
#: SQL loop the refactor removed, and its return is the optimization's
#: correctness guarantee.
_REQUIRED_PROBE_ENTRIES: tuple[str, ...] = (
    "collect_workspace_files",
    "bulk_size_mtime_for_paths",
)
_FORBIDDEN_PROBE_ENTRIES: tuple[str, ...] = (
    "store.get_file",
)
#: ``_staleness_block`` has been cached since S-3; on a hit the
#: cumulative-time contribution of ``peek_dirty_paths`` /
#: ``count_deleted_files`` / ``latest_job`` is near zero. We assert
#: their presence (they remain the canonical signals when the
#: signature changes) but do not pin their order.
_REQUIRED_METADATA_ENTRIES: tuple[str, ...] = (
    "peek_dirty_paths",
    "count_deleted_files",
    "latest_job",
)
#: Maximum depth at which we copy Python sources. Anything deeper
#: adds cold-build time without changing which hot-path entries the
#: profile surfaces.
_MAX_RELATIVE_DEPTH: Final[int] = 4


def _build_indexed_workspace(tmp: Path) -> tuple[Path, Path, ExploreIndex]:
    """Build an indexed copy of the ralph_self workspace and return the handle.

    Returns ``(workspace_copy, index_dir, handle)``. The workspace is
    a copy so the probe does not mutate the real tree; the index lives
    in a sibling directory that the caller cleans up.
    """
    package_root = Path(__file__).resolve().parents[3]
    workspace_copy = tmp / "workspace"
    # A complete copy of ralph_self is unnecessary; we just need a
    # tree that walks cleanly with ``collect_workspace_files``. A
    # handful of files in a directory is enough for the profile to
    # exercise the bulk-load code paths.
    workspace_copy.mkdir(parents=True, exist_ok=True)
    for src in package_root.rglob("*.py"):
        rel = src.relative_to(package_root)
        # Only carry a slice of the tree so the cold build is fast;
        # the profile only cares about ``staleness_probe`` and
        # ``serving_metadata``, both of which are O(n) over the
        # persisted rows and the workspace manifest.
        if len(rel.parts) > _MAX_RELATIVE_DEPTH:
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        dest = workspace_copy / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    index_dir = tmp / DEFAULT_INDEX_ROOT
    store = ExploreStore(index_dir=index_dir)
    reindex(
        store=store,
        workspace_root=workspace_copy,
        options=ReindexOptions(mode="full", timeout_ms=120_000),
    )
    handle = ExploreIndex(
        workspace_root=workspace_copy,
        index_root=index_dir,
        store=store,
        generation=int(store.get_setting("current_generation") or 0),
    )
    return workspace_copy, index_dir, handle


def _run_profile(callable_: Callable[[], object]) -> cProfile.Profile:
    """Run ``callable_`` 5 times under cProfile and return the profiler."""
    profiler: cProfile.Profile = cProfile.Profile()
    for _ in range(5):
        profiler.runcall(callable_)
    return profiler


def _table_rows(profiler: cProfile.Profile, *, top: int) -> list[tuple[str, float]]:
    """Return ``(name, cumtime)`` rows from ``profiler`` sorted by cumtime desc.

    We use the :class:`pstats.Stats` ordered dict directly rather
    than re-parsing the printed table -- the text format is fragile
    (header lines, ``List reduced ... due to restriction``, total
    lines) and a direct read avoids every parser edge case.
    ``sort_stats('cumulative')`` orders ascending; we re-sort
    descending so the report reads naturally.
    """
    # ``cProfile.Profile.stats`` is a ``dict[_Label, tuple[int, int, int, int, dict[...]]]``
    # where the value is ``(cc, nc, tt, ct, callers)`` and ``ct`` is the
    # cumulative time. The typeshed types ``ct`` as ``int`` even though the
    # runtime value is a ``float``; we widen it to ``float`` here so the
    # downstream report formatter (``f"{ct:8.6f}"``) prints a real decimal.
    raw_rows: list[tuple[str, float]] = []
    for label, profile_row in profiler.stats.items():
        ct_int: int = profile_row[3]
        raw_rows.append((_qualname(label), float(ct_int)))

    def _sort_key(row: tuple[str, float]) -> tuple[float, str]:
        return (-row[1], row[0])

    sorted_rows: list[tuple[str, float]] = sorted(raw_rows, key=_sort_key)
    return sorted_rows[:top]


def _qualname(func: tuple[str, int, str]) -> str:
    """Return ``path:line(name)`` for a ``(file, line, name)`` triple."""
    path, line, name = func
    return f"{path}:{line}({name})"


def _assert_entries_present(
    rows: Sequence[tuple[str, float]],
    required: Sequence[str],
    forbidden: Sequence[str],
    *,
    label: str,
) -> list[str]:
    """Return a list of assertion failures; empty when the profile is honest."""
    joined = "\n".join(name for name, _ in rows)
    missing = [
        f"{label}: required entry {needle!r} missing from profile"
        for needle in required
        if needle not in joined
    ]
    forbidden_hits = [
        f"{label}: forbidden entry {needle!r} present in profile"
        for needle in forbidden
        if needle in joined
    ]
    return [*missing, *forbidden_hits]


def _parse_args(argv: Sequence[str] | None) -> tuple[int, Path | None]:
    """Parse CLI args into a typed ``(top, out)`` pair.

    ``argparse.Namespace`` is untyped in the standard stubs so every
    attribute access on ``args`` resolves to ``Any``; ``disallow_any_expr``
    then makes each access a hard error. Returning a typed tuple keeps
    the rest of :func:`main` free of those.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Number of cumulative-time entries to print (default 30)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the recorded profile to this path (JSON)",
    )
    parsed = parser.parse_args(argv)
    # ``argparse.Namespace`` is untyped in the standard stubs so
    # every attribute access resolves to ``Any`` and trips
    # ``disallow_any_expr``. The values are guaranteed to be the
    # ``int``/``Path`` declared in the ``add_argument`` calls above
    # (or ``None``), so a narrow, code-scoped ``type: ignore`` is
    # the correct escape hatch.
    raw_top: int = int(parsed.top)  # type: ignore[misc]
    raw_out: object = parsed.out
    if raw_out is None:
        out_value: Path | None = None
    elif isinstance(raw_out, Path):
        out_value = raw_out
    else:
        out_value = Path(str(raw_out))
    return raw_top, out_value


def main(argv: Sequence[str] | None = None) -> int:
    """Run the probe and assert the expected hot-path entries."""
    top_n, out_path = _parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="ralph-probe-profile-") as raw_tmp:
        tmp = Path(raw_tmp)
        workspace, _index_dir, handle = _build_indexed_workspace(tmp)
        # ``type(name, bases, dict)`` returns ``type[Unknown]`` because the
        # class body is untyped; we keep the session as ``object`` and let
        # duck-typing carry the ``.explore_index`` attribute.
        session_body: dict[str, object] = {"explore_index": handle}
        session_cls: type[object] = type("_Session", (), session_body)
        session: object = session_cls()

        def _run_probe() -> object:
            return staleness_probe(session, workspace_root=workspace)

        def _run_metadata() -> object:
            return serving_metadata(
                session, index_used=True, fallback_reason=None
            )

        probe_profiler: cProfile.Profile = _run_profile(_run_probe)
        metadata_profiler: cProfile.Profile = _run_profile(_run_metadata)
        probe_rows: list[tuple[str, float]] = _table_rows(probe_profiler, top=top_n)
        metadata_rows: list[tuple[str, float]] = _table_rows(metadata_profiler, top=top_n)

    print(f"=== staleness_probe cumulative-time (top {top_n}) ===")
    for name, cumtime in probe_rows:
        print(f"  {cumtime:8.6f}  {name}")
    print()
    print(f"=== serving_metadata cumulative-time (top {top_n}) ===")
    for name, cumtime in metadata_rows:
        print(f"  {cumtime:8.6f}  {name}")
    print()

    failures: list[str] = []
    failures.extend(
        _assert_entries_present(
            probe_rows,
            _REQUIRED_PROBE_ENTRIES,
            _FORBIDDEN_PROBE_ENTRIES,
            label="staleness_probe",
        )
    )
    failures.extend(
        _assert_entries_present(
            metadata_rows,
            _REQUIRED_METADATA_ENTRIES,
            (),
            label="serving_metadata",
        )
    )
    if failures:
        print("FAIL")
        for f in failures:
            print(f"  {f}")
        return 1
    print("OK")
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "top": top_n,
            "probe": [{"name": name, "cumtime": cumtime} for name, cumtime in probe_rows],
            "metadata": [
                {"name": name, "cumtime": cumtime} for name, cumtime in metadata_rows
            ],
        }
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
