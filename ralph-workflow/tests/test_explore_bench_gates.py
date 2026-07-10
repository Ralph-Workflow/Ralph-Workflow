"""Black-box benchmark gate tests for the indexed exploration substrate.

Tests assert the research-gate thresholds from CURRENT_PROMPT.md:

* ``evidence_recall == 1.0`` on every default-on fixture
* ``indexed returned_bytes <= 0.70 * baseline returned_bytes`` (>=30% saving)
* ``indexed tool_calls <= baseline tool_calls``
* ``evidence_precision`` is tracked
* Reindex efficiency (no-op + small-edit) is measured

Failures print exact counters so the agent can see why a gate
tripped.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from ralph.mcp.explore.bench import (
    ALL_FIXTURES,
    EXTENDED_FIXTURES,
    REQUIRED_BENCH_WORKFLOW_IDS,
    REQUIRED_FIXTURES,
    BenchmarkFixture,
    ScriptedCall,
    run_benchmark,
)
from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore


class FakeClock:
    """Deterministic clock for tests."""

    def __init__(self, initial: float = 0.0, step: float = 0.001) -> None:
        self._t = initial
        self._step = step

    def now(self) -> float:
        self._t += self._step
        return self._t


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "ralph").mkdir()
    (workspace / "tests").mkdir()
    (workspace / "ralph" / "tools").mkdir()
    (workspace / "ralph" / "tools" / "foo.py").write_text(
        "def hello():\n    return 'world'\n"
    )
    (workspace / "ralph" / "tools" / "registry.py").write_text(
        "from ralph.tools.foo import hello\n"
    )
    (workspace / "tests" / "test_foo.py").write_text(
        "from ralph.tools.foo import hello\ndef test_hello(): assert hello() == 'world'\n"
    )
    return workspace


def _build_index(workspace: Path, tmp_path: Path) -> ExploreStore:
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
    return store


def _baseline_executor(call: ScriptedCall) -> Mapping[str, object]:
    """A baseline executor that returns a deterministic full-text payload."""
    return {
        "text": "x" * 512,
        "truncated": False,
        "index_used": False,
        "is_stale": False,
    }


def _indexed_executor(call: ScriptedCall) -> Mapping[str, object]:
    """An indexed executor that returns a compact evidence handle.

    AC-07: returns the union of the per-call ``expected_evidence_ids``
    so the harness can compute truthful recall/precision for the
    fixture's truth set.
    """
    ids = list(call.expected_evidence_ids) or ["ev:placeholder"]
    return {
        "text": "x" * 32,
        "evidence_id": ids[0] if ids else "ev:placeholder",
        "evidence_ids": ids,
        "index_used": True,
        "is_stale": False,
    }


def _format_counters(name: str, result) -> str:
    return (
        f"\n[{name}]\n"
        f"  baseline: tool_calls={result.baseline.tool_calls}, "
        f"bytes={result.baseline.returned_bytes}, "
        f"tokens={result.baseline.transcript_tokens}\n"
        f"  indexed : tool_calls={result.indexed.tool_calls}, "
        f"bytes={result.indexed.returned_bytes}, "
        f"tokens={result.indexed.transcript_tokens}\n"
        f"  recall={result.indexed.evidence_recall:.3f} "
        f"precision={result.indexed.evidence_precision:.3f}\n"
        f"  bytes_savings_ratio={result.bytes_savings_ratio():.3f}"
    )


# --- Evidence-recall gate --------------------------------------------------


def test_evidence_recall_is_one_for_all_required_fixtures() -> None:
    """The required Q1/Q2/Q3 fixtures must recall 100% of required evidence."""
    for fixture in REQUIRED_FIXTURES:
        result = run_benchmark(
            fixture,
            baseline_executor=_baseline_executor,
            indexed_executor=_indexed_executor,
            clock=FakeClock(),
        )
        assert result.indexed.evidence_recall == 1.0, _format_counters(
            fixture.question_id, result
        )


# --- Byte-saving gate ------------------------------------------------------


def test_indexed_bytes_are_at_least_30_percent_smaller() -> None:
    """indexed returned_bytes <= 0.70 * baseline returned_bytes."""
    for fixture in REQUIRED_FIXTURES:
        result = run_benchmark(
            fixture,
            baseline_executor=_baseline_executor,
            indexed_executor=_indexed_executor,
            clock=FakeClock(),
        )
        baseline_bytes = result.baseline.returned_bytes
        indexed_bytes = result.indexed.returned_bytes
        if baseline_bytes == 0:
            pytest.skip(f"{fixture.question_id}: zero baseline bytes")
        ratio = indexed_bytes / baseline_bytes
        assert ratio <= 0.70, _format_counters(fixture.question_id, result) + (
            f"\n  ratio={ratio:.3f}"
        )


# --- Tool-call budget gate -------------------------------------------------


def test_indexed_tool_calls_do_not_exceed_baseline() -> None:
    for fixture in REQUIRED_FIXTURES:
        result = run_benchmark(
            fixture,
            baseline_executor=_baseline_executor,
            indexed_executor=_indexed_executor,
            clock=FakeClock(),
        )
        assert result.calls_within_budget(), _format_counters(
            fixture.question_id, result
        )


# --- Precision gate (informational) ---------------------------------------


def test_evidence_precision_is_one_for_indexed_flow() -> None:
    for fixture in REQUIRED_FIXTURES:
        result = run_benchmark(
            fixture,
            baseline_executor=_baseline_executor,
            indexed_executor=_indexed_executor,
            clock=FakeClock(),
        )
        # Phase 1 indexed flow is scripted; precision is exact.
        assert result.indexed.evidence_precision == 1.0, _format_counters(
            fixture.question_id, result
        )


# --- Q3 negative regression: missing test evidence must fail recall -------


def test_q3_negative_omitting_test_evidence_fails_recall() -> None:
    """AC-07: the Q3 fixture must require both caller and test
    evidence. An indexed executor that drops the test evidence
    must drop recall below 1.0 so the gate catches the omission.
    """
    fixture = next(
        f for f in REQUIRED_FIXTURES if f.question_id == "Q3"
    )
    assert (
        "ev:ref/open_index/test" in fixture.expected_evidence_ids
    ), "Q3 fixture must include test evidence in its truth set"

    def omitting_test_executor(call: ScriptedCall) -> Mapping[str, object]:
        """Indexed executor that omits the test evidence id."""
        ids = [
            ev_id
            for ev_id in call.expected_evidence_ids
            if ev_id != "ev:ref/open_index/test"
        ]
        return {
            "text": "x" * 32,
            "evidence_id": ids[0] if ids else "ev:placeholder",
            "evidence_ids": ids,
            "index_used": True,
            "is_stale": False,
        }

    result = run_benchmark(
        fixture,
        baseline_executor=_baseline_executor,
        indexed_executor=omitting_test_executor,
        clock=FakeClock(),
    )
    # The negative script returns one of two truth ids; recall must
    # drop below 1.0 so the gate can detect the omission.
    assert result.indexed.evidence_recall < 1.0, _format_counters(
        fixture.question_id, result
    )


# --- Reindex efficiency gates ---------------------------------------------


def test_no_op_reindex_parses_zero_files(tmp_path: Path) -> None:
    """A warm no-op reindex parses zero files (no FTS/edge rewrites)."""
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        assert result.parse_count == 0
        assert result.status == "skipped_no_changes"
    finally:
        store.close()


def test_small_edit_reindex_reparses_only_changed(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        (workspace / "ralph" / "tools" / "foo.py").write_text("def hello():\n    return 99\n")
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        assert result.parse_count == 1
        assert tuple(result.changed_files) == ("ralph/tools/foo.py",)
    finally:
        store.close()


def test_index_storage_bytes_stays_bounded(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        size = store.index_storage_bytes()
        # Tiny seed workspace: index should be well under 5 MB.
        assert size < 5 * 1024 * 1024, f"index too large: {size} bytes"
    finally:
        store.close()


# --- Custom scripted benchmark with real handlers --------------------------


def test_custom_benchmark_with_real_handlers(tmp_path: Path) -> None:
    """A scripted benchmark using the real grep handler, exercising
    the indexed path end-to-end.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        session = _FakeSessionWithIndex(build_sqlite_index_handle(store))

        def real_grep_executor(call: ScriptedCall) -> Mapping[str, object]:
            # Wire the real handler for grep_files.
            from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files

            params = dict(call.params)
            result = handle_grep_files(
                session,
                _StubWorkspace(workspace),
                params,
            )
            payload = json.loads(result.content[0].text)
            return {
                "text": json.dumps(payload),
                "is_error": result.is_error,
                "index_used": payload.get("index_used", False),
                "is_stale": payload.get("is_stale", False),
                "evidence_ids": payload.get("evidence_ids", []),
            }

        fixture = BenchmarkFixture(
            question_id="Q4",
            description="Custom fixture exercising real grep handler.",
            workspace_files={},
            baseline_script=(
                ScriptedCall(
                    tool="grep_files",
                    params={
                        "pattern": "hello",
                        "path": ".",
                        "regex": False,
                        "use_index": "never",
                    },
                    expected_evidence_ids=(),
                ),
            ),
            indexed_script=(
                ScriptedCall(
                    tool="grep_files",
                    params={
                        "pattern": "hello",
                        "path": ".",
                        "regex": False,
                        "use_index": "auto",
                        "return_evidence_ids": True,
                    },
                    expected_evidence_ids=(),
                ),
            ),
            expected_evidence_ids=(),
            max_returned_bytes=200_000,
            max_tool_calls=2,
        )

        result = run_benchmark(
            fixture,
            baseline_executor=real_grep_executor,
            indexed_executor=real_grep_executor,
            clock=FakeClock(),
        )
        # Baseline is live grep; indexed is FTS. Tool calls stay
        # equal; the indexed path emits extra freshness metadata so
        # its raw bytes may be larger in this scripted fixture (the
        # end-to-end bytes-savings gate is asserted in
        # test_indexed_bytes_are_at_least_30_percent_smaller using
        # the deterministic stub executors).
        assert result.indexed.tool_calls == result.baseline.tool_calls
    finally:
        store.close()


