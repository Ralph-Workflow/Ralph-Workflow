"""Test-integrity gate — a test may not be weakened to let a change pass.

The gate has two halves.

TEST WEAKENING (DIFF-DRIVEN)

    A diff that removes or changes an existing assertion, fixture or
    expected value in ``tests/`` *in the same change as production code* is
    a finding, unless the change records WHY.

    "In the same change as production code" is the coupling that makes the
    rule meaningful: an agent that changes shipped behaviour and relaxes the
    test that pinned it has made the suite agree with itself instead of with
    the contract. A diff that only ADDS tests, or that only touches test
    files, carries no production coupling and is never reported.

    The coupling is deliberately broad: ANY non-test file changed in the same
    diff counts as the production code in question. A tighter
    test-file-to-module mapping would be evadable by renaming the module,
    and the finding lists the coupled production paths so a reviewer can
    judge the coupling directly.

    The recorded reason is an inline marker on or next to the changed check:

        # RALPH-TEST-REASON: <why the check changed>

    The marker is honoured anywhere inside the same diff hunk (on the
    removed line, on an added line, or on a context line next to the
    change). The marker constant is :data:`TEST_REASON_MARKER`.

    A removed check is NOT reported when the hunk replaces it with an
    equivalent-or-stronger check: the same line moved, the same assertion
    with an extra constraint, or a bare assertion replaced by a specific
    one. A changed EXPECTED VALUE (``assert x == 5`` -> ``assert x == 6``)
    IS reported — that is the classic "make the test agree with the new
    behaviour" edit.

PYTEST DETECTION IN PRODUCTION CODE (AST)

    Shipped code must not sniff the test runner. A production module that
    reads the pytest current-test environment variable, or looks the pytest
    package up in ``sys.modules``, has changed its behaviour under test —
    the test then proves nothing about the shipped path.

    The detector is AST-based (not regex), so prose that merely names the
    environment variable is not a finding; only real reads are. Import
    aliases (``import sys as s``) are resolved.

INPUT SEAM

    audit_test_integrity_diff(diff_text) -> list[TestIntegrityFinding]
    audit_pytest_detection(root) -> list[TestIntegrityFinding]

    The diff seam takes a UNIFIED DIFF STRING: the caller supplies it (the
    coordinator wires a git-diff based baseline) and the gate never shells
    out, never reads version control and never touches the network, so a
    synthetic diff exercises every rule offline and deterministically. The
    detection seam takes a repository ROOT and reads the production package
    under it.

Usage:
    python -m ralph.testing.audit_test_integrity [--diff PATH|-] [--root DIR]

``--diff`` takes the path of a unified diff file (``-`` reads stdin) and
enables the test-weakening half. ``--root`` overrides the repository root
(default: the package root's parent). The pytest-detection half always
scans the production package under the root.

Exit 0 = clean, 1 = findings, 2 = usage error / missing root / unreadable diff.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing._audit_baseline import worktree_diff

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "CATEGORY_PYTEST_DETECTION",
    "CATEGORY_TEST_WEAKENING",
    "PRODUCTION_PACKAGE_DIR",
    "TEST_REASON_MARKER",
    "TEST_TREE_DIR",
    "TestIntegrityFinding",
    "audit_pytest_detection",
    "audit_test_integrity_diff",
    "main",
]

#: The recorded-reason marker. A removed or changed test check is accepted
#: only when the diff hunk carries this marker in the form
#: ``# RALPH-TEST-REASON: <why the check changed>``.
TEST_REASON_MARKER = "RALPH-TEST-REASON:"

#: Finding categories. Stable strings: the verify step and any waiver record
#: key on them.
CATEGORY_TEST_WEAKENING = "test-weakening"
CATEGORY_PYTEST_DETECTION = "pytest-detection"

#: Directory name that marks a test tree in a repository-relative path.
TEST_TREE_DIR = "tests"

#: Directory name of the shipped package (the production code).
PRODUCTION_PACKAGE_DIR = "ralph"

#: The pytest current-test environment variable. Assembled from fragments so
#: this module does not match its own detector (the same self-scan trick the
#: repo-structure audit uses for its bypass-comment markers).
_PYTEST_ENV_TOKEN = "PYTEST_CURRENT" + "_TEST"

#: The module name looked up in ``sys.modules`` by a test-runner probe.
_PYTEST_MODULE_NAME = "pytest"

#: Detector names reported in a finding detail.
_DETECTOR_ENV = "pytest current-test environment variable"
_DETECTOR_MODULE = "pytest module lookup"

#: The kinds of test check the diff half recognises.
_KIND_ASSERTION = "assertion"
_KIND_FIXTURE = "pytest fixture"
_KIND_RAISES = "raises/warns expectation"
_KIND_EXPECTED_VALUE = "expected-value constant"

#: An assertion statement (the line starts with ``assert``).
_ASSERT_RE = re.compile(r"^assert\b")

#: A pytest fixture decorator.
_FIXTURE_RE = re.compile(r"^@\s*(?:pytest\s*\.\s*)?fixture\b")

#: A ``pytest.raises`` / ``pytest.warns`` expectation.
_RAISES_RE = re.compile(r"\bpytest\s*\.\s*(?:raises|warns)\s*\(")

#: The first comparison operator of an assertion expression.
_COMPARISON_RE = re.compile(
    r"^(?P<lhs>.*?)\s*(?P<op>==|!=|<=|>=|<|>|\bis\s+not\b|\bis\b|\bnot\s+in\b|\bin\b)\s*(?P<rhs>.*)$"
)

#: Every constraint an assertion expression carries.
_CONSTRAINT_RE = re.compile(r"==|!=|<=|>=|<|>|\bis\s+not\b|\bis\b|\bnot\s+in\b|\bin\b")

#: Mapping from AST comparison operator types to their canonical string representations.
_OP_MAP: dict[type[ast.cmpop], str] = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
}

#: A module-level constant assignment (``NAME = value`` / ``NAME: T = value``).
_CONST_ASSIGN_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=")

#: The arguments of a ``pytest.raises`` / ``pytest.warns`` call.
_RAISES_CALL_RE = re.compile(r"pytest\s*\.\s*(?:raises|warns)\s*\((?P<args>[^)]*)\)")

#: A unified-diff hunk header.
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

#: Directories never scanned by the production-code half.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "htmlcov",
        "tmp",
    }
)

#: Maximum number of coupled production paths quoted in a finding detail.
_MAX_COUPLED_PATHS_IN_DETAIL = 3

#: Maximum length of the quoted removed line in a finding detail.
_MAX_QUOTED_LINE = 120

_USAGE = "Usage: python -m ralph.testing.audit_test_integrity [--diff PATH|-] [--root DIR]"


@dataclass(frozen=True)
class TestIntegrityFinding:
    """One test-integrity finding."""

    # Prevent pytest from trying to collect this class as a test class.
    __test__ = False

    file_path: str
    line: int
    category: str
    detail: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


@dataclass(frozen=True)
class _Check:
    """One test check (assertion, fixture, expectation or expected value)."""

    kind: str
    text: str
    lhs: str
    operator: str | None
    expected: str | None
    constraints: int


@dataclass(frozen=True)
class _Hunk:
    """One unified-diff hunk: its removed, added and context lines."""

    removed: tuple[tuple[int, str], ...]
    added: tuple[tuple[int, str], ...]
    context: tuple[tuple[int, str], ...]

    def all_lines(self) -> tuple[tuple[int, str], ...]:
        """Return every line of the hunk (removed, added and context)."""
        return (*self.removed, *self.added, *self.context)


@dataclass(frozen=True)
class _DiffFile:
    """One file's hunks inside a unified diff."""

    path: str
    hunks: tuple[_Hunk, ...]


