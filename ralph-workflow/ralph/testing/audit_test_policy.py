"""Policy compliance audit for test files.

Uses AST analysis (not regex) to detect policy violations in test code:
- sleep() calls with non-zero arguments
- Real I/O operations (file I/O, network, subprocess)
- GitPython real-Git boundary crossings (``Repo.init``, ``git.Repo``) in
  tests outside the ``subprocess_e2e`` profile and the sanctioned
  real-git registry
- White-box coupling to private ``ralph`` production internals
- White-box counts above the frozen baseline ceiling

It also reconciles the frozen baseline against the WHOLE tree, so a baseline
row that no longer corresponds to any coupling fails the gate instead of
rotting silently (see :mod:`ralph.testing.whitebox_reconciliation`).

Usage:
    python -m ralph.testing.audit_test_policy [tests_root]
    python -m ralph.testing.audit_test_policy --emit-baseline [tests_root]

Returns exit code 0 if no policy violations found, 1 otherwise.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.test_suites import REQUIRED_AUTO_INTEGRATE_E2E_FILES
from ralph.testing import test_whitebox_baseline
from ralph.testing.policy_allowlists import (
    IO_ALLOWLIST,
    SOURCE_READ_ALLOWLIST,
    WALL_CLOCK_ALLOWLIST,
)
from ralph.testing.test_whitebox_baseline import WHITEBOX_BASELINE
from ralph.testing.whitebox_detectors import (
    analyze_module,
    canonical_patch_target,
    canonical_target_string,
    collect_whitebox_counts,
    count_private_attrs,
    count_private_imports,
    count_setattr_private,
    has_subprocess_e2e_marker,
)
from ralph.testing.whitebox_reconciliation import (
    BASELINE_DRIFT_CATEGORIES,
    WhiteboxDrift,
    reconcile_whitebox_baseline,
)

if TYPE_CHECKING:
    from collections.abc import Collection

__all__ = [
    "AuditInventory",
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


def _git_bound_names(tree: ast.AST) -> frozenset[str]:
    """Return the names bound by imports of the ``git`` (GitPython) module.

    Covers ``import git[.sub]``, ``from git[.sub] import X`` and their
    aliased forms; the returned names are the roots that calls are
    matched against in :meth:`TestPolicyAuditor._check_git_call`.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "git" or alias.name.startswith("git."):
                    names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "git" or module.startswith("git."):
                for alias in node.names:
                    names.add(alias.asname or alias.name)
    return frozenset(names)


def _is_git_crossing_root(root: str) -> bool:
    """Return True when an imported ``git`` name crosses the real-Git boundary."""
    lowered = root.lower()
    return lowered in _GIT_CROSSING_ROOTS or lowered.endswith("repo")


def _is_registered_real_git_file(file_path: str) -> bool:
    """Return True when ``file_path`` is a sanctioned default-lane real-git file.

    The canonical testing policy (docs/ralph-workflow-policy/testing-policy.md)
    names the ``REQUIRED_AUTO_INTEGRATE_E2E_FILES`` registry as the single
    sanctioned exception that keeps real-git tests on the default
    ``make test`` profile; the audit honours the same registry so the two
    mechanisms cannot drift. Paths outside the project tree (e.g. the
    temporary files the audit's own unit tests write) are never registered.
    """
    try:
        relative = Path(file_path).resolve().relative_to(_PROJECT_ROOT).as_posix()
    except ValueError:
        return False
    return relative in REQUIRED_AUTO_INTEGRATE_E2E_FILES


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

#: Roots of dotted call names that cross the real-Git boundary when the
#: root is bound by a ``git`` (GitPython) import. ``Repo``/``Git`` entry
#: points shell out to the real git binary, so ``Repo.init(...)``,
#: ``Repo(path)``, ``git.Repo(...)`` and every call rooted at such a name
#: are real subprocess work. Aliases are recognised by the ``repo`` suffix
#: (``from git import Repo as GitRepo``). Exception and data classes
#: (``GitCommandError``, ``Actor``, ...) never touch a process and are not
#: crossing roots.
_GIT_CROSSING_ROOTS: frozenset[str] = frozenset({"repo", "git"})

# Files marked subprocess_e2e skip only subprocess and network checks.
_SUBPROCESS_E2E_FILES: set[str] = set()

