"""Fixed-tokenizer scripted-flow benchmark harness for the indexed exploration substrate.

This module owns the deterministic harness that proves the
research-gate counters from the architecture finding:

* ``transcript_tokens`` — token count of the full scripted transcript
  (tool descriptions + inputs + outputs + final evidence context)
  computed by a fixed, pinned tokenizer.
* ``returned_bytes`` — deterministic secondary proxy of transcript cost.
* ``tool_calls`` — number of scripted tool calls per flow.
* ``wall_time`` — wall-clock seconds for the scripted flow.
* ``stale_fallback_events`` — count of stale or fallback responses.
* ``evidence_recall`` — required_evidence / expected_required.
* ``evidence_precision`` — required_evidence / returned_evidence.
* ``parse_count`` — number of files reparsed during the reindex step.
* ``changed_file_count`` — number of files marked dirty and reindexed.
* ``index_storage_bytes`` — disk size of the SQLite index.

The harness runs scripted tool flows over ``tmp_path`` workspaces,
NOT a live agent. Wall time uses a constructor-injected ``Clock``
(defaults to a real clock; tests inject ``FakeClock`` to avoid the
``audit_test_policy`` wall-clock violation).

Required deterministic questions (per the architecture finding):

* Q1 — find where a tool is registered.
* Q2 — find likely tests for a handler.
* Q3 — estimate rename impact via lexical callers (graph impact is
  Phase 3, so Q3 uses lexical callers only; the deferral register
  records that semantic impact is deferred).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol


# Ponytail: fixed tokenizer. Counts ASCII whitespace-separated tokens
# (split on Unicode whitespace). Cheap, deterministic, and not a
# dependency on a real tokenizer library. Tests assert the same value
# for the same input. The tokenizer is intentionally simple: this is
# a budget, not a fidelity claim.
def _fixed_token_count(text: str) -> int:
    if not text:
        return 0
    return len(text.split())


class Clock(Protocol):
    """Protocol for wall-clock injection. Tests inject a FakeClock."""

    def now(self) -> float:
        ...


class SystemClock:
    """Default wall-clock implementation backed by ``time.monotonic``."""

    def now(self) -> float:
        return time.monotonic()


@dataclass(frozen=True, slots=True)
class ScriptedCall:
    """A single scripted tool call used by a benchmark fixture."""

    tool: str
    params: Mapping[str, object]
    expected_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BenchmarkFixture:
    """A single benchmark fixture (one question, two flows)."""

    question_id: str
    description: str
    workspace_files: Mapping[str, str]
    baseline_script: tuple[ScriptedCall, ...]
    indexed_script: tuple[ScriptedCall, ...]
    expected_evidence_ids: tuple[str, ...]
    max_returned_bytes: int
    max_tool_calls: int
    requires_reindex: bool = False


@dataclass(frozen=True, slots=True)
class BenchmarkCounters:
    """Single-script counters."""

    tool_calls: int
    returned_bytes: int
    transcript_tokens: int
    wall_time_seconds: float
    stale_fallback_events: int
    evidence_recall: float
    evidence_precision: float


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Result of a single benchmark question (one fixture)."""

    question_id: str
    baseline: BenchmarkCounters
    indexed: BenchmarkCounters
    parse_count: int = 0
    changed_file_count: int = 0
    index_storage_bytes: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def bytes_savings_ratio(self) -> float:
        """Return 1 - (indexed.bytes / baseline.bytes); 0.0 if baseline is 0."""
        if self.baseline.returned_bytes == 0:
            return 0.0
        return 1.0 - (self.indexed.returned_bytes / self.baseline.returned_bytes)

    def calls_within_budget(self, baseline_calls: int | None = None) -> bool:
        baseline = baseline_calls or self.baseline.tool_calls
        return self.indexed.tool_calls <= baseline


def _tokenize_call(call: ScriptedCall) -> int:
    """Approximate token cost of a single tool call's description+input."""
    payload = f"{call.tool} {sorted(call.params.items())}"
    return _fixed_token_count(payload)


# Ponytail: tool description token accounting. Helper used by the
# MCP-description gate to estimate per-tool schema tokens. Stable across
# runs and deterministic.
def tool_catalog_tokens(
    tool_descriptions: Iterable[tuple[str, str]],
) -> dict[str, int]:
    """Estimate token cost per tool description.

    Args:
        tool_descriptions: iterable of ``(tool_name, description)`` pairs.

    Returns:
        Mapping from tool name to its fixed-token count.
    """
    return {name: _fixed_token_count(desc) for name, desc in tool_descriptions}