def _group_str(match: re.Match[str], group: str) -> str:
    """Return a named regex group as ``str`` (never ``Any``)."""
    value = match.group(group)
    if not isinstance(value, str):
        msg = "expected regex capture group to be str"
        raise TypeError(msg)
    return value


def _normalize_diff_path(raw: str) -> str:
    """Return the repository-relative path carried by a diff header line."""
    path = raw.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return ""
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def _parse_unified_diff(diff_text: str) -> list[_DiffFile]:
    """Return the files and hunks carried by a unified diff."""
    files: list[_DiffFile] = []
    old_path = ""
    new_path = ""
    hunks: list[_Hunk] = []
    removed: list[tuple[int, str]] = []
    added: list[tuple[int, str]] = []
    context: list[tuple[int, str]] = []
    old_line = 0
    new_line = 0
    in_hunk = False

    def close_hunk() -> None:
        nonlocal removed, added, context, in_hunk
        if in_hunk:
            hunks.append(_Hunk(removed=tuple(removed), added=tuple(added), context=tuple(context)))
        removed = []
        added = []
        context = []
        in_hunk = False

    def close_file() -> None:
        nonlocal hunks, old_path, new_path
        close_hunk()
        path = new_path or old_path
        if path and hunks:
            files.append(_DiffFile(path=path, hunks=tuple(hunks)))
        hunks = []
        old_path = ""
        new_path = ""

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            close_file()
            continue
        if raw.startswith("--- "):
            old_path = _normalize_diff_path(raw[4:])
            continue
        if raw.startswith("+++ "):
            new_path = _normalize_diff_path(raw[4:])
            continue
        header = _HUNK_HEADER_RE.match(raw)
        if header is not None:
            close_hunk()
            in_hunk = True
            old_line = int(header.group(1))
            new_line = int(header.group(3))
            continue
        if not in_hunk:
            continue
        if raw.startswith("+"):
            added.append((new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-"):
            removed.append((old_line, raw[1:]))
            old_line += 1
        elif raw.startswith("\\"):
            continue
        else:
            context.append((new_line, raw[1:] if raw.startswith(" ") else raw))
            old_line += 1
            new_line += 1
    close_file()
    return files


def _is_test_path(path: str) -> bool:
    """Return True when *path* belongs to a test tree."""
    parts = path.split("/")
    if TEST_TREE_DIR in parts[:-1]:
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def _is_production_path(path: str) -> bool:
    """Return True when *path* is production code (not a test file)."""
    return not _is_test_path(path)


def _count_constraints(expression: str) -> int:
    """Return the number of constraints an assertion expression carries."""
    return sum(1 for _ in _CONSTRAINT_RE.finditer(expression))


def _assertion_test_node(text: str) -> ast.expr | None:
    """Return the AST test expression of an assertion statement, or None."""
    stripped = text.strip()
    if not stripped.startswith("assert"):
        return None
    try:
        mod = ast.parse(stripped)
    except SyntaxError:
        return None
    if mod.body and isinstance(mod.body[0], ast.Assert):
        return mod.body[0].test
    return None


def _compare_ordered_numeric(op: ast.cmpop, left: int | float, right: int | float) -> bool:
    """Evaluate an ordering comparison between two numeric constants."""
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    return False


def _compare_ordered_str(op: ast.cmpop, left: str, right: str) -> bool:
    """Evaluate an ordering comparison between two string constants."""
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    return False


def _evaluate_ordered_compare(op: ast.cmpop, left: object, right: object) -> bool:
    """Evaluate an ordering comparison between two constants."""
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return _compare_ordered_numeric(op, left, right)
    if isinstance(left, str) and isinstance(right, str):
        return _compare_ordered_str(op, left, right)
    return False


def _evaluate_constant_compare(op: ast.cmpop, left: object, right: object) -> bool:
    """Evaluate a comparison between two constant values."""
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Is):
        return left is right
    if isinstance(op, ast.IsNot):
        return left is not right
    return _evaluate_ordered_compare(op, left, right)


def _split_comparison(expression: str) -> tuple[str, str | None, str | None]:
    """Return ``(lhs, operator, expected)`` of an assertion expression."""
    test = _assertion_test_node(f"assert {expression}")
    if test is not None:
        if isinstance(test, ast.Compare) and len(test.ops) >= 1:
            op_type = type(test.ops[0])
            if op_type in _OP_MAP:
                lhs = ast.unparse(test.left).strip()
                op = _OP_MAP[op_type]
                expected = ast.unparse(test.comparators[0]).strip()
                return lhs, op, expected
        return ast.unparse(test).strip(), None, None
    match = _COMPARISON_RE.match(expression)
    if match is None:
        return expression, None, None
    lhs = _group_str(match, "lhs").strip()
    operator = _group_str(match, "op")
    expected = _group_str(match, "rhs").strip()
    return lhs, operator, expected


def _assertion_check(text: str) -> _Check:
    """Return the check described by an assertion statement."""
    expression = text[len("assert") :].strip()
    lhs, operator, expected = _split_comparison(expression)
    return _Check(
        kind=_KIND_ASSERTION,
        text=text,
        lhs=lhs,
        operator=operator,
        expected=expected,
        constraints=_count_constraints(expression),
    )


def _raises_check(text: str) -> _Check:
    """Return the check described by a ``pytest.raises`` / ``pytest.warns`` line."""
    match = _RAISES_CALL_RE.search(text)
    arguments = _group_str(match, "args").strip() if match is not None else ""
    first = arguments.split(",")[0].strip() if arguments else ""
    return _Check(
        kind=_KIND_RAISES,
        text=text,
        lhs="",
        operator=None,
        expected=first or None,
        constraints=1 + arguments.count(","),
    )


def _constant_value(text: str) -> str | None:
    """Return the assigned value of a module-level constant assignment."""
    match = _CONST_ASSIGN_RE.match(text)
    if match is None:
        return None
    return text[match.end() :].strip()


def _is_expected_constant(text: str) -> bool:
    """Return True when *text* assigns an UPPER-CASE ``EXPECT``-named constant."""
    match = _CONST_ASSIGN_RE.match(text)
    if match is None:
        return False
    name = _group_str(match, "name")
    return name.isupper() and "EXPECT" in name


def _classify_check(text: str) -> _Check | None:
    """Return the check a changed test line carries, or None when it carries none."""
    stripped = text.strip()
    if not stripped:
        return None
    if _ASSERT_RE.match(stripped):
        return _assertion_check(stripped)
    if _FIXTURE_RE.match(stripped):
        return _Check(
            kind=_KIND_FIXTURE,
            text=stripped,
            lhs="",
            operator=None,
            expected=None,
            constraints=0,
        )
    if _RAISES_RE.search(stripped):
        return _raises_check(stripped)
    if _is_expected_constant(stripped):
        return _Check(
            kind=_KIND_EXPECTED_VALUE,
            text=stripped,
            lhs="",
            operator=None,
            expected=_constant_value(stripped),
            constraints=0,
        )
    return None


def _is_tautological(text: str) -> bool:
    """Return True when an assertion is tautological (always passes unconditionally)."""
    test = _assertion_test_node(text)
    if test is None:
        return False
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
        op = test.ops[0]
        comparator = test.comparators[0]
        if isinstance(op, (ast.Eq, ast.Is, ast.LtE, ast.GtE)) and ast.dump(test.left) == ast.dump(
            comparator
        ):
            return True
        if isinstance(test.left, ast.Constant) and isinstance(comparator, ast.Constant):
            return _evaluate_constant_compare(op, test.left.value, comparator.value)
    return False


def _replacement_is_allowed(removed: _Check, added: Sequence[_Check]) -> bool:
    """Return True when the hunk replaces *removed* with an equal-or-stronger check.

    An identical line is a move, not a weakening. An assertion may also be
    replaced by a STRONGER one: the same expression with more constraints,
    or a bare assertion replaced by a specific one. Everything else — a
    changed expected value, a different expression, a changed fixture — is a
    weakening and is reported.
    """
    for candidate in added:
        if candidate.kind != removed.kind:
            continue
        if candidate.kind == _KIND_ASSERTION and _is_tautological(candidate.text):
            continue
        if candidate.text == removed.text:
            return True
        if candidate.constraints < removed.constraints:
            continue
        if removed.expected is not None and candidate.expected != removed.expected:
            continue
        if removed.operator is not None and candidate.operator != removed.operator:
            continue
        if candidate.lhs != removed.lhs:
            continue
        if (
            removed.operator is None
            and candidate.operator is None
            and candidate.constraints == removed.constraints
        ):
            continue
        return True
    return False


def _hunk_has_reason(hunk: _Hunk) -> bool:
    """Return True when the hunk carries a nonempty recorded-reason marker."""
    for _, text in hunk.all_lines():
        if TEST_REASON_MARKER in text:
            reason = text.split(TEST_REASON_MARKER, 1)[1].strip()
            if reason:
                return True
    return False


def _summarize_paths(paths: Sequence[str]) -> str:
    """Return a short, quoted summary of the coupled production paths."""
    shown = list(paths[:_MAX_COUPLED_PATHS_IN_DETAIL])
    summary = ", ".join(shown)
    if len(paths) > len(shown):
        summary = f"{summary} (+{len(paths) - len(shown)} more)"
    return summary


def _quote(text: str) -> str:
    """Return a one-line, length-capped quotation of *text*."""
    single_line = " ".join(text.split())
    if len(single_line) > _MAX_QUOTED_LINE:
        single_line = f"{single_line[:_MAX_QUOTED_LINE]}..."
    return f"'{single_line}'"


def _weakening_detail(check: _Check, production_summary: str) -> str:
    """Return the actionable detail of one test-weakening finding."""
    return (
        f"{check.kind} removed or weakened in the same change as production code "
        f"({production_summary}); removed line: {_quote(check.text)}. "
        f"fix: restore the check, or record the reason inline as "
        f"'# {TEST_REASON_MARKER} <why the check changed>' on or next to the changed line"
    )


def _hunk_findings(path: str, hunk: _Hunk, production_summary: str) -> list[TestIntegrityFinding]:
    """Return the weakening findings of one hunk of a test file."""
    if _hunk_has_reason(hunk):
        return []
    removed_checks: list[tuple[int, _Check]] = []
    for line, text in hunk.removed:
        check = _classify_check(text)
        if check is not None:
            removed_checks.append((line, check))
    if not removed_checks:
        return []
    added_checks: list[_Check] = []
    for _, text in hunk.added:
        check = _classify_check(text)
        if check is not None:
            added_checks.append(check)
    findings: list[TestIntegrityFinding] = []
    for line, check in removed_checks:
        if _replacement_is_allowed(check, added_checks):
            continue
        findings.append(
            TestIntegrityFinding(
                file_path=path,
                line=line,
                category=CATEGORY_TEST_WEAKENING,
                detail=_weakening_detail(check, production_summary),
            )
        )
    return findings


def audit_test_integrity_diff(diff_text: str) -> list[TestIntegrityFinding]:
    """Audit a unified diff and return every test-weakening finding.

    Args:
        diff_text: The unified diff of the change under review.

    Returns:
        The findings, ordered by path, then line, then category. An empty
        list means the change does not weaken a test.
    """
    files = _parse_unified_diff(diff_text)
    production_paths = sorted({file.path for file in files if _is_production_path(file.path)})
    if not production_paths:
        return []
    summary = _summarize_paths(production_paths)
    findings: list[TestIntegrityFinding] = []
    for file in files:
        if not _is_test_path(file.path):
            continue
        for hunk in file.hunks:
            findings.extend(_hunk_findings(file.path, hunk, summary))
    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.category))
    return findings