# Sleep exemptions apply only to the sleep category.
_SLEEP_ALLOWLIST: set[str] = set()  # none currently — all sleep() in tests is a design defect

# Files in this list skip only the real-I/O category. The entries and
# the reason each one exists live in ralph.testing.policy_allowlists.
_IO_ALLOWLIST = IO_ALLOWLIST

# Files that may read only shipped repository source files. The entries
# and the reason each one exists live in ralph.testing.policy_allowlists.
_SOURCE_READ_ALLOWLIST = SOURCE_READ_ALLOWLIST

_PATH_READ_METHODS: frozenset[str] = frozenset({"read_text", "read_bytes"})

# Files that may measure real wall-clock time. The entries and the
# reason each one exists live in ralph.testing.policy_allowlists.
_WALL_CLOCK_ALLOWLIST = WALL_CLOCK_ALLOWLIST

_SMOKE_NAME_ALLOWLIST: set[str] = {
    "test_smoke_capability_breaks_when_supported_but_unrendered",
    "test_exec_smoke_subprocess_e2e",
}

_PATH_IO_METHODS: frozenset[str] = frozenset(
    {"read_text", "write_text", "read_bytes", "write_bytes", "open"}
)
_PATH_IO_CALLS: frozenset[str] = frozenset(f"Path.{method}" for method in _PATH_IO_METHODS)

# Patch calls are exemptions only for their exact dotted target.
_MONKEYPATCH_PATTERNS: set[str] = _split_names(
    """
    monkeypatch.setattr monkeypatch.setenv monkeypatch.delenv monkeypatch.setitem
    monkeypatch.delitem unittest.mock.patch mock.patch mocker.patch patch(
    """
)

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


@dataclass
class AuditInventory:
    """What the single per-file audit pass learns about the tree.

    The audit walks every test module ONCE. This collector receives what
    that walk learns, so the whole-tree reconciliation of the frozen
    baseline can run afterwards WITHOUT a second scan of the tree.
    """

    #: Fresh white-box counts keyed by project-relative test path. Only
    #: files with at least one non-zero count are present.
    counts: dict[str, WhiteboxCounts] = field(default_factory=dict)

    #: Every audited test path that still exists in the tree. It tells a
    #: DELETED file apart from a file whose coupling was converted away.
    existing_paths: set[str] = field(default_factory=set)

    #: The pytest markers declared by the most recently audited module.
    module_markers: set[str] = field(default_factory=set)