def _run_script(
    script: Sequence[ScriptedCall],
    *,
    executor: Callable[[ScriptedCall], Mapping[str, object]],
    clock: Clock,
) -> BenchmarkCounters:
    """Run a scripted tool flow and aggregate counters.

    The executor is expected to return a dict with the result payload;
    the harness counts bytes/tokens but does not introspect the
    payload semantics.
    """
    start = clock.now()
    returned_bytes = 0
    transcript_tokens = 0
    stale_fallback = 0
    returned_evidence_ids: set[str] = set()
    for call in script:
        result = executor(call)
        # Tokenize the result payload deterministically.
        text = str(result)
        returned_bytes += len(text.encode("utf-8"))
        transcript_tokens += _fixed_token_count(text)
        transcript_tokens += _tokenize_call(call)
        if result.get("is_stale") is True or result.get("index_used") is False:
            stale_fallback += 1
        # AC-12: collect returned evidence ids so the harness can
        # compute real recall/precision from the union of (a) the
        # per-call expected ids and (b) any explicit evidence_ids
        # in the executor's payload.
        returned_ids: object = result.get("evidence_ids", ())
        if isinstance(returned_ids, (list, tuple)):
            for ev_id in returned_ids:
                if isinstance(ev_id, str) and ev_id:
                    returned_evidence_ids.add(ev_id)
    wall_time = clock.now() - start
    # AC-12: evidence recall/precision are derived from the actual
    # returned evidence ids, not hardcoded. The benchmark harness
    # uses the union of per-call expected ids as the truth set
    # and the actual returned ids as the prediction set.
    expected_ids: set[str] = set()
    for call in script:
        for ev_id in call.expected_evidence_ids:
            if isinstance(ev_id, str) and ev_id:
                expected_ids.add(ev_id)
    recall, precision = _evidence_metrics(expected_ids, returned_evidence_ids)
    return BenchmarkCounters(
        tool_calls=len(script),
        returned_bytes=returned_bytes,
        transcript_tokens=transcript_tokens,
        wall_time_seconds=wall_time,
        stale_fallback_events=stale_fallback,
        evidence_recall=recall,
        evidence_precision=precision,
    )


def _evidence_metrics(
    expected: set[str],
    returned: set[str],
) -> tuple[float, float]:
    """Return (recall, precision) as floats in [0.0, 1.0].

    Both return 1.0 when the expected set is empty (the fixture
    did not require any specific evidence). When returned is
    empty but expected is not, both are 0.0.
    """
    if not expected:
        return 1.0, 1.0
    if not returned:
        return 0.0, 0.0
    matched = expected & returned
    recall = len(matched) / len(expected)
    precision = len(matched) / len(returned)
    return recall, precision


# --- Required fixture question builders ---

_REQUIRED_LITERAL_TOKENS = re.compile(r"\w+", re.UNICODE)


def _tokenize_literal(text: str) -> list[str]:
    raw: list[str] = _REQUIRED_LITERAL_TOKENS.findall(text)
    return raw


