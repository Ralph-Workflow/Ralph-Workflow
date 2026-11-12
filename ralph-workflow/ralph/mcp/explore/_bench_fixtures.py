from __future__ import annotations

from ralph.mcp.explore._bench_types import BenchmarkFixture, ScriptedCall


def question_register_tool() -> BenchmarkFixture:
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
    return BenchmarkFixture(
        question_id="Q2",
        description="Find likely tests for a handler.",
        workspace_files={
            "ralph/mcp/tools/workspace/_read_handlers.py": (
                "def handle_read_file(...):\n    ...\n"
            ),
            "tests/test_mcp_read_handler.py": ("def test_handle_read_file_basic():\n    ...\n"),
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
    return BenchmarkFixture(
        question_id="Q3",
        description=(
            "Estimate rename impact via lexical callers. Graph/semantic "
            "impact is Phase 3 and tracked in deferred_phases.py."
        ),
        workspace_files={
            "ralph/mcp/explore/store.py": "def open_index():\n    ...\n",
            "ralph/mcp/explore/pipeline.py": "from ralph.mcp.explore.store import open_index\n",
            "tests/test_explore_pipeline.py": ("from ralph.mcp.explore.store import open_index\n"),
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


def question_graph_callers() -> BenchmarkFixture:
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
    return BenchmarkFixture(
        question_id="Q6",
        description="Mutation freshness metadata via edit_file target.",
        workspace_files={},
        baseline_script=(
            ScriptedCall(
                tool="edit_file",
                params={
                    "path": "ralph/tools/foo.py",
                    "edits": [{"oldText": "return 'world'", "newText": "return 'world!'"}],
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
                    "edits": [{"oldText": "return 'world'", "newText": "return 'world!'"}],
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


REQUIRED_BENCH_WORKFLOW_IDS: tuple[str, ...] = (
    "Q1",
    "Q2",
    "Q3",
    "Q4",
    "Q5",
    "Q6",
    "Q7",
    "Q8",
    "Q9",
    "Q10",
    "Q11",
    "Q12",
    "Q13",
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