def _sys_module_names(tree: ast.AST) -> frozenset[str]:
    """Return the local names bound to the ``sys`` module."""
    names = {"sys"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sys":
                    names.add(alias.asname or "sys")
    return frozenset(names)


def _is_sys_modules(node: ast.AST, sys_names: frozenset[str]) -> bool:
    """Return True when *node* is a ``sys.modules`` attribute access."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "modules"
        and isinstance(node.value, ast.Name)
        and node.value.id in sys_names
    )


def _is_pytest_constant(node: ast.AST) -> bool:
    """Return True when *node* is the string literal ``"pytest"``."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value == _PYTEST_MODULE_NAME
    )


def _is_pytest_env_constant(node: ast.AST) -> bool:
    """Return True when *node* is the pytest current-test environment name."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value == _PYTEST_ENV_TOKEN
    )


def _is_pytest_env_name(node: ast.AST) -> bool:
    """Return True when *node* names the pytest current-test environment variable."""
    if isinstance(node, ast.Name):
        return node.id == _PYTEST_ENV_TOKEN
    if isinstance(node, ast.Attribute):
        return node.attr == _PYTEST_ENV_TOKEN
    return False


def _module_probe_detector(node: ast.AST, sys_names: frozenset[str]) -> str | None:
    """Return the module-probe detector that fires on *node*, or None."""
    if isinstance(node, ast.Subscript):
        probes = _is_sys_modules(node.value, sys_names) and _is_pytest_constant(node.slice)
    elif isinstance(node, ast.Call):
        probes = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_sys_modules(node.func.value, sys_names)
            and bool(node.args)
            and _is_pytest_constant(node.args[0])
        )
    elif isinstance(node, ast.Compare):
        probes = (
            _is_pytest_constant(node.left)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.In)
            and len(node.comparators) == 1
            and _is_sys_modules(node.comparators[0], sys_names)
        )
    else:
        probes = False
    if probes:
        return _DETECTOR_MODULE
    return None


def _pytest_detector(node: ast.AST, sys_names: frozenset[str]) -> str | None:
    """Return the detector that fires on *node*, or None when none fires."""
    if _is_pytest_env_constant(node) or _is_pytest_env_name(node):
        return _DETECTOR_ENV
    module_detector = _module_probe_detector(node, sys_names)
    if module_detector is not None:
        return module_detector
    return None


def _detection_detail(detector: str) -> str:
    """Return the actionable detail of one pytest-detection finding."""
    return (
        f"production code reads the test runner through the {detector}: shipped code must "
        "not branch on pytest, or the test stops proving the shipped path. "
        "fix: remove the detection and pass the behaviour in explicitly (a parameter, an "
        "injected flag, or a dependency) instead of sniffing the test environment"
    )


def _detect_pytest_usage(source: str, file_path: str) -> list[TestIntegrityFinding]:
    """Return the pytest-detection findings of one production source file."""
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return []
    sys_names = _sys_module_names(tree)
    findings: list[TestIntegrityFinding] = []
    for node in ast.walk(tree):
        detector = _pytest_detector(node, sys_names)
        if detector is None:
            continue
        lineno: int = getattr(node, "lineno", 0)
        findings.append(
            TestIntegrityFinding(
                file_path=file_path,
                line=lineno,
                category=CATEGORY_PYTEST_DETECTION,
                detail=_detection_detail(detector),
            )
        )
    return findings


def _production_package_root(root: Path) -> Path:
    """Return the production package directory under *root*."""
    if root.name == PRODUCTION_PACKAGE_DIR:
        return root
    candidate = root / PRODUCTION_PACKAGE_DIR
    if candidate.is_dir():
        return candidate
    msg = f"production package not found under root: {root}"
    raise FileNotFoundError(msg)


def _relative_path(path: Path, root: Path) -> str:
    """Return *path* relative to *root* when possible, else its absolute form."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def audit_pytest_detection(root: Path) -> list[TestIntegrityFinding]:
    """Audit the production package under *root* for pytest detection.

    Args:
        root: The repository root. The production package is ``root/ralph``
            (or *root* itself when it already IS the package).

    Returns:
        The findings, ordered by path, then line, then category.
    """
    package_root = _production_package_root(root)
    findings: list[TestIntegrityFinding] = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "pytest" not in source.lower():
            continue
        findings.extend(_detect_pytest_usage(source, _relative_path(path, root)))
    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.category))
    return findings