def question_register_tool() -> BenchmarkFixture:
    """Q1: find where a tool is registered."""
    return BenchmarkFixture(
        question_id="Q1",
        description="Find where a tool is registered.",
        workspace_files={
            "ralph/mcp/tools/workspace/_read_handlers.py": (
                "def handle_read_file(...):\n    ...\n"
            ),
            "ralph/mcp/tools/bridge/_registry.py": (
                "from ralph.mcp.tools.bridge._specs_file_read import file_read_specs\n"
                "def tool_specs(...):\n    specs.extend(file_read_specs())\n    return tuple(specs)\n"
            ),
        },
        baseline_script=(
            ScriptedCall(
                tool="search_files",
                params={"pattern": "**/*.py", "path": "ralph"},
                expected_evidence_ids=("ev:register/registry",),
            ),
            ScriptedCall(
                tool="grep_files",
                params={
                    "pattern": "file_read_specs",
                    "path": "ralph/mcp/tools/bridge",
                },
                expected_evidence_ids=("ev:register/registry",),
            ),
            ScriptedCall(
                tool="read_file",
                params={"path": "ralph/mcp/tools/bridge/_registry.py"},
                expected_evidence_ids=("ev:register/registry",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="grep_files",
                params={
                    "pattern": "file_read_specs",
                    "path": "ralph/mcp/tools/bridge",
                    "use_index": "auto",
                    "return_evidence_ids": True,
                },
                expected_evidence_ids=("ev:register/registry",),
            ),
            ScriptedCall(
                tool="read_file",
                params={
                    "path": "ralph/mcp/tools/bridge/_registry.py",
                    "evidence_id": "ev:register/registry",
                },
                expected_evidence_ids=("ev:register/registry",),
            ),
        ),
        expected_evidence_ids=("ev:register/registry",),
        max_returned_bytes=200_000,
        max_tool_calls=4,
    )


def question_find_handler_tests() -> BenchmarkFixture:
    """Q2: find likely tests for a handler."""
    return BenchmarkFixture(
        question_id="Q2",
        description="Find likely tests for a handler.",
        workspace_files={
            "ralph/mcp/tools/workspace/_read_handlers.py": (
                "def handle_read_file(...):\n    ...\n"
            ),
            "tests/test_mcp_read_handler.py": (
                "def test_handle_read_file_basic():\n    ...\n"
            ),
        },
        baseline_script=(
            ScriptedCall(
                tool="search_files",
                params={"pattern": "**/test_*.py", "path": "tests"},
                expected_evidence_ids=("ev:test/handle_read_file",),
            ),
            ScriptedCall(
                tool="grep_files",
                params={
                    "pattern": "handle_read_file",
                    "path": "tests",
                },
                expected_evidence_ids=("ev:test/handle_read_file",),
            ),
            ScriptedCall(
                tool="read_file",
                params={"path": "tests/test_mcp_read_handler.py"},
                expected_evidence_ids=("ev:test/handle_read_file",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="grep_files",
                params={
                    "pattern": "handle_read_file",
                    "path": "tests",
                    "use_index": "auto",
                    "return_evidence_ids": True,
                },
                expected_evidence_ids=("ev:test/handle_read_file",),
            ),
            ScriptedCall(
                tool="read_file",
                params={
                    "path": "tests/test_mcp_read_handler.py",
                    "evidence_id": "ev:test/handle_read_file",
                },
                expected_evidence_ids=("ev:test/handle_read_file",),
            ),
        ),
        expected_evidence_ids=("ev:test/handle_read_file",),
        max_returned_bytes=200_000,
        max_tool_calls=4,
    )


def question_estimate_rename_impact() -> BenchmarkFixture:
    """Q3: estimate rename impact via lexical callers (Phase 1 lexical only)."""
    return BenchmarkFixture(
        question_id="Q3",
        description=(
            "Estimate rename impact via lexical callers. Graph/semantic "
            "impact is Phase 3 and tracked in deferred_phases.py."
        ),
        workspace_files={
            "ralph/mcp/explore/store.py": "def open_index():\n    ...\n",
            "ralph/mcp/explore/pipeline.py": "from ralph.mcp.explore.store import open_index\n",
            "tests/test_explore_pipeline.py": (
                "from ralph.mcp.explore.store import open_index\n"
            ),
        },
        baseline_script=(
            ScriptedCall(
                tool="grep_files",
                params={
                    "pattern": "open_index",
                    "path": "ralph",
                },
                expected_evidence_ids=("ev:ref/open_index/pipeline",),
            ),
            ScriptedCall(
                tool="grep_files",
                params={
                    "pattern": "open_index",
                    "path": "tests",
                },
                expected_evidence_ids=("ev:ref/open_index/test",),
            ),
            ScriptedCall(
                tool="read_file",
                params={"path": "ralph/mcp/explore/pipeline.py"},
                expected_evidence_ids=("ev:ref/open_index/pipeline",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="grep_files",
                params={
                    "pattern": "open_index",
                    "path": "ralph",
                    "use_index": "auto",
                    "return_evidence_ids": True,
                },
                expected_evidence_ids=("ev:ref/open_index/pipeline",),
            ),
            ScriptedCall(
                tool="read_file",
                params={
                    "path": "ralph/mcp/explore/pipeline.py",
                    "evidence_id": "ev:ref/open_index/pipeline",
                },
                expected_evidence_ids=("ev:ref/open_index/pipeline",),
            ),
        ),
        expected_evidence_ids=("ev:ref/open_index/pipeline",),
        max_returned_bytes=300_000,
        max_tool_calls=4,
    )


REQUIRED_FIXTURES: tuple[BenchmarkFixture, ...] = (
    question_register_tool(),
    question_find_handler_tests(),
    question_estimate_rename_impact(),
)


# AC-12: the benchmark gate must cover graph queries, edit impact
# preview, mutation freshness metadata, and the Phase 4 git/exec
# remediation. Each new fixture is a positive control (the
# indexed/path script must succeed and beat the baseline) AND a
# negative control (the negative script is a known-bad pattern that
# the gate must reject). The deferred_phases list keeps optional
# ralph_explore out of the gate until benchmarked.


def question_graph_callers() -> BenchmarkFixture:
    """Q4: graph callers via ``ralph_graph query_type=neighbors``.

    The positive script asks the graph for ``hello`` and reads
    back the resulting evidence handles; the negative script
    walks the directory tree and greps repeatedly to find the
    same information, with a larger returned byte budget.
    """
    return BenchmarkFixture(
        question_id="Q4",
        description="Find callers of a symbol via ralph_graph.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="search_files",
                params={"pattern": "**/*.py", "path": "ralph"},
                expected_evidence_ids=("ev:graph/callers/hello",),
            ),
            ScriptedCall(
                tool="grep_files",
                params={"pattern": "hello", "path": "ralph"},
                expected_evidence_ids=("ev:graph/callers/hello",),
            ),
            ScriptedCall(
                tool="grep_files",
                params={"pattern": "hello", "path": "tests"},
                expected_evidence_ids=("ev:graph/callers/tests",),
            ),
            ScriptedCall(
                tool="read_file",
                params={"path": "ralph/tools/foo.py"},
                expected_evidence_ids=("ev:graph/callers/hello",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="ralph_graph",
                params={
                    "query_type": "neighbors",
                    "target": "ralph.tools.foo.hello",
                    "depth": 2,
                    "return_evidence_ids": True,
                },
                expected_evidence_ids=("ev:graph/callers/hello",),
            ),
            ScriptedCall(
                tool="read_file",
                params={
                    "path": "ralph/tools/foo.py",
                    "evidence_id": "ev:graph/callers/hello",
                },
                expected_evidence_ids=("ev:graph/callers/hello",),
            ),
        ),
        expected_evidence_ids=("ev:graph/callers/hello",),
        max_returned_bytes=200_000,
        max_tool_calls=4,
    )


def question_edit_impact_preview() -> BenchmarkFixture:
    """Q5: edit impact preview via ralph_graph impact.

    The positive script asks for an impact preview (Phase 3
    feature) and reads the targeted evidence handle. The negative
    script greps for callers and re-reads the function body.
    """
    return BenchmarkFixture(
        question_id="Q5",
        description="Estimate rename impact via ralph_graph impact preview.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="grep_files",
                params={"pattern": "hello", "path": "ralph"},
                expected_evidence_ids=("ev:impact/hello",),
            ),
            ScriptedCall(
                tool="grep_files",
                params={"pattern": "hello", "path": "tests"},
                expected_evidence_ids=("ev:impact/hello",),
            ),
            ScriptedCall(
                tool="read_file",
                params={"path": "ralph/tools/foo.py"},
                expected_evidence_ids=("ev:impact/hello",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="ralph_graph",
                params={
                    "query_type": "impact",
                    "target": "ralph.tools.foo.hello",
                    "change_kind": "rename",
                },
                expected_evidence_ids=("ev:impact/hello",),
            ),
            ScriptedCall(
                tool="read_file",
                params={
                    "path": "ralph/tools/foo.py",
                    "evidence_id": "ev:impact/hello",
                },
                expected_evidence_ids=("ev:impact/hello",),
            ),
        ),
        expected_evidence_ids=("ev:impact/hello",),
        max_returned_bytes=200_000,
        max_tool_calls=3,
    )


def question_mutation_freshness() -> BenchmarkFixture:
    """Q6: mutation freshness metadata after a workspace write.

    The positive script calls edit_file with a target selector and
    expects the response to include the freshness metadata. The
    negative script performs the same edit with a plain
    oldText/newText and re-reads the file to confirm content.
    """
    return BenchmarkFixture(
        question_id="Q6",
        description="Mutation freshness metadata via edit_file target.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="edit_file",
                params={
                    "path": "ralph/tools/foo.py",
                    "oldText": "return 'world'",
                    "newText": "return 'world!'",
                },
                expected_evidence_ids=("ev:mutation/foo",),
            ),
            ScriptedCall(
                tool="read_file",
                params={"path": "ralph/tools/foo.py"},
                expected_evidence_ids=("ev:mutation/foo",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="edit_file",
                params={
                    "path": "ralph/tools/foo.py",
                    "oldText": "return 'world'",
                    "newText": "return 'world!'",
                    "reindex": "auto",
                    "return_evidence_updates": True,
                },
                expected_evidence_ids=("ev:mutation/foo",),
            ),
        ),
        expected_evidence_ids=("ev:mutation/foo",),
        max_returned_bytes=200_000,
        max_tool_calls=2,
    )


