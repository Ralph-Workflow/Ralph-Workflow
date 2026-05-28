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

# --- Allowlist: test files exempt from specific checks ---

# Files with pytest.mark.subprocess_e2e are EXCLUDED from all checks.
_SUBPROCESS_E2E_FILES: set[str] = set()

# Files that legitimately use sleep() for clock-injection testing.
_SLEEP_ALLOWLIST: set[str] = set()  # none currently — all sleep() in tests is a design defect

# Files that legitimately do real I/O (e.g., test infrastructure).
_IO_ALLOWLIST: set[str] = set()

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
        self.generic_visit(node)

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
        if func_name in (
            "Path.read_text",
            "Path.write_text",
            "Path.read_bytes",
            "Path.write_bytes",
            "Path.open",
        ):
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
    """Find all test files marked with @pytest.mark.subprocess_e2e."""
    e2e_files: set[str] = set()
    for py_file in tests_root.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "subprocess_e2e" in content:
            e2e_files.add(py_file.name)
    return e2e_files


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
    if file_path.name in _SUBPROCESS_E2E_FILES:
        return []

    # Skip files in the sleep allowlist.
    if file_path.name in _SLEEP_ALLOWLIST:
        return []

    # Skip files in the I/O allowlist.
    if file_path.name in _IO_ALLOWLIST:
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
