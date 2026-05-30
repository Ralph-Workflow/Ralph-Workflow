"""Policy compliance audit for test files.

Uses AST analysis (not regex) to detect policy violations in test code:
- sleep() calls with non-zero arguments
- Real I/O operations (file I/O, network, subprocess)
- Budget circumvention patterns

Usage:
    python -m ralph.testing.audit_test_policy [tests_root]

Returns exit code 0 if no policy violations found, 1 otherwise.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Directories to skip during file collection.
_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".venv", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", "htmlcov", "build", "dist", "tmp",
})

# --- Allowlist: test files exempt from specific checks ---

# Files with pytest.mark.subprocess_e2e are EXCLUDED from all checks.
_SUBPROCESS_E2E_FILES: set[str] = set()

# Files that legitimately use sleep() for clock-injection testing.
_SLEEP_ALLOWLIST: set[str] = set()  # none currently — all sleep() in tests is a design defect

# Files that legitimately do real I/O (e.g., test infrastructure,
# memory regression tests requiring real filesystem allocations).
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
    # Template rendering tests that read Jinja2 template files from the repo.
    # These tests verify template logic against real template content; mocking
    # the file reads would test nothing meaningful.
    "test_analysis_context_partial_analysis_context_path_behavior",
    "test_analysis_context_partial_analysis_context_path_only_behavior",
    "test_analysis_context_partial_analysis_context_rendering",
    "test_analysis_context_partial_analysis_context_suppression",
    "test_analysis_prompt_payload_contract_analysis_template_payload_contract",
    "test_analysis_prompt_payload_contract_retry_hint_guard_in_templates",
    # AST inspection tests that read production Python source files to enforce
    # invariants (e.g., no hardcoded phase names). The read target IS the
    # subject under test — replacing with mocked content would defeat the purpose.
    "test_no_hardcoded_phase_names_artifact_tool_has_no_canonical_drain_names",
    "test_no_hardcoded_phase_names_display_layer_has_no_canonical_phase_names",
    "test_no_hardcoded_phase_names_handoffs_has_no_canonical_phase_names",
    "test_no_hardcoded_phase_names_materialize_has_no_canonical_phase_names",
    "test_no_hardcoded_phase_names_register_role_handlers_is_generic",
    "test_no_hardcoded_phase_names_runner_artifact_handoff_is_generic",
    "test_no_hardcoded_phase_names_runner_has_no_canonical_phase_names",
    # Static analysis tests that read Python source or documentation files
    # from the repo to enforce structural invariants.
    "test_parallel_no_worktree_imports",
    "test_repo_root_operational_docs_sync",
    # Git integration tests using the tmp_git_repo fixture (which wraps
    # tmp_path). The write_text calls go to the fixture's temp directory.
    "test_git_rebase_preconditions",
    "test_git_wrapper",
    # Helper backend classes: write_text is a method on a custom backend
    # object (MemoryBackend subclass), not a Path.write_text() call.
    # The audit tool's AST heuristic cannot distinguish these.
    "test_tool_artifact_1_helper_failingartifactbackend",
    "test_tool_artifact_2_helper_failingartifactbackend",
    # XDG_CONFIG_HOME redirect writes to tmp_path subdir — real path is the test's assertion target.
    "test_get_or_create_uses_xdg_config_home",
}

# Files that legitimately use time.monotonic()/time.perf_counter() for
# non-circumvention purposes (single-point measurements, FakeClock
# comparison, timing correctness assertions).
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
    "test_hard_kill_helper_sleeperexecutor",
    # Performance regression test: measures real subscriber dispatch latency.
    # Wall-clock measurement IS the correctness assertion.
    "test_subscriber_performance",
}

# Path I/O methods that indicate real filesystem access.
_PATH_IO_METHODS: frozenset[str] = frozenset(
    {"read_text", "write_text", "read_bytes", "write_bytes", "open"}
)

# Patterns that monkeypatch away real I/O — these are legitimate.
_MONKEYPATCH_PATTERNS: set[str] = {
    "monkeypatch.setattr",
    "monkeypatch.setenv",
    "monkeypatch.delenv",
    "monkeypatch.setitem",
    "monkeypatch.delitem",
    "unittest.mock.patch",
    "mock.patch",
    "mocker.patch",
    "patch(",
}

# Sleep calls that are explicitly allowed (e.g., asyncio.sleep(0) which yields).
_ALLOWED_SLEEP_PATTERNS: set[str] = {
    "asyncio.sleep(0)",
    "asyncio.sleep(0.0)",
    "time.sleep(0)",
    "time.sleep(0.0)",
}


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

    def __init__(self, file_path: str, source: str) -> None:
        self.file_path = file_path
        self.source_lines = source.splitlines()
        self.violations: list[TestPolicyViolation] = []
        self._has_monkeypatch = any(
            pattern in source for pattern in _MONKEYPATCH_PATTERNS
        )
        self._has_subprocess_e2e_marker = "subprocess_e2e" in source
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

        # Check if this is inside a monkeypatch context — if the file
        # has monkeypatch patterns, assume sleep is being patched.
        if self._has_monkeypatch:
            return

        # Check if the argument is a literal 0.
        if node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant):
                if isinstance(first_arg.value, (int, float)) and first_arg.value <= 0:
                    return  # asyncio.sleep(0) is allowed
                elif isinstance(first_arg.value, (int, float)) and first_arg.value > 0:
                    self._add_violation(
                        node,
                        "sleep",
                        f"{func_name}({first_arg.value}) — "
                        "sleep with positive argument is a design defect",
                    )
                    return
            # If the argument is not a constant, flag it — any non-trivial sleep is suspicious.
            self._add_violation(
                node,
                "sleep",
                f"{func_name}(...) — dynamic sleep call; inject a clock abstraction instead",
            )

    def _check_io_call(self, node: ast.Call) -> None:  # noqa: PLR0911
        """Detect real I/O operations."""
        func_name = self._get_func_name(node)
        if func_name is None:
            # Check for Path(expr).method() pattern where receiver is a Call
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in _PATH_IO_METHODS
                and not self._has_monkeypatch
                and not self._is_using_tmp_path()
            ):
                self._add_violation(
                    node,
                    "io",
                    f".{node.func.attr}() — real filesystem I/O in test; use tmp_path fixture",
                )
            return

        # Detect open() calls (file I/O).
        if func_name == "open":
            # Monkeypatch presence makes open() OK — it's being faked.
            if self._has_monkeypatch:
                return
            self._add_violation(
                node,
                "io",
                "open() — real file I/O in test; use MemoryWorkspace, tmp_path, or monkeypatch",
            )
            return

        # Detect Path().read_text / write_text etc.
        if any(func_name == f"Path.{m}" for m in _PATH_IO_METHODS):
            if self._has_monkeypatch:
                return
            # Allow if using tmp_path (detected via string pattern).
            if self._is_using_tmp_path():
                return
            self._add_violation(
                node,
                "io",
                f"{func_name}() — real filesystem I/O in test; use tmp_path fixture",
            )
            return

        # Detect subprocess calls.
        if func_name in (
            "subprocess.run",
            "subprocess.Popen",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "asyncio.create_subprocess_exec",
            "asyncio.create_subprocess_shell",
            "os.system",
            "os.popen",
        ):
            # Allow if file is marked subprocess_e2e (excluded at file level).
            if self._has_subprocess_e2e_marker:
                return
            self._add_violation(
                node,
                "io",
                f"{func_name}() — subprocess call in test; use MockProcessExecutor",
            )
            return

        # Detect network I/O.
        if func_name in (
            "socket.socket",
            "socket.create_connection",
            "urllib.request.urlopen",
            "requests.get",
            "requests.post",
            "httpx.get",
            "httpx.post",
        ):
            if self._has_monkeypatch:
                return
            self._add_violation(
                node,
                "io",
                f"{func_name}() — network I/O in test; use mock/patch at the boundary",
            )
            return

    def _check_wall_clock_call(self, node: ast.Call) -> None:
        """Detect time.monotonic()/time.perf_counter() in elapsed-time loops."""
        func_name = self._get_func_name(node)
        if func_name is None:
            return

        if func_name in ("time.monotonic", "time.perf_counter"):
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

        # threading.Event().wait(), asyncio.Event().wait(), etc.
        if func_name.endswith(".wait") and not node.args and not node.keywords:
            if self._inside_wait_for:
                return
            self._add_violation(
                node,
                "blocking-wait",
                f"{func_name}() without timeout - "
                "blocking wait in test; always specify a timeout",
            )

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


def _collect_subprocess_e2e_files(tests_root: Path) -> set[str]:
    """Find all test files marked with @pytest.mark.subprocess_e2e.

    Uses string pre-filter then AST parsing for accuracy. Files that
    contain 'subprocess_e2e' in docstrings/comments but lack the actual
    marker are NOT added (string-only false positives are excluded).
    """
    e2e_files: set[str] = set()
    for py_file in tests_root.rglob("*.py"):
        # Skip directories.
        if any(part in _SKIP_DIRS for part in py_file.relative_to(tests_root).parts):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Fast string pre-filter.
        if "subprocess_e2e" not in content:
            continue
        # AST-based confirmation: check for @pytest.mark.subprocess_e2e decorator
        # or pytestmark assignment.
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            # If we cannot parse, fall back to string-based (conservative).
            e2e_files.add(py_file.stem)
            continue
        has_marker = _has_subprocess_e2e_marker_ast(tree)
        if has_marker:
            e2e_files.add(py_file.stem)
    return e2e_files


def _has_subprocess_e2e_marker_ast(tree: ast.AST) -> bool:
    """Check AST for @pytest.mark.subprocess_e2e decorator or pytestmark."""
    for node in ast.walk(tree):
        # Check for @pytest.mark.subprocess_e2e decorator.
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                if _is_subprocess_e2e_decorator(decorator):
                    return True
        # Check for module-level pytestmark = pytest.mark.subprocess_e2e
        # or pytestmark = [pytest.mark.subprocess_e2e]
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


def audit_test_file(file_path: Path) -> list[TestPolicyViolation]:  # noqa: PLR0911
    """Audit a single test file for policy violations.

    Returns a list of violations found.
    """
    if not file_path.is_file() or file_path.suffix != ".py":
        return []

    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    # Skip subprocess_e2e marked files.
    file_stem = file_path.stem

    if file_stem in _SUBPROCESS_E2E_FILES:
        return []

    # Skip files in the sleep allowlist.
    if file_stem in _SLEEP_ALLOWLIST:
        return []

    # Skip files in the I/O allowlist.
    if file_stem in _IO_ALLOWLIST:
        return []

    # Skip files in the wall-clock allowlist (legitimate time.monotonic()/
    # time.perf_counter() for single-point measurements, FakeClock
    # comparison, or timing correctness assertions).
    if file_stem in _WALL_CLOCK_ALLOWLIST:
        return []

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        # Skip files with syntax errors — not our concern.
        return []

    auditor = TestPolicyAuditor(str(file_path), source)
    auditor.visit(tree)

    return auditor.violations


def audit_tests_directory(tests_root: Path) -> tuple[list[TestPolicyViolation], int]:
    """Audit all test files in a directory.

    Returns (violations, files_checked).
    """
    global _SUBPROCESS_E2E_FILES  # noqa: PLW0603
    _SUBPROCESS_E2E_FILES = _collect_subprocess_e2e_files(tests_root)

    all_violations: list[TestPolicyViolation] = []
    files_checked = 0

    for py_file in sorted(tests_root.rglob("*.py")):
        # Skip excluded directories.
        if any(part in _SKIP_DIRS for part in py_file.relative_to(tests_root).parts):
            continue
        # Skip test_process_audit.py itself and this audit file.
        if py_file.name in ("test_process_audit.py", "audit_test_policy.py"):
            continue
        if "/fixtures/" in py_file.as_posix():
            continue
        violations = audit_test_file(py_file)
        if violations:
            all_violations.extend(violations)
        files_checked += 1

    return all_violations, files_checked


def main(argv: list[str] | None = None) -> int:
    """Run the policy audit and return exit code.

    Exit code 0: no violations found.
    Exit code 1: violations found.
    """
    args = argv if argv is not None else sys.argv[1:]

    tests_root = Path(args[0]) if args else (
        Path(__file__).parent.parent.parent / "tests"
    )

    if not tests_root.is_dir():
        print(f"Error: tests directory not found: {tests_root}", file=sys.stderr)
        return 2

    print(f"Auditing test files in: {tests_root}")
    print(f"Subprocess E2E files excluded: {len(_SUBPROCESS_E2E_FILES)}")
    print()

    violations, files_checked = audit_tests_directory(tests_root)

    if violations:
        print(f"POLICY VIOLATIONS FOUND: {len(violations)} violation(s) in {files_checked} file(s)")
        print("=" * 72)
        for v in violations:
            print(f"  {v}")
        print()
        print("These violations are test design defects. Fix the test, not the audit.")
        print("Guidance: docs/agents/testing-guide.md §'Test Performance Policy'")
        return 1

    print(f"No policy violations found in {files_checked} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