class TestPolicyAuditor:
    """Evaluate the test-policy categories of ONE parsed test module.

    Every fact the checks need — the white-box counts, the patched targets
    and the call sites — comes from ONE AST traversal
    (:func:`ralph.testing.whitebox_detectors.analyze_module`), so the
    whole-tree audit walks every test module once instead of once per
    detector.
    """

    def __init__(
        self,
        file_path: str,
        source: str,
        tree: ast.AST | None = None,
        *,
        monkeypatch_patterns: Collection[str] | None = None,
    ) -> None:
        self.file_path = file_path
        self.source_lines = source.splitlines()
        self.violations: list[TestPolicyViolation] = []
        stem = Path(file_path).stem
        self._source_read_exempt = stem in _SOURCE_READ_ALLOWLIST
        parsed_tree = tree if tree is not None else ast.parse(source, filename=file_path)
        patterns = _MONKEYPATCH_PATTERNS if monkeypatch_patterns is None else monkeypatch_patterns
        self.facts = analyze_module(parsed_tree, monkeypatch_patterns=patterns)
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
            stem in _SUBPROCESS_E2E_FILES or self.facts.has_subprocess_e2e_marker
        )
        self._skip_sleep = stem in _SLEEP_ALLOWLIST or self._is_subprocess_e2e
        self._skip_io = stem in _IO_ALLOWLIST or self._is_subprocess_e2e
        self._skip_wall_clock = stem in _WALL_CLOCK_ALLOWLIST or self._is_subprocess_e2e
        self._skip_subprocess_network = self._is_subprocess_e2e
        # GitPython crossings join the subprocess/network category: a
        # ``Repo.init``/``git.Repo`` call shells out to the real git
        # binary. The ``subprocess_e2e`` marker owns the category, and so
        # does the sanctioned real-git registry that keeps the registered
        # default-lane integration proofs on the ``make test`` profile.
        self._git_roots = _git_bound_names(parsed_tree)
        self._skip_git = self._is_subprocess_e2e or _is_registered_real_git_file(file_path)
        self._uses_tmp_path = "tmp_path" in "\n".join(self.source_lines)
        self._check_calls()

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

    def _check_calls(self) -> None:
        """Check every call site of the module, in source order."""
        for node, func_name, inside_wait_for in self.facts.call_sites:
            self._check_sleep_call(node, func_name)
            self._check_io_call(node, func_name)
            self._check_git_call(node, func_name)
            self._check_wall_clock_call(node, func_name)
            self._check_blocking_wait_call(node, func_name, inside_wait_for)

    def _check_sleep_call(self, node: ast.Call, func_name: str | None) -> None:
        """Detect time.sleep(N) and asyncio.sleep(N) where N > 0."""
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

    def _check_io_call(self, node: ast.Call, func_name: str | None) -> None:
        """Detect real I/O operations."""
        if self._skip_io:
            return
        if func_name is None:
            self._check_anonymous_path_io_call(node)
        elif func_name == "open":
            if not self._is_target_patched("builtins.open"):
                self._add_violation(
                    node,
                    "io",
                    "open() — real file I/O in test; use MemoryWorkspace, tmp_path, or monkeypatch",
                )
        elif func_name in _PATH_IO_CALLS:
            method_name = func_name.split(".", 1)[1]
            if self._is_source_read_exempt(method_name):
                return
            target = canonical_patch_target("Path", method_name)
            if not self._is_target_patched(target) and not self._uses_tmp_path:
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

    def _check_git_call(self, node: ast.Call, func_name: str | None) -> None:
        """Detect GitPython calls that cross the real-Git boundary.

        GitPython's ``Repo``/``Git`` entry points spawn the real git
        binary, so in a test outside the ``subprocess_e2e`` profile they
        are real subprocess work (the fixture-shaped violation: a helper
        module that builds a repository with ``Repo.init`` /
        ``repo.index.commit`` for a suite that never declares the
        marker).
        """
        if self._skip_git or func_name is None:
            return
        root = func_name.split(".", 1)[0]
        if root not in self._git_roots:
            return
        if not _is_git_crossing_root(root):
            return
        self._add_violation(
            node,
            "io",
            f"{func_name}() — real Git boundary crossing in test; "
            "mark the file subprocess_e2e or route repository setup "
            "through the simulated Git seam",
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
        if self._is_target_patched(target) or self._uses_tmp_path:
            return
        self._add_violation(
            node,
            "io",
            f".{node.func.attr}() — real filesystem I/O in test; use tmp_path fixture",
        )

    def _check_wall_clock_call(self, node: ast.Call, func_name: str | None) -> None:
        """Detect time.monotonic()/time.perf_counter() in elapsed-time loops."""
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

    def _check_blocking_wait_call(
        self,
        node: ast.Call,
        func_name: str | None,
        inside_wait_for: bool,
    ) -> None:
        """Detect .wait() calls without a timeout argument."""
        if func_name is None:
            return
        if func_name.endswith(".wait") and not node.args and not node.keywords:
            if inside_wait_for:
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
        return canonical in self.facts.patched_targets


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
        if has_subprocess_e2e_marker(tree):
            e2e_files.add(py_file.stem)
    return e2e_files


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
    inventory: AuditInventory | None = None,
) -> list[TestPolicyViolation]:
    """Audit a single test file for policy violations.

    Args:
        file_path: The test file to audit.
        baseline: The frozen ceilings to compare against.
        baseline_key: The project-relative key of ``file_path`` in the
            frozen baseline.
        inventory: The collector that receives what this pass learns
            (the fresh white-box counts, the audited path and the declared
            pytest markers). It lets the whole-tree reconciliation run
            afterwards without a SECOND scan of the tree.
    """
    if not file_path.is_file() or file_path.suffix != ".py":
        return []
    key = baseline_key or _baseline_key(file_path)
    if inventory is not None:
        # The file exists, so it is not a DELETED baseline row, and it
        # declares no marker the audit could read.
        inventory.existing_paths.add(key)
        inventory.module_markers = set()
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []

    auditor = TestPolicyAuditor(str(file_path), source, tree)
    effective_baseline = WHITEBOX_BASELINE if baseline is None else baseline
    auditor.violations.extend(
        audit_whitebox_ratchet(
            file_path,
            auditor.facts.counts,
            effective_baseline,
            baseline_key,
        )
    )
    if inventory is not None:
        inventory.module_markers = set(auditor.facts.markers)
        if any(count > 0 for count in auditor.facts.counts.values()):
            inventory.counts[key] = dict(auditor.facts.counts)
    return auditor.violations


