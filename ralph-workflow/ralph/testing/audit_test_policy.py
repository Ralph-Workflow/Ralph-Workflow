"""Policy compliance audit for test files.

Uses AST analysis (not regex) to detect policy violations in test code:
- sleep() calls with non-zero arguments
- Real I/O operations (file I/O, network, subprocess)
- White-box coupling to private ``ralph`` production internals
- White-box counts above the frozen baseline ceiling

Usage:
    python -m ralph.testing.audit_test_policy [tests_root]
    python -m ralph.testing.audit_test_policy --emit-baseline [tests_root]

Returns exit code 0 if no policy violations found, 1 otherwise.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping
from pathlib import Path

from ralph.testing import test_whitebox_baseline
from ralph.testing.test_whitebox_baseline import WHITEBOX_BASELINE
from ralph.testing.whitebox_detectors import (
    canonical_patch_target,
    canonical_target_string,
    collect_patched_targets,
    collect_whitebox_counts,
    count_private_attrs,
    count_private_imports,
    count_setattr_private,
)

__all__ = [
    "TestPolicyViolation",
    "audit_test_file",
    "audit_tests_directory",
    "audit_whitebox_ratchet",
    "collect_whitebox_counts",
    "collect_whitebox_inventory",
    "count_private_attrs",
    "count_private_imports",
    "count_setattr_private",
    "main",
    "render_whitebox_baseline",
]


def _split_names(value: str) -> set[str]:
    return set(value.split())


_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "build",
        "dist",
        "tmp",
    }
)

_SUBPROCESS_CALLS: frozenset[str] = frozenset(
    {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "os.system",
        "os.popen",
    }
)
_NETWORK_CALLS: frozenset[str] = frozenset(
    {
        "socket.socket",
        "socket.create_connection",
        "urllib.request.urlopen",
        "requests.get",
        "requests.post",
        "httpx.get",
        "httpx.post",
    }
)

# Files marked subprocess_e2e skip only subprocess and network checks.
_SUBPROCESS_E2E_FILES: set[str] = set()

# Sleep exemptions apply only to the sleep category.
_SLEEP_ALLOWLIST: set[str] = set()  # none currently — all sleep() in tests is a design defect

# Files in this list skip only the real-I/O category.
_IO_ALLOWLIST: set[str] = {
    # Memory regression test (test_multimodal_session_memory_regression) is
    # intrinsically coupled to FsWorkspace for valid tracemalloc measurements.
    # The test measures the production code path through handle_read_media
    # and collect_media_entries_for_phase, which in production use FsWorkspace.
    # Switching to MemoryWorkspace would:
    #   (a) change the call graph (different Workspace subclass),
    #   (b) alter object allocations (in-memory dict vs filesystem ops),
    #   (c) invalidate the calibrated regression thresholds.
    # The tracemalloc snapshot MUST match the production code path exactly —
    # a fake workspace would measure a DIFFERENT code path and produce
    # meaningless regression assertions. This is a legitimate allowlist entry.
    "test_multimodal_session_memory_regression",
    # Upstream-media memory regression test. Drives
    # normalize_upstream_content_blocks() repeatedly with N embedded-data
    # blocks AND a workspace argument; the cache_path/byte_loader seam
    # requires real FsWorkspace for the same reasons as the existing
    # multimodal regression test (memory accounting through the real
    # _write_durable_media_cache call path).
    "test_upstream_media_memory_regression",
    # FileBackedSession caching test. Writes a real session JSON file
    # to tmp_path and triggers the (mtime_ns, size)-keyed cache; the
    # caching is the unit under test and the production loader calls
    # path.read_text() which the audit flags as real I/O. Switching
    # to a fake Path would defeat the regression assertion (which
    # specifically tests the on-disk change-detection contract).
    "test_mcp_file_backed_session_caching",
    # Template rendering and source-invariant tests read Jinja2 template files
    # from the repo. The template body is the subject under test, so mocking
    # it would test nothing meaningful.
    "test_prompt_best_practices",
    "test_analysis_context_partial_analysis_context_path_behavior",
    "test_analysis_context_partial_analysis_context_path_only_behavior",
    "test_analysis_context_partial_analysis_context_rendering",
    "test_analysis_context_partial_analysis_context_suppression",
    "test_analysis_prompt_payload_contract_analysis_template_payload_contract",
    "test_analysis_prompt_payload_contract_retry_hint_guard_in_templates",
    # Failure-ownership prompt guard reads the shared partial and five source
    # templates because their literal contents are the regression contract;
    # fixture text would not detect prompt-source drift.
    "test_prompts_no_exemption_for_failures",
    # Source-inspection guard that reads production Python source files
    # (prompts/materialize.py, mcp/tools/artifact.py, display/parallel_display.py,
    # display/completion_summary.py, cli/commands/run.py, pipeline/handoffs.py,
    # and the plain_renderer package) to enforce no hardcoded phase names.
    # The seven previous single-source guards were consolidated into this
    # single module. Production source is the observable subject of these
    # structural tests, so replacing with mocked content would defeat the
    # purpose.
    "test_no_hardcoded_phase_names",
    # Static analysis tests that read Python source or documentation files
    # from the repo to enforce structural invariants.
    "test_doc_adding_a_new_agent",
    "test_parallel_no_worktree_imports",
    "test_repo_root_operational_docs_sync",
    # Documentation-source and CLI callback source-sync invariants inspect
    # repository documentation and production modules. Their read targets are
    # the subjects under test, so fixtures or a tmp_path copy would make the
    # regression guards meaningless; the callback guidance test also pins the
    # explicit-only local-config boundary.
    "test_documentation_command_sync",
    # AC-08 silent-skip audit that reads auto_integrate.py source to
    # enforce that the AC-01 disabled-path and the
    # phase-transition-pre-check surface both remain byte-identical
    # (the production source IS the subject under test; a tmp_path
    # copy would defeat the structural invariant the test pins).
    "test_auto_integrate_no_silent_skip",
    # Agent spawn site guard that drives the (sync) invoke reader and
    # the (async) SubprocessAgentExecutor.run() paths RUNTIME via
    # recording sync/async fake factories on a real ``ProcessManager``.
    # The factories use ``FakePopen`` /
    # ``FakeControllableAsyncProcess`` from ``ralph.testing.fake_process``
    # so no real OS process is created and no ``subprocess.run`` /
    # ``Popen`` / ``create_subprocess_exec`` is invoked. Listed here
    # so the audit does not flag the ``ProcessManager`` /
    # ``FakePopen`` / ``FakeControllableAsyncProcess`` imports as
    # test policy violations; the runtime seam itself does no real I/O.
    "test_agent_spawn_detaches_tty",
    # Spawn-entry containment test reads the package's script declarations and
    # guarded production modules. Those files are the subject under test: a
    # mocked filesystem could not detect a newly added entry point that skips
    # inherited malloc-debug cleanup.
    "test_spawn_env_containment",
    # Artifact-submission prompt audits that read the packaged Jinja
    # templates (production source) to enforce that every single-shot
    # template embeds the shared ``_artifact_submission.j2`` macro with
    # the canonical artifact type. The template body is the subject under
    # test — a mock would defeat the audit's purpose.
    "test_audit_artifact_submission_canonical_types",
    "test_audit_artifact_submission_dumb_agent_proof",
    "test_audit_artifact_submission_standardization",
    # Spawns python -O to verify import-time invariants survive -O.
    # This cannot be tested from the same process because -O is a
    # per-process flag; a subprocess is the only way to test this.
    "test_audit_artifact_submission_canonical_path",
    # Spawns python -O to verify timing-safety import-time invariants survive -O.
    # This cannot be tested from the same process because -O is a
    # per-process flag; a subprocess is the only way to test this.
    "test_timing_safety_transports",
    # Black-box CLI entry-point verification for the wt-013 skill-mirror
    # freshness audit. The test spawns ``python -m
    # ralph.testing.audit_skill_mirror_freshness`` to prove the audit's
    # ``main()`` is reachable as a module CLI (the path the verify
    # step uses), not just as an imported function. MockProcessExecutor
    # cannot replace this: the verification step runs the audit as a
    # subprocess, and the test's job is to mirror that path so a
    # packaging regression that breaks ``python -m ...`` invocation
    # cannot slip past the unit test.
    "test_audit_skill_mirror_freshness",
    # Git integration tests using the tmp_git_repo fixture (which wraps
    # tmp_path). The write_text calls go to the fixture's temp directory.
    "test_git_rebase_preconditions",
    "test_git_wrapper",
    # Legacy-restart acceptance test (scenario 8) seeds raw legacy checkpoint
    # + record bytes into the tmp_git_repo fixture dir so the production
    # resume read must parse and migrate them; same tmp_git_repo-writes
    # pattern as the two entries above.
    "test_legacy_restart_acceptance",
    # Docs<->pin-list parity guard: reads docs/agents/watchdog-spec.md
    # to enforce that every RALPH_PIN_TEST_PATHS entry is cited in the
    # traceability doc. The assertion is the contract under test;
    # mocking the file read would test nothing.
    "test_watchdog_spec_docs_pin_parity",
    # Subprocess-launched AGY mock agent stub
    # (tests/_support/mock_agy_v1_1_13.py, the v1.1.13 emitter split out of
    # mock_agy.py). The stub performs REAL loopback JSON-RPC round trips
    # (urllib.request.urlopen) against the harness's MCP fallback server at
    # RALPH_MCP_ENDPOINT -- the real HTTP transport is the system under test
    # (wire-ledger evidence is only produced by an actual tools/call), so
    # mocking the boundary would defeat the purpose. Mirrors
    # mock_multimodal_agent.py's real-wire dispatch.
    "mock_agy_v1_1_13",
    # Startup-diagnostics tests: the LifecycleDeps contract requires a
    # real listening socket (reserve_listening_socket returns
    # (host, port, socket.socket)); a kernel-assigned loopback bind on
    # port 0 is the production dependency shape under test. Mocking the
    # boundary would break the tuple type the production code asserts.
    "test_mcp_server_startup_diagnostics",
}

# Files that may read only shipped repository source files. This exemption
# suppresses Path.read_text/read_bytes findings only; every other policy
# category remains checked for the same file.
_SOURCE_READ_ALLOWLIST: set[str] = {
    # Starter-policy and ADR alignment guard. Reads the shipped
    # ``ralph/project_policy/starters/*.md`` files and ADR-0002 because their
    # literal contents are the regression contract: the test detects drift
    # between the four starter policies and the recorded architecture
    # decision. A tmp_path copy would assert against the fixture, not the
    # shipped file, so the guard would stop detecting drift entirely. Reading
    # those files is the ONLY thing the test needs exempted.
    "test_visual_verdict_policy_alignment",
    # Parallel-execution guidance regression guard. Reads the shipped
    # ``ralph/prompts/templates/shared/_parallel_execution.jinja`` partial
    # because its literal contents are the regression contract: the test
    # pins the wave/ownership/protected-path prose, a fixture copy would
    # assert the fixture's contents, not the shipped file's, and a
    # ``tmp_path`` rewrite would defeat the drift guard. Reading that one
    # template is the ONLY thing the test needs exempted.
    "test_developer_prompt_subagent_guidance",
    # per-file-ignores inventory drift guard. Reads the shipped
    # ``pyproject.toml`` and ``docs/ralph-workflow-policy/linting-policy.md``
    # because the policy document claims to be "the complete inventory of
    # keys" for ``[tool.ruff.lint.per-file-ignores]``; a fixture copy would
    # assert the fixture's completeness, not the shipped files'. Reading
    # those two files is the ONLY thing the test needs exempted.
    "test_linting_policy_inventory",
    # Single-owner regression guards for the pipeline state/record
    # authorities. Each scans the shipped ``ralph/pipeline/*.py`` sources
    # with ``ast`` because the shipped source IS the regression contract:
    # the test detects any new direct writer of an authority-owned field
    # outside its owning module. A tmp_path copy would scan the fixture, not
    # the production tree, so the guard would stop detecting drift entirely.
    # Reading those files is the ONLY thing these tests need exempted; any
    # write/subprocess/sleep in them is still reported.
    "test_waiting_state_owner",
    "test_recovery_record_retained_owner",
    "test_run_loop_setters_only",
    "test_run_loop_decision_delegation",
    "test_diagnostic_transition_helpers",
    "test_commit_completion_authority",
}

_PATH_READ_METHODS: frozenset[str] = frozenset({"read_text", "read_bytes"})

_WALL_CLOCK_ALLOWLIST: set[str] = {
    # Single timestamp measurement for fake process creation — not an
    # elapsed-time loop. Aliased as _time.monotonic().
    "test_process_manager",
    # Comparing FakeClock timing against real wall-clock time.
    # Core purpose of these tests: verify FakeClock.sleep() and
    # FakeClock.advance() don't cause real wall-clock delays.
    "test_timeout_clock",
    # Parallel execution timing assertions.
    # Measures fan-out/verify timing to confirm parallelism works
    # correctly, not to accumulate passage-of-time for control flow.
    "test_parallel_serialized_verification",
    # Measures actual process kill duration for hard-kill correctness testing.
    # Wall-clock measurement IS the point of this test.
    # Renamed from ``test_*_helper_*`` to ``_*_helper_*`` in commit
    # e1247b55d; the audit's allowlist keys on the file stem, so this
    # entry must match the renamed stem to remain effective.
    "_hard_kill_helper_sleeperexecutor",
    # Performance regression test: measures real subscriber dispatch latency.
    # Wall-clock measurement IS the correctness assertion.
    "test_subscriber_performance",
    # Wall-clock budget pin for the new anti-drift test classes (per
    # PA-004). The TestRegressionBudget::test_combined_wall_clock_under_8s
    # test and the test_wedged_run_exits_cleanly test (PA-006) both
    # measure real wall-clock to assert budget compliance. The wall-clock
    # measurement IS the correctness assertion in both cases.
    "test_no_anti_drift_regression",
    "test_no_anti_drift_recovery_invariants",
    # Wall-clock budget pin for the F1-F20 fault matrix. Each
    # per-mode case asserts the call returned inside its 1.0s
    # budget via real wall-clock because the production handler
    # does not expose a clock injection seam yet; the measurement
    # IS the correctness assertion (criterion 1c).
    "test_explore_fault_matrix_full",
    "test_explore_fault_matrix_full_late",
    "test_explore_fault_matrix",
    # Wall-clock budget pin for the runtime FD-leak / watch-handle
    # resource tests. The proofs use real wall-clock to measure
    # the post-operation FD count and inspect the child process
    # /proc/<pid>/fdinfo via a real subprocess because the explore
    # substrate owns the SQLite connection (no injection seam).
    "test_explore_resource_lifecycle",
    # Default-gate endpoint sweep wall-clock budget pin (per AC-05,
    # AC-06). The sweep must complete well inside the immutable
    # 60-second combined test budget; the per-tool and per-suite
    # 5s/10s budgets are the correctness assertion, so the test
    # uses ``time.monotonic()`` to measure real elapsed time.
    # Production code paths still inject FakeClock for any
    # timeout-driven flow; the wall-clock measurement here is a
    # single-point budget pin that cannot be expressed with a
    # fake clock. The ``test_mcp_endpoint_functional_sweep`` file
    # is the surviving single sweep after the zero-dead-code
    # consolidation (the legacy
    # ``tests/integration/test_mcp_endpoint_sweep.py`` was deleted
    # and its unique wall-clock assertions ported here).
    "test_mcp_endpoint_functional_sweep",
    # Realistic-codebase fixture wall-clock budget pin (per AC-02,
    # AC-06). ``test_indexed_search_completes_under_5s`` measures
    # the index + grep cycle over a ~50-file synthetic workspace
    # so a regression that triples the indexed search wall time
    # cannot slip past the default gate. The wall-clock
    # measurement IS the assertion; production paths inside the
    # cycle still use FakeClock for any timeout contract.
    "test_explore_real_codebase",
    # Memory-plateau characterization harness (wt-024): prints
    # early/late elapsed means via time.perf_counter() for drift
    # inspection. Retained-byte plateau remains the hard assertion;
    # elapsed timing is characterization evidence, not a brittle
    # wall-clock oracle. Kept in the default suite (no
    # subprocess_e2e) because the harness uses no real child
    # processes; only the single-point timing measurement needs
    # this allowlist.
    "test_recovery_memory_regression",
    # S-12 scenario baseline characterization harness: the
    # responsiveness dimension measures real reindex + search
    # elapsed time across 6 filesystem scenarios. The wall-clock
    # measurement IS the characterization data being recorded
    # (not a control-flow oracle); production code paths inside
    # the cycle still use FakeClock for any timeout contract.
    # Kept in the default suite because the harness uses no real
    # child processes; only the single-point timing measurement
    # needs this allowlist.
    "test_filesystem_scenario_baseline",
}

_SMOKE_NAME_ALLOWLIST: set[str] = {
    "test_smoke_capability_breaks_when_supported_but_unrendered",
    "test_exec_smoke_subprocess_e2e",
}

_PATH_IO_METHODS: frozenset[str] = frozenset(
    {"read_text", "write_text", "read_bytes", "write_bytes", "open"}
)

# Patch calls are exemptions only for their exact dotted target.
_MONKEYPATCH_PATTERNS: set[str] = _split_names(
    """
    monkeypatch.setattr monkeypatch.setenv monkeypatch.delenv monkeypatch.setitem
    monkeypatch.delitem unittest.mock.patch mock.patch mocker.patch patch(
    """
)

_ALLOWED_SLEEP_PATTERNS: set[str] = {
    "asyncio.sleep(0)",
    "asyncio.sleep(0.0)",
    "time.sleep(0)",
    "time.sleep(0.0)",
}

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WHITEBOX_CATEGORIES: tuple[str, ...] = (
    "setattr_private",
    "private_import",
    "private_attr",
)
WhiteboxCounts = dict[str, int]
WhiteboxBaseline = Mapping[str, Mapping[str, int]]


class TestPolicyViolation:
    """A single policy violation found in a test file."""

    __test__ = False

    def __init__(
        self,
        file_path: str,
        line: int,
        category: str,
        detail: str,
    ) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


class TestPolicyAuditor(ast.NodeVisitor):
    """AST visitor that detects test policy violations."""

    def __init__(self, file_path: str, source: str, tree: ast.AST | None = None) -> None:
        self.file_path = file_path
        self.source_lines = source.splitlines()
        self.violations: list[TestPolicyViolation] = []
        self._source_read_exempt = Path(file_path).stem in _SOURCE_READ_ALLOWLIST
        parsed_tree = tree if tree is not None else ast.parse(source, filename=file_path)
        self._has_subprocess_e2e_marker = _has_subprocess_e2e_marker_ast(parsed_tree)
        # The ``subprocess_e2e`` marker is the repository's declared profile for
        # tests that drive REAL subprocesses and sockets. Per the canonical
        # testing policy (docs/ralph-workflow-policy/testing-policy.md):
        # "An agent MUST NOT introduce real ``time.sleep()``, real filesystem
        # I/O, or real network I/O in tests not marked ``subprocess_e2e`` or
        # ``live_agy``", and the audit "fails the gate on real
        # ``time.sleep()`` with non-zero arguments, real file ``open()``, real
        # subprocess calls, and real socket/``urllib``/``requests``/``httpx``
        # calls in any test that is not marked ``subprocess_e2e``".
        #
        # A marked test therefore owns the real-time and real-process
        # categories: waiting for a real child to exit, measuring real elapsed
        # time, and touching real sockets are the SUBJECT UNDER TEST there.
        # The marker exempts those categories ONLY. The three white-box
        # detectors are unconditional and still run on every test file.
        self._is_subprocess_e2e = (
            Path(file_path).stem in _SUBPROCESS_E2E_FILES or self._has_subprocess_e2e_marker
        )
        self._skip_sleep = Path(file_path).stem in _SLEEP_ALLOWLIST or self._is_subprocess_e2e
        self._skip_io = Path(file_path).stem in _IO_ALLOWLIST or self._is_subprocess_e2e
        self._skip_wall_clock = (
            Path(file_path).stem in _WALL_CLOCK_ALLOWLIST or self._is_subprocess_e2e
        )
        self._skip_subprocess_network = self._is_subprocess_e2e
        self._patched_targets = collect_patched_targets(parsed_tree, _MONKEYPATCH_PATTERNS)
        self._inside_wait_for: bool = False

    def _add_violation(self, node: ast.AST, category: str, detail: str) -> None:
        lineno: int = getattr(node, "lineno", 0)
        self.violations.append(
            TestPolicyViolation(
                file_path=self.file_path,
                line=lineno,
                category=category,
                detail=detail,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        """Detect sleep() and I/O calls."""
        self._check_sleep_call(node)
        self._check_io_call(node)
        self._check_wall_clock_call(node)
        self._check_blocking_wait_call(node)
        func_name = self._get_func_name(node)
        parent_was_in_wait_for = self._inside_wait_for
        if func_name in ("asyncio.wait_for", "asyncio.timeout"):
            self._inside_wait_for = True
        self.generic_visit(node)
        self._inside_wait_for = parent_was_in_wait_for

    def _check_sleep_call(self, node: ast.Call) -> None:
        """Detect time.sleep(N) and asyncio.sleep(N) where N > 0."""
        func_name = self._get_func_name(node)
        if func_name is None:
            return

        if func_name not in ("time.sleep", "asyncio.sleep"):
            return
        if self._skip_sleep:
            return
        if self._is_target_patched(func_name):
            return

        if node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant):
                if isinstance(first_arg.value, (int, float)) and first_arg.value <= 0:
                    return  # asyncio.sleep(0) is allowed
                if isinstance(first_arg.value, (int, float)) and first_arg.value > 0:
                    self._add_violation(
                        node,
                        "sleep",
                        f"{func_name}({first_arg.value}) — "
                        "sleep with positive argument is a design defect",
                    )
                    return
            self._add_violation(
                node,
                "sleep",
                f"{func_name}(...) — dynamic sleep call; inject a clock abstraction instead",
            )

    def _check_io_call(self, node: ast.Call) -> None:
        """Detect real I/O operations."""
        if self._skip_io:
            return
        func_name = self._get_func_name(node)
        if func_name is None:
            self._check_anonymous_path_io_call(node)
        elif func_name == "open":
            if not self._is_target_patched("builtins.open"):
                self._add_violation(
                    node,
                    "io",
                    "open() — real file I/O in test; use MemoryWorkspace, tmp_path, or monkeypatch",
                )
        elif any(func_name == f"Path.{method}" for method in _PATH_IO_METHODS):
            method_name = func_name.split(".", 1)[1]
            if self._is_source_read_exempt(method_name):
                return
            target = canonical_patch_target("Path", method_name)
            if not self._is_target_patched(target) and not self._is_using_tmp_path():
                self._add_violation(
                    node,
                    "io",
                    f"{func_name}() — real filesystem I/O in test; use tmp_path fixture",
                )
        elif func_name in _SUBPROCESS_CALLS:
            if not self._skip_subprocess_network:
                self._add_violation(
                    node,
                    "io",
                    f"{func_name}() — subprocess call in test; use MockProcessExecutor",
                )
        elif func_name in _NETWORK_CALLS and not (
            self._skip_subprocess_network or self._is_target_patched(func_name)
        ):
            self._add_violation(
                node,
                "io",
                f"{func_name}() — network I/O in test; use mock/patch at the boundary",
            )

    def _is_source_read_exempt(self, method_name: str) -> bool:
        """Return True when this file may read shipped repository files."""
        return self._source_read_exempt and method_name in _PATH_READ_METHODS

    def _check_anonymous_path_io_call(self, node: ast.Call) -> None:
        """Detect I/O on an inline Path expression without a dotted name."""
        if not (isinstance(node.func, ast.Attribute) and node.func.attr in _PATH_IO_METHODS):
            return
        if self._is_source_read_exempt(node.func.attr):
            return
        target = canonical_patch_target("Path", node.func.attr)
        if self._is_target_patched(target) or self._is_using_tmp_path():
            return
        self._add_violation(
            node,
            "io",
            f".{node.func.attr}() — real filesystem I/O in test; use tmp_path fixture",
        )

    def _check_wall_clock_call(self, node: ast.Call) -> None:
        """Detect time.monotonic()/time.perf_counter() in elapsed-time loops."""
        func_name = self._get_func_name(node)
        if func_name is None:
            return

        if func_name in ("time.monotonic", "time.perf_counter"):
            if self._skip_wall_clock or self._is_target_patched(func_name):
                return
            self._add_violation(
                node,
                "wall-clock",
                f"{func_name}() - real wall-clock measurement in test; "
                "inject a clock abstraction instead",
            )

    def _check_blocking_wait_call(self, node: ast.Call) -> None:
        """Detect .wait() calls without a timeout argument."""
        func_name = self._get_func_name(node)
        if func_name is None:
            return

        if func_name.endswith(".wait") and not node.args and not node.keywords:
            if self._inside_wait_for:
                return
            if self._is_subprocess_e2e:
                # A real child process is waited on for real; the marker owns
                # the blocking-wait category for the same reason it owns the
                # sleep and wall-clock categories.
                return
            self._add_violation(
                node,
                "blocking-wait",
                f"{func_name}() without timeout - blocking wait in test; always specify a timeout",
            )

    def _is_target_patched(self, target: str) -> bool:
        """Return whether the exact dotted ``target`` is patched in this file."""
        canonical = canonical_target_string(target)
        return canonical in self._patched_targets

    def _get_func_name(self, node: ast.Call) -> str | None:
        """Extract the full dotted function name from a Call node."""
        func = node.func
        parts: list[str] = []

        while isinstance(func, ast.Attribute):
            parts.append(func.attr)
            func = func.value
        if isinstance(func, ast.Name):
            parts.append(func.id)
        else:
            return None

        return ".".join(reversed(parts))

    def _is_using_tmp_path(self) -> bool:
        """Check if the file uses tmp_path fixture (legitimate filesystem use)."""
        return "tmp_path" in "\n".join(self.source_lines)


def _collect_markers_for_file(py_file: Path) -> set[str]:
    """Return every pytest marker name used in ``py_file`` (AST-based)."""

    try:
        source = py_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()

    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        return set()

    markers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    _harvest_markers_from_value(node.value, markers)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                _harvest_markers_from_value(decorator, markers)
    return markers


def _harvest_markers_from_value(value: ast.AST, markers: set[str]) -> None:
    """Walk a single marker expression and record its name."""
    if isinstance(value, ast.Attribute):
        if (
            isinstance(value.value, ast.Attribute)
            and value.value.attr == "mark"
            and isinstance(value.value.value, ast.Name)
            and value.value.value.id == "pytest"
        ):
            markers.add(value.attr)
    elif isinstance(value, ast.Call):
        if isinstance(value.func, ast.Attribute):
            _harvest_markers_from_value(value.func, markers)
    elif isinstance(value, ast.List):
        for elt in value.elts:
            _harvest_markers_from_value(elt, markers)


def _collect_subprocess_e2e_files(tests_root: Path) -> set[str]:
    """Find all test files marked with @pytest.mark.subprocess_e2e."""
    e2e_files: set[str] = set()
    for py_file in tests_root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in py_file.relative_to(tests_root).parts):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "subprocess_e2e" not in content:
            continue
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            e2e_files.add(py_file.stem)
            continue
        has_marker = _has_subprocess_e2e_marker_ast(tree)
        if has_marker:
            e2e_files.add(py_file.stem)
    return e2e_files


def _has_subprocess_e2e_marker_ast(tree: ast.AST) -> bool:
    """Check AST for @pytest.mark.subprocess_e2e decorator or pytestmark."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                if _is_subprocess_e2e_decorator(decorator):
                    return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    if _is_subprocess_e2e_decorator(node.value):
                        return True
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if _is_subprocess_e2e_decorator(elt):
                                return True
    return False


def _is_subprocess_e2e_decorator(node: ast.AST) -> bool:
    """Check if a decorator node represents @pytest.mark.subprocess_e2e."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "subprocess_e2e"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _iter_test_files(tests_root: Path) -> list[Path]:
    """Return auditable Python files below ``tests_root`` in stable order."""
    return [
        path
        for path in sorted(tests_root.rglob("*.py"))
        if not any(part in _SKIP_DIRS for part in path.relative_to(tests_root).parts)
    ]


def _baseline_key(file_path: Path, tests_root: Path | None = None) -> str:
    """Return the project-relative baseline key for ``file_path``."""
    resolved = file_path.resolve()
    try:
        return resolved.relative_to(_PROJECT_ROOT).as_posix()
    except ValueError:
        pass

    if tests_root is not None:
        try:
            relative = resolved.relative_to(tests_root.resolve())
        except ValueError:
            relative = Path(file_path.name)
        return (Path("tests") / relative).as_posix()
    return (Path("tests") / file_path.name).as_posix()


def collect_whitebox_inventory(tests_root: Path) -> dict[str, WhiteboxCounts]:
    """Return current white-box counts for every non-zero test file."""
    inventory: dict[str, WhiteboxCounts] = {}
    for path in _iter_test_files(tests_root):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        counts = collect_whitebox_counts(tree)
        if any(count > 0 for count in counts.values()):
            inventory[_baseline_key(path, tests_root)] = counts
    return inventory


def render_whitebox_baseline(inventory: Mapping[str, Mapping[str, int]]) -> str:
    """Render a deterministic frozen-baseline Python module."""
    docstring = test_whitebox_baseline.__doc__
    if docstring is None:
        raise RuntimeError("frozen white-box baseline is missing its module docstring")
    lines = [
        f'"""{docstring}"""\n',
        "from __future__ import annotations",
        "",
        "INIT_COMPLETE = True",
        "",
        '_RAW = """',
    ]
    for path in sorted(inventory):
        counts = inventory[path]
        if not any(counts.get(category, 0) > 0 for category in _WHITEBOX_CATEGORIES):
            continue
        lines.append(
            f"{path}|{counts.get('setattr_private', 0)},"
            f"{counts.get('private_import', 0)},{counts.get('private_attr', 0)}"
        )
    lines.extend(
        [
            '"""',
            "",
            "",
            "def _parse(raw: str) -> dict[str, dict[str, int]]:",
            '    """Parse the frozen ``path|c1,c2,c3`` table into the public dict form."""',
            "    table: dict[str, dict[str, int]] = {}",
            "    for line in raw.splitlines():",
            "        record = line.strip()",
            "        if not record:",
            "            continue",
            '        path, counts = record.split("|")',
            '        setattr_private, private_import, private_attr = counts.split(",")',
            "        table[path] = {",
            '            "setattr_private": int(setattr_private),',
            '            "private_import": int(private_import),',
            '            "private_attr": int(private_attr),',
            "        }",
            "    return table",
            "",
            "",
            "WHITEBOX_BASELINE: dict[str, dict[str, int]] = _parse(_RAW)",
        ]
    )
    return "\n".join(lines) + "\n"


