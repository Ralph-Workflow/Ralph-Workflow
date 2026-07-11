"""Benchmark fixture builders for the indexed exploration substrate.

Extracted from :mod:`ralph.mcp.explore.bench` so the harness module stays
under the repository's per-file line ceiling. Each fixture defines a
question, baseline flow, indexed flow, expected evidence ids, and the
budget ceilings the gate tests compare against.

AC-12: this module owns Q1-Q13 (lexical indexed search, graph,
edit-impact, mutation freshness, and Phase 4 git/exec/web/media
remediation fixtures). Q1-Q3 are the lexicon-only baselines; Q4-Q13
are the focused-handler coverage set that the bench gate asserts.
"""

from __future__ import annotations

from ralph.mcp.explore._bench_types import BenchmarkFixture, ScriptedCall


def question_register_tool() -> BenchmarkFixture:
    """Q1: find where a tool is registered.

    AC-07 (real-handler bench): the indexed script uses real
    handlers, so the indexed read_file MUST drop the synthetic
    ``evidence_id`` parameter and only specify ``path``. The
    truth set (a real FTS-derived evidence id) is supplied by
    the test from the indexed store; the fixture's
    ``expected_evidence_ids`` remains a closed vocabulary for
    unit-test sanity checks that exercise the harness with
    synthetic executors.
    """
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
                    "regex": False,
                    "case_sensitive": False,
                    "use_index": "auto",
                    "return_evidence_ids": True,
                },
                expected_evidence_ids=("ev:register/registry",),
            ),
            ScriptedCall(
                tool="read_file",
                params={"path": "ralph/mcp/tools/bridge/_registry.py"},
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
            # AC-07 (real-handler bench): a single search_files call
            # with ``contains_symbol`` narrows the test files to
            # those referencing the handler symbol. The
            # ``return_evidence_ids`` flag attaches the indexed
            # handles so the next ``read_file`` call can resolve
            # back to the exact chunk without re-grepping.
            ScriptedCall(
                tool="search_files",
                params={
                    "pattern": "**/test_*.py",
                    "path": "tests",
                    "contains_symbol": "handle_read_file",
                    "return_evidence_ids": True,
                },
                expected_evidence_ids=("ev:test/handle_read_file",),
            ),
            ScriptedCall(
                tool="read_file",
                params={"path": "tests/test_mcp_read_handler.py"},
                expected_evidence_ids=("ev:test/handle_read_file",),
            ),
        ),
        expected_evidence_ids=("ev:test/handle_read_file",),
        max_returned_bytes=200_000,
        max_tool_calls=4,
    )


def question_estimate_rename_impact() -> BenchmarkFixture:
    """Q3: estimate rename impact via lexical callers (Phase 1 lexical only).

    AC-07: the Q3 fixture truth set includes both the caller
    evidence (``ev:ref/open_index/pipeline``) and the test
    evidence (``ev:ref/open_index/test``). A scripted indexed
    flow that returns only the caller evidence does not recall
    the test evidence; the gate is the only way to detect that
    omission.
    """
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
            # AC-07 (real-handler bench): one indexed grep call
            # across the whole workspace yields both caller and
            # test evidence in a single round trip; the baseline
            # needs two separate scoped greps. This 2-call vs
            # 3-call asymmetry is what gives the indexed flow
            # its 30%+ byte savings in the real-handler gate.
            ScriptedCall(
                tool="grep_files",
                params={
                    "pattern": "open_index",
                    "path": ".",
                    "regex": False,
                    "case_sensitive": False,
                    "use_index": "auto",
                    "return_evidence_ids": True,
                },
                expected_evidence_ids=(
                    "ev:ref/open_index/pipeline",
                    "ev:ref/open_index/test",
                ),
            ),
            ScriptedCall(
                tool="read_file",
                params={"path": "ralph/mcp/explore/pipeline.py"},
                expected_evidence_ids=("ev:ref/open_index/pipeline",),
            ),
        ),
        expected_evidence_ids=(
            "ev:ref/open_index/pipeline",
            "ev:ref/open_index/test",
        ),
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
                    "edits": [
                        {"oldText": "return 'world'", "newText": "return 'world!'"}
                    ],
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
                    "edits": [
                        {"oldText": "return 'world'", "newText": "return 'world!'"}
                    ],
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


def question_phase4_git_log_summary() -> BenchmarkFixture:
    """Q10: Phase 4 ``git_log`` ``format='summary'``."""
    return BenchmarkFixture(
        question_id="Q10",
        description="Phase 4 git_log summary output.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="git_log",
                params={"count": 5},
                expected_evidence_ids=("ev:git/summary/log",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="git_log",
                params={"count": 5, "format": "summary"},
                expected_evidence_ids=("ev:git/summary/log",),
            ),
        ),
        expected_evidence_ids=("ev:git/summary/log",),
        max_returned_bytes=80_000,
        max_tool_calls=2,
    )


def question_phase4_git_show_summary() -> BenchmarkFixture:
    """Q11: Phase 4 ``git_show`` ``format='summary'``."""
    return BenchmarkFixture(
        question_id="Q11",
        description="Phase 4 git_show summary output.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="git_show",
                params={"ref": "HEAD"},
                expected_evidence_ids=("ev:git/summary/show",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="git_show",
                params={"ref": "HEAD", "format": "summary"},
                expected_evidence_ids=("ev:git/summary/show",),
            ),
        ),
        expected_evidence_ids=("ev:git/summary/show",),
        max_returned_bytes=80_000,
        max_tool_calls=2,
    )


def question_phase4_web_search_summary() -> BenchmarkFixture:
    """Q12: Phase 4 ``web_search`` ``format='summary'``."""
    return BenchmarkFixture(
        question_id="Q12",
        description="Phase 4 web_search summary output.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="web_search",
                params={"query": "ralph workflow"},
                expected_evidence_ids=("ev:web/search/summary",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="web_search",
                params={"query": "ralph workflow", "format": "summary"},
                expected_evidence_ids=("ev:web/search/summary",),
            ),
        ),
        expected_evidence_ids=("ev:web/search/summary",),
        max_returned_bytes=80_000,
        max_tool_calls=2,
    )


def question_phase4_read_media_metadata() -> BenchmarkFixture:
    """Q13: Phase 4 ``read_media`` ``format='metadata'``."""
    return BenchmarkFixture(
        question_id="Q13",
        description="Phase 4 read_media metadata output.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="read_media",
                params={"path": "report.pdf"},
                expected_evidence_ids=("ev:media/metadata/report",),
            ),
        ),
        indexed_script=(
            ScriptedCall(
                tool="read_media",
                params={"path": "report.pdf", "format": "metadata"},
                expected_evidence_ids=("ev:media/metadata/report",),
            ),
        ),
        expected_evidence_ids=("ev:media/metadata/report",),
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
    "Q10",  # Phase 4 git_log summary
    "Q11",  # Phase 4 git_show summary
    "Q12",  # Phase 4 web_search summary
    "Q13",  # Phase 4 read_media metadata
)

EXTENDED_FIXTURES: tuple[BenchmarkFixture, ...] = (
    question_graph_callers(),
    question_edit_impact_preview(),
    question_mutation_freshness(),
    question_phase4_git_status_compact(),
    question_phase4_git_diff_summary(),
    question_phase4_exec_summary_spill(),
    question_phase4_git_log_summary(),
    question_phase4_git_show_summary(),
    question_phase4_web_search_summary(),
    question_phase4_read_media_metadata(),
)

ALL_FIXTURES: tuple[BenchmarkFixture, ...] = (
    *REQUIRED_FIXTURES,
    *EXTENDED_FIXTURES,
)