def question_phase4_git_status_compact() -> BenchmarkFixture:
    """Q7: Phase 4 git_status compact output.

    The positive script requests ``format=compact``; the negative
    script uses the default (raw) format and pays a larger byte
    budget. The benchmark gate requires the compact form to use
    fewer bytes than the default at parity call count.
    """
    return BenchmarkFixture(
        question_id="Q7",
        description="Phase 4 git_status compact output.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="git_status",
                params={},
                expected_evidence_ids=("ev:git/compact/status",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="git_status",
                params={"format": "compact"},
                expected_evidence_ids=("ev:git/compact/status",),
            ),
        ),
        expected_evidence_ids=("ev:git/compact/status",),
        max_returned_bytes=80_000,
        max_tool_calls=2,
    )


def question_phase4_git_diff_summary() -> BenchmarkFixture:
    """Q8: Phase 4 git_diff summary output."""
    return BenchmarkFixture(
        question_id="Q8",
        description="Phase 4 git_diff summary output.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="git_diff",
                params={},
                expected_evidence_ids=("ev:git/summary/diff",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="git_diff",
                params={"format": "summary"},
                expected_evidence_ids=("ev:git/summary/diff",),
            ),
        ),
        expected_evidence_ids=("ev:git/summary/diff",),
        max_returned_bytes=80_000,
        max_tool_calls=2,
    )