def audit_whitebox_ratchet(
    file_path: Path,
    counts: Mapping[str, int],
    baseline: WhiteboxBaseline,
    baseline_key: str | None = None,
) -> list[TestPolicyViolation]:
    """Compare one file's white-box counts with its frozen ceiling."""
    if not any(count > 0 for count in counts.values()):
        return []
    key = baseline_key or _baseline_key(file_path)
    ceiling = baseline.get(key)
    if ceiling is None:
        return [
            TestPolicyViolation(
                file_path=str(file_path),
                line=1,
                category="whitebox-baseline-missing",
                detail=(
                    "white-box usage is absent from the frozen baseline; "
                    f"current counts: {dict(counts)}"
                ),
            )
        ]

    violations: list[TestPolicyViolation] = []
    for category in _WHITEBOX_CATEGORIES:
        current = counts.get(category, 0)
        allowed = ceiling.get(category, 0)
        if current > allowed:
            violations.append(
                TestPolicyViolation(
                    file_path=str(file_path),
                    line=1,
                    category="whitebox-regression",
                    detail=(f"{category} count {current} exceeds frozen ceiling {allowed}"),
                )
            )
    return violations


def audit_test_file(
    file_path: Path,
    *,
    baseline: WhiteboxBaseline | None = None,
    baseline_key: str | None = None,
) -> list[TestPolicyViolation]:
    """Audit a single test file for policy violations."""
    if not file_path.is_file() or file_path.suffix != ".py":
        return []
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []

    auditor = TestPolicyAuditor(str(file_path), source, tree)
    auditor.visit(tree)
    effective_baseline = WHITEBOX_BASELINE if baseline is None else baseline
    auditor.violations.extend(
        audit_whitebox_ratchet(
            file_path,
            collect_whitebox_counts(tree),
            effective_baseline,
            baseline_key,
        )
    )
    return auditor.violations