def _default_root() -> Path:
    """Return the repository root the gate scans when no root is given."""
    return Path(__file__).parent.parent.parent


def _read_diff(diff_path: str) -> str:
    """Return the unified diff carried by *diff_path* (``-`` reads stdin)."""
    if diff_path == "-":
        text: str = sys.stdin.read()
        return text
    # filesystem-read-ok: CLI entry point reads the user-supplied diff file once
    return Path(diff_path).read_text(encoding="utf-8")


def _parse_arguments(args: Sequence[str]) -> tuple[str | None, str | None] | None:
    """Return ``(diff_path, root)`` parsed from *args*, or None on a usage error."""
    diff_path: str | None = None
    root_arg: str | None = None
    index = 0
    while index < len(args):
        argument = args[index]
        if argument not in {"--diff", "--root"}:
            print(f"Error: unknown argument: {argument}", file=sys.stderr)
            return None
        index += 1
        if index >= len(args):
            print(f"Error: {argument} requires a value", file=sys.stderr)
            return None
        if argument == "--diff":
            diff_path = args[index]
        else:
            root_arg = args[index]
        index += 1
    return diff_path, root_arg


def _collect_findings(root: Path, diff_text: str | None) -> list[TestIntegrityFinding]:
    """Return every finding of the diff half and the detection half."""
    findings: list[TestIntegrityFinding] = []
    if diff_text is not None:
        findings.extend(audit_test_integrity_diff(diff_text))
    findings.extend(audit_pytest_detection(root))
    return findings