def audit_tests_directory(
    tests_root: Path,
    *,
    baseline: WhiteboxBaseline | None = None,
    inventory: AuditInventory | None = None,
) -> tuple[list[TestPolicyViolation], int]:
    """Audit all test files in a directory."""
    _SUBPROCESS_E2E_FILES.clear()
    _SUBPROCESS_E2E_FILES.update(_collect_subprocess_e2e_files(tests_root))

    collector = AuditInventory() if inventory is None else inventory
    all_violations: list[TestPolicyViolation] = []
    files_checked = 0

    for py_file in _iter_test_files(tests_root):
        if py_file.name in ("test_process_audit.py", "audit_test_policy.py"):
            collector.existing_paths.add(_baseline_key(py_file, tests_root))
            continue
        if "/fixtures/" in py_file.as_posix():
            collector.existing_paths.add(_baseline_key(py_file, tests_root))
            continue
        violations = audit_test_file(
            py_file,
            baseline=baseline,
            baseline_key=_baseline_key(py_file, tests_root),
            inventory=collector,
        )
        if violations:
            all_violations.extend(violations)
        if (
            "smoke" in py_file.stem.lower()
            and py_file.stem not in _SMOKE_NAME_ALLOWLIST
            and "smoke" not in collector.module_markers
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


def _describes_baseline_tree(tests_root: Path) -> bool:
    """Return True when ``tests_root`` is the tree the frozen baseline describes.

    The frozen baseline records paths relative to the ``ralph-workflow/``
    project root (``tests/...``). A whole-tree reconciliation therefore only
    makes sense for the project's canonical ``tests/`` tree: auditing a
    temporary directory (as the unit tests do) describes a DIFFERENT tree,
    and every frozen row would look like a removed file.
    """
    return tests_root.resolve() == (_PROJECT_ROOT / "tests").resolve()


def reconcile_baseline(
    tests_root: Path,
    inventory: AuditInventory,
    baseline: WhiteboxBaseline,
) -> list[WhiteboxDrift]:
    """Return the baseline rows that disagree with the whole tree.

    The per-file ratchet only ever sees ONE file at a time, so it cannot
    see a baseline row that no longer corresponds to any coupling. This
    whole-tree pass closes that gap. The ``whitebox-regression`` direction
    is deliberately left out: the per-file ratchet already reports it, and
    the audit must not print it twice.
    """
    if not _describes_baseline_tree(tests_root):
        return []
    drifts = reconcile_whitebox_baseline(
        inventory.counts,
        baseline,
        existing_paths=inventory.existing_paths,
    )
    return [drift for drift in drifts if drift.category in BASELINE_DRIFT_CATEGORIES]


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

    effective_baseline = WHITEBOX_BASELINE if baseline is None else baseline
    collector = AuditInventory()
    violations, files_checked = audit_tests_directory(
        tests_root,
        baseline=effective_baseline,
        inventory=collector,
    )
    drifts = reconcile_baseline(tests_root, collector, effective_baseline)
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

    if drifts:
        print(f"WHITE-BOX BASELINE DRIFT: {len(drifts)} row(s) disagree with the frozen baseline")
        print("=" * 72)
        for drift in drifts:
            print(f"  {drift.path}: [{drift.category}] {drift.detail}")
        print()
        print("Fix the FROZEN BASELINE, not the audit:")
        print("  python -m ralph.testing.audit_test_policy --emit-baseline")
        print("lowers the ceiling to the current count; update the matching ledger")
        print("row in docs/agents/testing-guide.md in the SAME edit, because a")
        print("lowered ceiling without a matching ledger row leaves the ledger")
        print("claiming coupling the tree no longer has.")

    if violations or drifts:
        return 1

    print(f"No policy violations found in {files_checked} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
