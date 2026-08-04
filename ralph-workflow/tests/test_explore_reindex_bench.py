"""Focused black-box tests for the reindex bench CLI and the
typed Python structure-extractor seam.

The tests exercise four contracts:

* correctness \u2014 the scalar and accelerated extractors emit
  byte-for-byte identical rows for empty, small, non-ASCII, and
  representative Python sources.
* selection \u2014 ``select_structure_extractor`` honors the named
  selector, rejects unknown names, and threads the
  ``ast_node_count`` hint through the ``auto`` crossover.
* CLI parsing \u2014 ``reindex_bench`` accepts both space- and
  comma-separated ``--compare`` lists and rejects unknown
  implementation names.
* snapshot parity \u2014 the ``_index_snapshot`` helper returns
  identical rows for scalar and accelerated runs of the same
  workspace, so the end-to-end mode can fail closed when the
  two implementations diverge.

The tests deliberately avoid any wall-clock assertion: real
performance measurements live in the standalone CLI to keep
``audit_test_policy`` happy.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ralph.mcp.explore._structure_extractor import (
    DEFAULT_CROSSOVER_NODES,
    IMPL_ACCELERATED,
    IMPL_AUTO,
    IMPL_SCALAR,
    select_structure_extractor,
    set_runtime_crossover,
    structure_extractor_name,
)
from ralph.mcp.explore.reindex_bench import (
    _index_snapshot,
    _parse_implementations,
    _seeded_workspace,
)
from ralph.mcp.explore.structure import (
    extract_python,
    extract_python_accelerated,
    extract_python_scalar,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- Correctness: scalar and accelerated must agree byte-for-byte --------


def _row_id(row: object) -> str:
    """Return the deterministic id of any row-like dataclass."""
    for attr in ("span_id", "symbol_id", "edge_id"):
        value = getattr(row, attr, None)
        if isinstance(value, str):
            return value
    return ""


def _row_sort_key(row: object) -> tuple[str, str, str, str, str]:
    """Stable key that covers spans, symbols, and edges uniformly."""
    return (
        getattr(row, "kind", ""),
        getattr(row, "relation", ""),
        getattr(row, "name", ""),
        getattr(row, "qualified_name", ""),
        getattr(row, "source_id", ""),
    )


def _id_sets(rows: tuple[object, ...]) -> tuple[set[str], list[tuple[str, ...]]]:
    """Return ``(set_of_ids, sorted_tuples)`` for a row sequence."""
    ids: set[str] = set()
    tuples: list[tuple[str, ...]] = []
    for row in rows:
        rid = _row_id(row)
        ids.add(rid)
        tuples.append(
            (
                rid,
                getattr(row, "kind", ""),
                getattr(row, "relation", ""),
                getattr(row, "name", ""),
                getattr(row, "qualified_name", ""),
                getattr(row, "source_id", ""),
                getattr(row, "target_id", ""),
            )
        )
    tuples.sort()
    return ids, tuples


def _common_source() -> str:
    return (
        "import os\n"
        "import sys\n"
        "from typing import Any\n"
        "\n"
        "class Base:\n"
        "    pass\n"
        "\n"
        "class Foo(Base):\n"
        "    def bar(self, x):\n"
        "        return os.path.join('a', str(x))\n"
        "\n"
        "def hello():\n"
        "    return Foo()\n"
        "\n"
        "def test_smoke():\n"
        "    # references hello for documentation.\n"
        "    return hello()\n"
    )


def test_scalar_and_accelerated_emit_identical_rows_for_common_source() -> None:
    """Both extractors produce the same span / symbol / edge sets."""
    scalar = extract_python_scalar(
        path="m.py", content=_common_source(), content_hash="h", generation=1
    )
    accel = extract_python_accelerated(
        path="m.py", content=_common_source(), content_hash="h", generation=1
    )
    scalar_ids, scalar_tuples = _id_sets(scalar.spans)
    accel_ids, accel_tuples = _id_sets(accel.spans)
    assert scalar_ids == accel_ids
    assert scalar_tuples == accel_tuples
    scalar_ids, scalar_tuples = _id_sets(scalar.symbols)
    accel_ids, accel_tuples = _id_sets(accel.symbols)
    assert scalar_ids == accel_ids
    assert scalar_tuples == accel_tuples
    scalar_ids, scalar_tuples = _id_sets(scalar.edges)
    accel_ids, accel_tuples = _id_sets(accel.edges)
    assert scalar_ids == accel_ids
    assert scalar_tuples == accel_tuples


def test_scalar_and_accelerated_agree_on_empty_and_non_ascii_inputs() -> None:
    """Edge inputs (empty, non-ASCII) must produce identical rows."""
    cases: tuple[tuple[str, str], ...] = (
        ("empty.py", ""),
        ("short.py", "x = 1\n"),
        # Non-ASCII identifier + non-ASCII string literal.
        ("unicode.py", "d\u00e9f = '\u00e9'\n\u4e2d\u6587 = '\u4e2d'\n"),
        ("nested.py", "def a():\n    def b():\n        return b()\n    return b\n"),
    )
    for path, content in cases:
        scalar = extract_python_scalar(
            path=path, content=content, content_hash="h", generation=1
        )
        accel = extract_python_accelerated(
            path=path, content=content, content_hash="h", generation=1
        )
        assert sorted(_row_id(r) for r in scalar.spans) == sorted(
            _row_id(r) for r in accel.spans
        )
        assert sorted(_row_id(r) for r in scalar.symbols) == sorted(
            _row_id(r) for r in accel.symbols
        )
        assert sorted(_row_id(r) for r in scalar.edges) == sorted(
            _row_id(r) for r in accel.edges
        )


def test_dispatcher_selects_accelerated_when_requested() -> None:
    """``extract_python(structure_extractor='accelerated')`` returns accelerated."""
    result = extract_python(
        path="m.py",
        content=_common_source(),
        content_hash="h",
        generation=1,
        structure_extractor=IMPL_ACCELERATED,
    )
    accel = extract_python_accelerated(
        path="m.py", content=_common_source(), content_hash="h", generation=1
    )
    assert sorted(_row_id(r) for r in result.edges) == sorted(
        _row_id(r) for r in accel.edges
    )


def test_dispatcher_selects_scalar_by_default_and_explicitly() -> None:
    """The default ``structure_extractor`` is the scalar reference path."""
    default = extract_python(
        path="m.py", content=_common_source(), content_hash="h", generation=1
    )
    explicit = extract_python(
        path="m.py",
        content=_common_source(),
        content_hash="h",
        generation=1,
        structure_extractor=IMPL_SCALAR,
    )
    scalar = extract_python_scalar(
        path="m.py", content=_common_source(), content_hash="h", generation=1
    )
    for rows, expected in (
        (default.spans, scalar.spans),
        (explicit.spans, scalar.spans),
        (default.edges, scalar.edges),
        (explicit.edges, scalar.edges),
    ):
        assert sorted(_row_id(r) for r in rows) == sorted(
            _row_id(r) for r in expected
        )


# --- Selection: the seam must honor the named selector ------------------


def test_select_structure_extractor_returns_named_implementation() -> None:
    scalar = select_structure_extractor(IMPL_SCALAR)
    accelerated = select_structure_extractor(IMPL_ACCELERATED)
    assert structure_extractor_name(scalar) == IMPL_SCALAR
    assert structure_extractor_name(accelerated) == IMPL_ACCELERATED
    # And they are distinct callables with the right contracts.
    assert scalar is not accelerated


def test_select_structure_extractor_rejects_unknown_name() -> None:
    try:
        select_structure_extractor("unknown")
    except ValueError as exc:
        assert "unknown structure extractor" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown extractor")


def test_auto_selector_threads_node_count_and_crossover() -> None:
    """``auto`` returns scalar below the crossover, accelerated at/above it."""
    try:
        # Below default crossover (200) -> scalar.
        set_runtime_crossover(DEFAULT_CROSSOVER_NODES)
        low = structure_extractor_name(
            select_structure_extractor(IMPL_AUTO, ast_node_count=10)
        )
        high = structure_extractor_name(
            select_structure_extractor(IMPL_AUTO, ast_node_count=5000)
        )
        assert low == IMPL_SCALAR
        assert high == IMPL_ACCELERATED

        # Raising the crossover past the node count flips ``high``.
        set_runtime_crossover(10_000)
        still_low = structure_extractor_name(
            select_structure_extractor(IMPL_AUTO, ast_node_count=5000)
        )
        assert still_low == IMPL_SCALAR
    finally:
        set_runtime_crossover(None)
    # Default crossover is restored after the test.
    restored = structure_extractor_name(
        select_structure_extractor(IMPL_AUTO, ast_node_count=10)
    )
    assert restored == IMPL_SCALAR


# --- CLI parsing: --compare accepts space- and comma-separated lists -----


def test_parse_implementations_accepts_comma_and_space_lists() -> None:
    assert _parse_implementations("scalar,accelerated") == ["scalar", "accelerated"]
    assert _parse_implementations("scalar accelerated") == ["scalar", "accelerated"]
    assert _parse_implementations("scalar, accelerated auto") == [
        "scalar",
        "accelerated",
        "auto",
    ]


def test_parse_implementations_rejects_unknown_name() -> None:
    try:
        _parse_implementations("scalar,nope")
    except SystemExit:
        # argparse exits on type-validation failure.
        return
    except Exception as exc:
        # argparse raises ArgumentTypeError which becomes SystemExit.
        assert "unknown implementation" in str(exc)
        return
    raise AssertionError("expected argparse to reject unknown implementation")


# --- Snapshot parity: scalar / accelerated produce identical store rows --


def test_snapshot_helper_returns_byte_equal_rows(tmp_path: Path) -> None:
    """The end-to-end snapshot helper agrees across implementations."""
    # Use a small corpus so the test stays well inside the
    # 60-second combined budget; snapshot equality holds for any
    # size because the helpers do not measure performance.
    workspace, _ = _seeded_workspace(
        files=4,
        lines_per_file=8,
        parent=tmp_path,
    )
    snapshot: dict[str, dict[str, object]] = {}
    for name in (IMPL_SCALAR, IMPL_ACCELERATED, IMPL_AUTO):
        snapshot[name] = _index_snapshot(
            workspace=workspace,
            parent=tmp_path,
            structure_extractor=name,
            mode="full",
            timeout_ms=10_000,
        )
    scalar_snap = snapshot[IMPL_SCALAR]
    for name in (IMPL_ACCELERATED, IMPL_AUTO):
        other = snapshot[name]
        assert other["generation"] == scalar_snap["generation"]
        assert other["changed_files"] == scalar_snap["changed_files"]
        assert other["spans"] == scalar_snap["spans"]
        assert other["symbols"] == scalar_snap["symbols"]
        assert other["edges"] == scalar_snap["edges"]


# --- CLI smoke: the binary must accept the documented flags --------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ralph.mcp.explore.reindex_bench", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_cli_exposes_compare_small_workload_and_end_to_end_flags() -> None:
    """The CLI must surface the plan-mandated ``--compare`` / ``--small-workload`` / ``--end-to-end`` flags."""
    result = _run_cli("--help")
    assert "--compare" in result.stdout
    assert "--small-workload" in result.stdout
    assert "--end-to-end" in result.stdout
    assert "--quick" in result.stdout


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(15)
def test_cli_compare_emits_machine_readable_summary() -> None:
    """``--compare scalar accelerated`` exits 0 and prints valid JSON."""
    result = _run_cli("--compare", "scalar", "accelerated", "--samples", "2", "--quick")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["all_passed"] is True
    workloads = payload["workloads"]
    assert len(workloads) == 1
    workload = workloads[0]
    assert "scalar" in workload
    assert "accelerated" in workload
    assert "scalar_vs_accelerated_speedup" in workload
    assert "metadata" in payload
    assert "platform" in payload["metadata"]


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_cli_compare_rejects_unknown_implementation() -> None:
    """Unknown names fail closed with a nonzero exit status."""
    result = _run_cli("--compare", "scalar", "unknown")
    assert result.returncode != 0