def _resolve_root(root_arg: str | None) -> Path | None:
    """Return the audit root, or None when it holds no production package."""
    root = Path(root_arg) if root_arg else _default_root()
    if not root.is_dir():
        print(f"Error: audit root not found: {root}", file=sys.stderr)
        return None
    try:
        _production_package_root(root)
    except FileNotFoundError:
        print(f"Error: production package not found under root: {root}", file=sys.stderr)
        return None
    return root


def main(argv: list[str] | None = None) -> int:
    """Run the test-integrity gate and return an exit code.

    Exit code 0: no findings. Exit code 1: findings. Exit code 2: usage
    error, a missing root, or an unreadable diff.
    """
    args = list(argv) if argv is not None else list(sys.argv[1:])
    if any(argument in {"-h", "--help"} for argument in args):
        print(_USAGE)
        return 0
    parsed = _parse_arguments(args)
    if parsed is None:
        print(_USAGE, file=sys.stderr)
        return 2
    diff_path, root_arg = parsed

    root = _resolve_root(root_arg)
    if root is None:
        return 2

    diff_text = ""
    if diff_path is not None:
        try:
            diff_text = _read_diff(diff_path)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error: could not read diff {diff_path}: {exc}", file=sys.stderr)
            return 2

    else:
        diff_text = worktree_diff(root)

    diff_label = diff_path if diff_path is not None else "(working-tree diff vs HEAD)"
    print(f"Auditing test integrity: root={root} diff={diff_label}")
    print()

    findings = _collect_findings(root, diff_text)

    if findings:
        print(f"TEST-INTEGRITY FINDINGS: {len(findings)} finding(s)")
        print("=" * 72)
        for finding in findings:
            print(f"  {finding}")
        print()
        print(
            "A test may not be weakened to let a change pass, and production code may not "
            "detect the test runner. Restore the check (or record why it changed with "
            f"'# {TEST_REASON_MARKER} <reason>' next to the change) and remove the "
            "pytest detection from production code."
        )
        return 1

    print("No test-integrity findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