def audit_tests_directory(
    tests_root: Path,
    *,
    baseline: WhiteboxBaseline | None = None,
) -> tuple[list[TestPolicyViolation], int]:
    """Audit all test files in a directory."""
    _SUBPROCESS_E2E_FILES.clear()
    _SUBPROCESS_E2E_FILES.update(_collect_subprocess_e2e_files(tests_root))

    all_violations: list[TestPolicyViolation] = []
    files_checked = 0

    for py_file in _iter_test_files(tests_root):
        if py_file.name in ("test_process_audit.py", "audit_test_policy.py"):
            continue
        if "/fixtures/" in py_file.as_posix():
            continue
        violations = audit_test_file(
            py_file,
            baseline=baseline,
            baseline_key=_baseline_key(py_file, tests_root),
        )
        if violations:
            all_violations.extend(violations)
        if (
            "smoke" in py_file.stem.lower()
            and py_file.stem not in _SMOKE_NAME_ALLOWLIST
            and "smoke" not in _collect_markers_for_file(py_file)
        ):
            all_violations.append(
                TestPolicyViolation(
                    file_path=str(py_file),
                    line=1,
                    category="smoke-unmarked",
                    detail=(
                        f"Test file '{py_file.name}' has 'smoke' in the name "
                        f"but is not marked with @pytest.mark.smoke. Without "
                        f"the marker, pytest's -m 'not smoke' addopts will "
                        f"NOT exclude it and it will leak into the regular "
                        f"test suite. Mark it: 'pytestmark = pytest.mark.smoke' "
                        f"or '@pytest.mark.smoke' on each test."
                    ),
                )
            )
        files_checked += 1

    return all_violations, files_checked


