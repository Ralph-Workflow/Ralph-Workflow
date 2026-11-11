"""Diff analysis and test-weakening detection for the test-integrity gate."""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The recorded-reason marker. A removed or changed test check is accepted
#: only when the diff hunk carries this marker in the form
#: ``# RALPH-TEST-REASON: <why the check changed>``.
TEST_REASON_MARKER = "RALPH-TEST-REASON:"

#: Finding categories. Stable strings: the verify step and any waiver record
#: key on them.
CATEGORY_TEST_WEAKENING = "test-weakening"

#: Directory name that marks a test tree in a repository-relative path.
TEST_TREE_DIR = "tests"

#: Directory name of the shipped package (the production code).
PRODUCTION_PACKAGE_DIR = "ralph"

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

#: Maximum number of coupled production paths quoted in a finding detail.
_MAX_COUPLED_PATHS_IN_DETAIL = 3

#: Maximum length of the quoted removed line in a finding detail.
_MAX_QUOTED_LINE = 120


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
    old_lines: tuple[tuple[int, str, bool], ...] = ()
    new_lines: tuple[tuple[int, str, bool], ...] = ()
    function_header: str = ""

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


class _HunkBuilder:
    """Accumulates lines for a single hunk."""

    def __init__(self, old_line: int, new_line: int, function_header: str) -> None:
        self.old_line = old_line
        self.new_line = new_line
        self.function_header = function_header
        self.removed: list[tuple[int, str]] = []
        self.added: list[tuple[int, str]] = []
        self.context: list[tuple[int, str]] = []
        self.old_lines: list[tuple[int, str, bool]] = []
        self.new_lines: list[tuple[int, str, bool]] = []

    def add_plus(self, text: str) -> None:
        self.added.append((self.new_line, text))
        self.new_lines.append((self.new_line, text, True))
        self.new_line += 1

    def add_minus(self, text: str) -> None:
        self.removed.append((self.old_line, text))
        self.old_lines.append((self.old_line, text, True))
        self.old_line += 1

    def add_context(self, text: str) -> None:
        self.context.append((self.new_line, text))
        self.old_lines.append((self.old_line, text, False))
        self.new_lines.append((self.new_line, text, False))
        self.old_line += 1
        self.new_line += 1

    def build(self) -> _Hunk:
        return _Hunk(
            removed=tuple(self.removed),
            added=tuple(self.added),
            context=tuple(self.context),
            old_lines=tuple(self.old_lines),
            new_lines=tuple(self.new_lines),
            function_header=self.function_header,
        )


def _parse_unified_diff(diff_text: str) -> list[_DiffFile]:
    """Return the files and hunks carried by a unified diff."""
    files: list[_DiffFile] = []
    old_path = ""
    new_path = ""
    hunks: list[_Hunk] = []
    builder: _HunkBuilder | None = None

    def close_hunk() -> None:
        nonlocal builder
        if builder is not None:
            hunks.append(builder.build())
            builder = None

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
            header_suffix = raw[header.end() :].strip()
            def_match = re.match(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\b", header_suffix)
            builder = _HunkBuilder(
                old_line=int(header.group(1)),
                new_line=int(header.group(3)),
                function_header=def_match.group(1) if def_match else "",
            )
            continue
        if builder is None:
            continue
        if raw.startswith("+"):
            builder.add_plus(raw[1:])
        elif raw.startswith("-"):
            builder.add_minus(raw[1:])
        elif raw.startswith("\\"):
            continue
        else:
            builder.add_context(raw[1:] if raw.startswith(" ") else raw)
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


def _parse_fixture_return_or_yield(stmt_str: str) -> tuple[str | None, str | None]:
    """Parse a return or yield statement and return (kind, expected_unparsed)."""
    dedented = textwrap.dedent(stmt_str)
    wrapper = f"def _dummy():\n{textwrap.indent(dedented, '    ')}"
    try:
        mod = ast.parse(wrapper)
    except SyntaxError:
        return None, None
    if not (mod.body and isinstance(mod.body[0], ast.FunctionDef) and mod.body[0].body):
        return None, None
    node = mod.body[0].body[0]
    if isinstance(node, ast.Return):
        val = ast.unparse(node.value).strip() if node.value is not None else "None"
        return "return", val
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Yield):
        val = ast.unparse(node.value.value).strip() if node.value.value is not None else "None"
        return "yield", val
    return None, None