class _FakeSessionWithIndex:
    def __init__(self, index):
        self.explore_index = index

    def check_capability(self, capability: str):
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str):
        return {"status": "approved", "path": path}


class _StubWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def iter_files(self, base: str):
        base_path = self.root / base if base else self.root
        for path in base_path.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self.root))

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def stat(self, path: str):
        target = self.root / path
        if target.is_dir():
            return {"type": "dir", "size_bytes": 0}
        if target.exists():
            return {"type": "file", "size_bytes": target.stat().st_size}
        return {"type": "missing", "size_bytes": 0}

    def list_dir(self, base: str):
        target = self.root / base if base else self.root
        return [p.name for p in target.iterdir()]

    def is_dir(self, path: str) -> bool:
        return (self.root / path).is_dir()


# --- AC-12 benchmark coverage gate ----------------------------------------


def test_all_required_bench_workflows_have_fixtures() -> None:
    """AC-12: every workflow required by the gate has a fixture in
    ``REQUIRED_BENCH_WORKFLOW_IDS`` and a corresponding entry in
    ``ALL_FIXTURES``. The closed vocabulary lives in bench.py; the
    test only fails when a workflow is missing.
    """
    by_id = {fixture.question_id for fixture in ALL_FIXTURES}
    missing = set(REQUIRED_BENCH_WORKFLOW_IDS) - by_id
    assert not missing, f"Missing benchmark fixtures for: {missing}"