def main(
    argv: list[str] | None = None,
    *,
    baseline: WhiteboxBaseline | None = None,
) -> int:
    """Run the policy audit and return an exit code."""
    args = argv if argv is not None else sys.argv[1:]
    emit_baseline = "--emit-baseline" in args
    positional = [argument for argument in args if argument != "--emit-baseline"]
    if len(positional) > 1 or any(argument.startswith("-") for argument in positional):
        print(
            "Usage: python -m ralph.testing.audit_test_policy [--emit-baseline] [tests_root]",
            file=sys.stderr,
        )
        return 2

    tests_root = Path(positional[0]) if positional else (_PROJECT_ROOT / "tests")

    if not tests_root.is_dir():
        print(f"Error: tests directory not found: {tests_root}", file=sys.stderr)
        return 2

    if emit_baseline:
        inventory = collect_whitebox_inventory(tests_root)
        sys.stdout.write(render_whitebox_baseline(inventory))
        return 0

    violations, files_checked = audit_tests_directory(tests_root, baseline=baseline)
    print(f"Auditing test files in: {tests_root}")
    print(f"Subprocess/network E2E files exempt: {len(_SUBPROCESS_E2E_FILES)}")
    print()

    if violations:
        print(f"POLICY VIOLATIONS FOUND: {len(violations)} violation(s) in {files_checked} file(s)")
        print("=" * 72)
        for violation in violations:
            print(f"  {violation}")
        print()
        print("These violations are test design defects. Fix the test, not the audit.")
        print("Guidance: docs/agents/testing-guide.md §'Test Performance Policy'")
        return 1

    print(f"No policy violations found in {files_checked} file(s).")
    return 0


_WALL_CLOCK_ALLOWLIST = _WALL_CLOCK_ALLOWLIST | frozenset(
    {"test_pipeline_multi_cycle_memory_regression"}
)

if __name__ == "__main__":
    raise SystemExit(main())