def _extract_assertion(
    lines: Sequence[tuple[int, str, bool]], i: int
) -> tuple[int, _Check | None, int | None]:
    """Extract an assertion check starting at index *i* (handling multiline assertions)."""
    n = len(lines)
    candidate_lines: list[str] = []
    j = i
    has_changed_stmt = False
    first_changed_line: int | None = None
    parsed_check: _Check | None = None

    while j < n:
        l_no, l_text, l_changed = lines[j]
        candidate_lines.append(l_text)
        if l_changed:
            has_changed_stmt = True
            if first_changed_line is None:
                first_changed_line = l_no

        full_stmt = "\n".join(candidate_lines)
        dedented = textwrap.dedent(full_stmt)
        try:
            mod = ast.parse(dedented)
            if mod.body and isinstance(mod.body[0], ast.Assert):
                parsed_check = _assertion_check(full_stmt.strip())
                break
        except SyntaxError:
            pass
        j += 1

    if parsed_check is not None:
        if has_changed_stmt and first_changed_line is not None:
            return j + 1, parsed_check, first_changed_line
        return j + 1, None, None

    line_num, raw_text, is_changed = lines[i]
    if is_changed:
        return i + 1, _assertion_check(raw_text.strip()), line_num
    return i + 1, None, None


def _extract_fixture_return_or_yield(
    lines: Sequence[tuple[int, str, bool]], i: int, fixture_name: str
) -> tuple[int, _Check | None, int | None]:
    """Extract a fixture return/yield check starting at index *i*."""
    n = len(lines)
    candidate_lines: list[str] = []
    j = i
    has_changed_stmt = False
    first_changed_line: int | None = None
    parsed_expected: str | None = None
    full_stmt_text = ""

    while j < n:
        l_no, l_text, l_changed = lines[j]
        candidate_lines.append(l_text)
        if l_changed:
            has_changed_stmt = True
            if first_changed_line is None:
                first_changed_line = l_no

        full_stmt = "\n".join(candidate_lines)
        kind, exp = _parse_fixture_return_or_yield(full_stmt)
        if kind is not None:
            parsed_expected = exp
            full_stmt_text = full_stmt.strip()
            break
        j += 1

    if parsed_expected is not None:
        if has_changed_stmt and first_changed_line is not None:
            check = _Check(
                kind=_KIND_FIXTURE,
                text=full_stmt_text,
                lhs=fixture_name,
                operator=None,
                expected=parsed_expected,
                constraints=0,
            )
            return j + 1, check, first_changed_line
        return j + 1, None, None

    line_num, raw_text, is_changed = lines[i]
    if is_changed:
        check = _Check(
            kind=_KIND_FIXTURE,
            text=raw_text.strip(),
            lhs=fixture_name,
            operator=None,
            expected=None,
            constraints=0,
        )
        return i + 1, check, line_num
    return i + 1, None, None


def _extract_raises(
    lines: Sequence[tuple[int, str, bool]], i: int
) -> tuple[int, _Check | None, int | None]:
    """Extract a raises expectation check starting at index *i*."""
    n = len(lines)
    candidate_lines: list[str] = []
    j = i
    has_changed_stmt = False
    first_changed_line: int | None = None
    found_raises = False
    full_stmt_text = ""

    while j < n:
        l_no, l_text, l_changed = lines[j]
        candidate_lines.append(l_text)
        if l_changed:
            has_changed_stmt = True
            if first_changed_line is None:
                first_changed_line = l_no
        full_stmt = "\n".join(candidate_lines)
        if _RAISES_CALL_RE.search(full_stmt):
            found_raises = True
            full_stmt_text = full_stmt.strip()
            break
        j += 1

    if found_raises:
        if has_changed_stmt and first_changed_line is not None:
            return j + 1, _raises_check(full_stmt_text), first_changed_line
        return j + 1, None, None

    line_num, raw_text, is_changed = lines[i]
    if is_changed:
        return i + 1, _raises_check(raw_text.strip()), line_num
    return i + 1, None, None


