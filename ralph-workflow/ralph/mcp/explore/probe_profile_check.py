from __future__ import annotations

import argparse
import cProfile
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_bytes_if_changed, write_text_if_changed
from ralph.mcp.explore.handlers import ExploreIndex
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.serving import (
    serving_metadata,
    staleness_probe,
)
from ralph.mcp.explore.store import DEFAULT_INDEX_ROOT, ExploreStore

if TYPE_CHECKING:
    from collections.abc import Sequence


_REQUIRED_PROBE_ENTRIES: tuple[str, ...] = (
    "collect_workspace_files",
    "bulk_size_mtime_for_paths",
)
_FORBIDDEN_PROBE_ENTRIES: tuple[str, ...] = ("store.get_file",)
_REQUIRED_METADATA_ENTRIES: tuple[str, ...] = (
    "peek_dirty_paths",
    "count_deleted_files",
    "latest_job",
)
_MAX_RELATIVE_DEPTH: Final[int] = 4


def _build_indexed_workspace(tmp: Path) -> tuple[Path, Path, ExploreIndex]:
    package_root = Path(__file__).resolve().parents[3]
    workspace_copy = tmp / "workspace"
    workspace_copy.mkdir(parents=True, exist_ok=True)
    for src in package_root.rglob("*.py"):
        rel = src.relative_to(package_root)
        if len(rel.parts) > _MAX_RELATIVE_DEPTH:
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        dest = workspace_copy / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_if_changed(DEFAULT_FILE_BACKEND, dest, src.read_bytes())
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
    profiler: cProfile.Profile = cProfile.Profile()
    for _ in range(5):
        profiler.runcall(callable_)
    return profiler


def _table_rows(profiler: cProfile.Profile, *, top: int) -> list[tuple[str, float]]:
    raw_rows: list[tuple[str, float]] = []
    profiler.create_stats()
    for label, profile_row in profiler.stats.items():
        ct_int: int = profile_row[3]
        raw_rows.append((_qualname(label), float(ct_int)))

    def _sort_key(row: tuple[str, float]) -> tuple[float, str]:
        return (-row[1], row[0])

    sorted_rows: list[tuple[str, float]] = sorted(raw_rows, key=_sort_key)
    return sorted_rows[:top]


def _qualname(func: tuple[str, int, str]) -> str:
    path, line, name = func
    return f"{path}:{line}({name})"


def _assert_entries_present(
    rows: Sequence[tuple[str, float]],
    required: Sequence[str],
    forbidden: Sequence[str],
    *,
    label: str,
) -> list[str]:
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


class _ProfileArguments(argparse.Namespace):
    top: int
    out: Path | None


def _parse_args(argv: Sequence[str] | None) -> tuple[int, Path | None]:
    parser = argparse.ArgumentParser(description="Inspect the explore index probe profile.")
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
    parsed = _ProfileArguments()
    parser.parse_args(argv, namespace=parsed)
    return parsed.top, parsed.out


def main(argv: Sequence[str] | None = None) -> int:
    top_n, out_path = _parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="ralph-probe-profile-") as raw_tmp:
        tmp = Path(raw_tmp)
        workspace, _index_dir, handle = _build_indexed_workspace(tmp)
        session_body: dict[str, object] = {"explore_index": handle}
        session_cls: type[object] = type("_Session", (), session_body)
        session: object = session_cls()

        def _run_probe() -> object:
            return staleness_probe(session, workspace_root=workspace)

        def _run_metadata() -> object:
            return serving_metadata(session, index_used=True, fallback_reason=None)

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
            "metadata": [{"name": name, "cumtime": cumtime} for name, cumtime in metadata_rows],
        }
        write_text_if_changed(
            DEFAULT_FILE_BACKEND, out_path, json.dumps(payload, indent=2, sort_keys=True)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