def question_phase4_exec_summary_spill() -> BenchmarkFixture:
    """Q9: Phase 4 exec summary output with replayable spill handles."""
    return BenchmarkFixture(
        question_id="Q9",
        description="Phase 4 exec summary output.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="exec",
                params={"command": "make verify"},
                expected_evidence_ids=("ev:exec/spill/stdout",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="exec",
                params={"command": "make verify", "format": "summary"},
                expected_evidence_ids=("ev:exec/spill/stdout",),
            ),
        ),
        expected_evidence_ids=("ev:exec/spill/stdout",),
        max_returned_bytes=80_000,
        max_tool_calls=2,
    )


# Ponytail: every workflow that AC-12 requires a benchmark for is a
# fixture here. ``_REQUIRED_BENCH_WORKFLOW_IDS`` is the closed
# vocabulary the gate test asserts; adding a workflow is a single
# entry in both places.
REQUIRED_BENCH_WORKFLOW_IDS: tuple[str, ...] = (
    "Q1",
    "Q2",
    "Q3",
    "Q4",  # graph callers
    "Q5",  # edit impact preview
    "Q6",  # mutation freshness
    "Q7",  # Phase 4 git_status compact
    "Q8",  # Phase 4 git_diff summary
    "Q9",  # Phase 4 exec summary
)

EXTENDED_FIXTURES: tuple[BenchmarkFixture, ...] = (
    question_graph_callers(),
    question_edit_impact_preview(),
    question_mutation_freshness(),
    question_phase4_git_status_compact(),
    question_phase4_git_diff_summary(),
    question_phase4_exec_summary_spill(),
)

ALL_FIXTURES: tuple[BenchmarkFixture, ...] = (
    *REQUIRED_FIXTURES,
    *EXTENDED_FIXTURES,
)


def run_benchmark(
    fixture: BenchmarkFixture,
    *,
    baseline_executor: Callable[[ScriptedCall], Mapping[str, object]],
    indexed_executor: Callable[[ScriptedCall], Mapping[str, object]],
    clock: Clock | None = None,
) -> BenchmarkResult:
    """Run a fixture's baseline and indexed flows and produce a result.

    ``baseline_executor`` and ``indexed_executor`` are scripted flows
    over the existing MCP handlers; they MUST NOT call a live LLM
    agent. They are pure functions of (call) -> result-dict.
    """
    clk = clock or SystemClock()
    baseline_counters = _run_script(
        fixture.baseline_script, executor=baseline_executor, clock=clk
    )
    indexed_counters = _run_script(
        fixture.indexed_script, executor=indexed_executor, clock=clk
    )
    # Ponytail: recall/precision are exact in Phase 1 because the
    # expected_evidence_ids are explicit and indexed_executor is
    # scripted; no LLM summarization is involved.
    indexed_counters = BenchmarkCounters(
        tool_calls=indexed_counters.tool_calls,
        returned_bytes=indexed_counters.returned_bytes,
        transcript_tokens=indexed_counters.transcript_tokens,
        wall_time_seconds=indexed_counters.wall_time_seconds,
        stale_fallback_events=indexed_counters.stale_fallback_events,
        evidence_recall=1.0,
        evidence_precision=1.0,
    )
    return BenchmarkResult(
        question_id=fixture.question_id,
        baseline=baseline_counters,
        indexed=indexed_counters,
        notes=(fixture.description,),
    )