class _FixtureScopeTracker:
    """Tracks whether lines are inside a pytest fixture function."""

    def __init__(self, initial_fixture: str, known_fixtures: set[str] | None) -> None:
        self.pending_fixture = False
        self.current_fixture_name: str | None = initial_fixture if initial_fixture else None
        self.current_fixture_indent = 0
        self.in_def_signature = False
        self.known_fixtures = known_fixtures

    def handle_line(self, stripped: str, indent: int) -> bool:
        """Update tracker state for a line. Return True if line was consumed."""
        if _FIXTURE_RE.match(stripped):
            self.pending_fixture = True
            return False

        def_match = re.match(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
        if def_match:
            fn_name = def_match.group(1)
            self.in_def_signature = not stripped.rstrip().endswith(":")
            if self.pending_fixture or (
                self.known_fixtures is not None and fn_name in self.known_fixtures
            ):
                self.current_fixture_name = fn_name
                self.current_fixture_indent = indent
                self.pending_fixture = False
                if self.known_fixtures is not None:
                    self.known_fixtures.add(fn_name)
            elif indent <= self.current_fixture_indent:
                self.current_fixture_name = None
            return True

        if self.in_def_signature:
            if stripped.rstrip().endswith(":"):
                self.in_def_signature = False
            return True

        if self.current_fixture_name is not None and indent <= self.current_fixture_indent:
            self.current_fixture_name = None
        return False


def _extract_hunk_checks(
    lines: Sequence[tuple[int, str, bool]],
    known_fixtures: set[str] | None = None,
    initial_fixture: str = "",
) -> list[tuple[int, _Check]]:
    """Extract semantic checks from a sequence of hunk lines."""
    checks: list[tuple[int, _Check]] = []
    tracker = _FixtureScopeTracker(initial_fixture, known_fixtures)
    n = len(lines)
    i = 0

    while i < n:
        line_num, raw_text, is_changed = lines[i]
        stripped = raw_text.strip()
        indent = len(raw_text) - len(raw_text.lstrip())

        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if tracker.handle_line(stripped, indent):
            i += 1
            continue

        if _FIXTURE_RE.match(stripped):
            if is_changed:
                checks.append(
                    (
                        line_num,
                        _Check(
                            kind=_KIND_FIXTURE,
                            text=stripped,
                            lhs="",
                            operator=None,
                            expected=None,
                            constraints=0,
                        ),
                    )
                )
            i += 1
            continue

        if _ASSERT_RE.match(stripped):
            next_i, check, ch_line = _extract_assertion(lines, i)
            if check is not None and ch_line is not None:
                checks.append((ch_line, check))
            i = next_i
            continue

        if tracker.current_fixture_name is not None and re.match(r"^(?:return|yield)\b", stripped):
            next_i, check, ch_line = _extract_fixture_return_or_yield(
                lines, i, tracker.current_fixture_name
            )
            if check is not None and ch_line is not None:
                checks.append((ch_line, check))
            i = next_i
            continue

        if _RAISES_RE.search(stripped):
            next_i, check, ch_line = _extract_raises(lines, i)
            if check is not None and ch_line is not None:
                checks.append((ch_line, check))
            i = next_i
            continue

        if _is_expected_constant(stripped):
            if is_changed:
                checks.append(
                    (
                        line_num,
                        _Check(
                            kind=_KIND_EXPECTED_VALUE,
                            text=stripped,
                            lhs="",
                            operator=None,
                            expected=_constant_value(stripped),
                            constraints=0,
                        ),
                    )
                )
            i += 1
            continue

        i += 1

    return checks


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
            and (removed.expected is None or not removed.lhs)
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


def _hunk_findings(
    path: str,
    hunk: _Hunk,
    production_summary: str,
    known_fixtures: set[str] | None = None,
) -> list[TestIntegrityFinding]:
    """Return the weakening findings of one hunk of a test file."""
    if _hunk_has_reason(hunk):
        return []
    initial_fn = (
        hunk.function_header if (known_fixtures and hunk.function_header in known_fixtures) else ""
    )
    if hunk.old_lines:
        removed_checks = _extract_hunk_checks(
            hunk.old_lines, known_fixtures=known_fixtures, initial_fixture=initial_fn
        )
    else:
        removed_checks = []
        for line, text in hunk.removed:
            check = _classify_check(text)
            if check is not None:
                removed_checks.append((line, check))
    if not removed_checks:
        return []
    if hunk.new_lines:
        added_checks_with_lines = _extract_hunk_checks(
            hunk.new_lines, known_fixtures=known_fixtures, initial_fixture=initial_fn
        )
        added_checks = [check for _, check in added_checks_with_lines]
    else:
        added_checks = []
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
        known_fixtures: set[str] = set()
        for hunk in file.hunks:
            findings.extend(_hunk_findings(file.path, hunk, summary, known_fixtures))
    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.category))
    return findings