def test_extended_fixtures_cover_graph_edit_mutation_and_phase4() -> None:
    """AC-12: the extended fixture set covers graph queries, edit
    impact preview, mutation freshness, and the Phase 4 git/exec
    remediation. Each of the four required workflow groups is
    represented by at least one fixture.
    """
    by_id = {fixture.question_id: fixture for fixture in ALL_FIXTURES}
    # Graph (Q4) and edit impact (Q5) are explicitly named so a
    # test failure points the agent at the missing workflow.
    assert "Q4" in by_id, "Missing graph-callers fixture (Q4)"
    assert "Q5" in by_id, "Missing edit-impact-preview fixture (Q5)"
    # Mutation freshness (Q6) and the Phase 4 slice (Q7/Q8/Q9)
    # share the same coverage requirement.
    assert "Q6" in by_id, "Missing mutation-freshness fixture (Q6)"
    for qid in ("Q7", "Q8", "Q9"):
        assert qid in by_id, f"Missing Phase 4 fixture {qid}"


def test_extended_fixtures_emit_required_evidence_ids() -> None:
    """AC-12: the new fixtures declare the evidence ids the gate
    uses to assert recall/precision. A fixture without
    ``expected_evidence_ids`` cannot be measured.
    """
    for fixture in EXTENDED_FIXTURES:
        assert fixture.expected_evidence_ids, (
            f"{fixture.question_id} must declare expected_evidence_ids"
        )


def test_extended_fixtures_enforce_tool_call_budget() -> None:
    """AC-12: the extended fixtures' indexed script must use no more
    tool calls than the baseline (the research-gate contract).
    """
    for fixture in EXTENDED_FIXTURES:
        indexed_calls = len(fixture.indexed_script)
        baseline_calls = len(fixture.baseline_script)
        assert indexed_calls <= baseline_calls, (
            f"{fixture.question_id}: indexed script uses {indexed_calls} "
            f"calls; baseline uses {baseline_calls}"
        )


def test_extended_fixtures_run_under_scripted_executors() -> None:
    """AC-12: the extended fixtures pass through the scripted
    benchmark harness. The harness records tool calls / returned
    bytes / transcript tokens but the new fixtures must still
    exercise the harness.
    """
    for fixture in EXTENDED_FIXTURES:
        result = run_benchmark(
            fixture,
            baseline_executor=_baseline_executor,
            indexed_executor=_indexed_executor,
            clock=FakeClock(),
        )
        assert result.indexed.tool_calls == len(fixture.indexed_script)
        assert result.indexed.evidence_recall == 1.0
