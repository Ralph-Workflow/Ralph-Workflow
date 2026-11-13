from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

TEST_REASON_CONSTANT = "RALPH_TEST_CHANGE_REASONS"

DELETED_TEST_MODULES: dict[str, str] = {
    "ralph-workflow/tests/project_policy/test_policy_skill_technique_ownership.py": (
        "the test encoded an editorial prose judgment as a machine constant; the "
        "structural policy audits and human review remain the safeguards"
    ),
    "ralph-workflow/tests/test_audit_public_docstrings.py": (
        "the audit_public_docstrings module and its verify step were deleted; "
        "the absolute comment gate covers the docstring floor"
    ),
    "ralph-workflow/tests/test_skills_auto_commit.py": (
        "the dead skills auto-commit helper and the tests that pinned only it were "
        "deleted; the live skill auto-commit path stays pinned by "
        "tests/test_audit_skill_auto_commit.py"
    ),
    "ralph-workflow/tests/test_audit_skill_mirror_freshness.py": (
        "the retired mirror-freshness audit was deleted with the mirrored bundle; the "
        "retired package stays absent under "
        "tests/test_retired_skills_package_absent.py"
    ),
    "ralph-workflow/tests/test_skills_package_skill_names_parity.py": (
        "the hand-kept skill-name list and its parity test were deleted with the "
        "mirrored bundle; the shipped set stays pinned by "
        "tests/test_skills_content.py"
    ),
    "ralph-workflow/tests/test_skills_package_sync_script.py": (
        "the skills sync script was deleted with the mirrored bundle; the retired "
        "package and script stay absent under tests/test_retired_skills_package_absent.py"
    ),
    "ralph-workflow/tests/recovery/test_retry_prompt_premise_check.py": (
        "the runtime duplicate of the debugging technique was retired, so the tests "
        "that pinned only the premise-check retry line and the fingerprint artifact "
        "that fed it were deleted; the retry prompt contract stays pinned by "
        "tests/recovery/test_retry_prompt_contract.py and "
        "tests/test_validation_retry_prompt_persistence.py"
    ),
    "ralph-workflow/tests/test_skills_package_version_parity.py": (
        "the separate skills package and its version parity were deleted with the "
        "mirrored bundle; the retired package stays absent under "
        "tests/test_retired_skills_package_absent.py"
    ),
}

CATEGORY_TEST_WEAKENING = "test-weakening"

TEST_TREE_DIR = "tests"

PRODUCTION_PACKAGE_DIR = "ralph"

_KIND_ASSERTION = "assertion"
_KIND_FIXTURE = "pytest fixture"
_KIND_RAISES = "raises/warns expectation"
_KIND_EXPECTED_VALUE = "expected-value constant"

_ASSERT_RE = re.compile(r"^assert\b")

_FIXTURE_RE = re.compile(r"^@\s*(?:pytest\s*\.\s*)?fixture\b")

_RAISES_RE = re.compile(r"\bpytest\s*\.\s*(?:raises|warns)\s*\(")

_COMPARISON_RE = re.compile(
    r"^(?P<lhs>.*?)\s*(?P<op>==|!=|<=|>=|<|>|\bis\s+not\b|\bis\b|\bnot\s+in\b|\bin\b)\s*(?P<rhs>.*)$"
)

_CONSTRAINT_RE = re.compile(r"==|!=|<=|>=|<|>|\bis\s+not\b|\bis\b|\bnot\s+in\b|\bin\b")

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

_CONST_ASSIGN_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=")

_RAISES_CALL_RE = re.compile(r"pytest\s*\.\s*(?:raises|warns)\s*\((?P<args>[^)]*)\)")

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_MAX_COUPLED_PATHS_IN_DETAIL = 3

_MAX_QUOTED_LINE = 120


@dataclass(frozen=True)
class TestIntegrityFinding:
    __test__ = False

    file_path: str
    line: int
    category: str
    detail: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


@dataclass(frozen=True)
class _Check:
    kind: str
    text: str
    lhs: str
    operator: str | None
    expected: str | None
    constraints: int


@dataclass(frozen=True)
class _Hunk:
    removed: tuple[tuple[int, str], ...]
    added: tuple[tuple[int, str], ...]
    context: tuple[tuple[int, str], ...]
    old_lines: tuple[tuple[int, str, bool], ...] = ()
    new_lines: tuple[tuple[int, str, bool], ...] = ()
    function_header: str = ""

    def all_lines(self) -> tuple[tuple[int, str], ...]:
        return (*self.removed, *self.added, *self.context)


@dataclass(frozen=True)
class _DiffFile:
    path: str
    hunks: tuple[_Hunk, ...]


def _group_str(match: re.Match[str], group: int | str) -> str:
    value = match.group(group)
    if not isinstance(value, str):
        msg = "expected regex capture group to be str"
        raise TypeError(msg)
    return value


def _normalize_diff_path(raw: str) -> str:
    path = raw.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return ""
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


class _HunkBuilder:
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
    parts = path.split("/")
    if TEST_TREE_DIR in parts[:-1]:
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def _is_production_path(path: str) -> bool:
    return not _is_test_path(path)


def _count_constraints(expression: str) -> int:
    return sum(1 for _ in _CONSTRAINT_RE.finditer(expression))


def _assertion_test_node(text: str) -> ast.expr | None:
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
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return _compare_ordered_numeric(op, left, right)
    if isinstance(left, str) and isinstance(right, str):
        return _compare_ordered_str(op, left, right)
    return False


def _evaluate_constant_compare(op: ast.cmpop, left: object, right: object) -> bool:
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


def _strip_trailing_comment(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("assert"):
        return text
    try:
        mod = ast.parse(stripped)
    except SyntaxError:
        return text
    if not mod.body or not isinstance(mod.body[0], ast.Assert):
        return text
    segment = ast.get_source_segment(stripped, mod.body[0])
    if segment is None:
        return text
    return segment


def _assertion_check(text: str) -> _Check:
    text = _strip_trailing_comment(text)
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
    match = _CONST_ASSIGN_RE.match(text)
    if match is None:
        return None
    return text[match.end() :].strip()


def _is_expected_constant(text: str) -> bool:
    match = _CONST_ASSIGN_RE.match(text)
    if match is None:
        return False
    name = _group_str(match, "name")
    return name.isupper() and "EXPECT" in name


def _classify_check(text: str) -> _Check | None:
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
    def __init__(self, initial_fixture: str, known_fixtures: set[str] | None) -> None:
        self.pending_fixture = False
        self.current_fixture_name: str | None = initial_fixture if initial_fixture else None
        self.current_fixture_indent = 0
        self.in_def_signature = False
        self.known_fixtures = known_fixtures

    def handle_line(self, stripped: str, indent: int) -> bool:
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


_REASON_ENTRY_RE = re.compile(
    r"""["']([^"'\n]+)["']\s*:\s*\(?\s*["']([^"'\n]+)["']""",
    re.DOTALL,
)


def _file_change_reasons(diff_file: _DiffFile) -> dict[str, str]:
    block_lines: list[str] = []
    inside = False
    for hunk in diff_file.hunks:
        for _line, text in hunk.added:
            if TEST_REASON_CONSTANT in text:
                inside = True
            if not inside:
                continue
            block_lines.append(text)
            if text.strip() == "}":
                inside = False
    reasons: dict[str, str] = {}
    for match in _REASON_ENTRY_RE.finditer("\n".join(block_lines)):
        name = _group_str(match, 1).strip()
        reason = _group_str(match, 2).strip()
        if name and reason:
            reasons[name] = reason
    return reasons


def _hunk_has_reason(reasons: dict[str, str]) -> bool:
    return bool(reasons)


def _summarize_paths(paths: Sequence[str]) -> str:
    shown = list(paths[:_MAX_COUPLED_PATHS_IN_DETAIL])
    summary = ", ".join(shown)
    if len(paths) > len(shown):
        summary = f"{summary} (+{len(paths) - len(shown)} more)"
    return summary


def _quote(text: str) -> str:
    single_line = " ".join(text.split())
    if len(single_line) > _MAX_QUOTED_LINE:
        single_line = f"{single_line[:_MAX_QUOTED_LINE]}..."
    return f"'{single_line}'"


def _weakening_detail(check: _Check, production_summary: str) -> str:
    return (
        f"{check.kind} removed or weakened in the same change as production code "
        f"({production_summary}); removed line: {_quote(check.text)}. "
        f"fix: restore the check, or record the reason as a "
        f"'{TEST_REASON_CONSTANT}' entry naming the changed check"
    )


def _hunk_findings(
    path: str,
    hunk: _Hunk,
    production_summary: str,
    known_fixtures: set[str] | None = None,
    reasons: dict[str, str] | None = None,
) -> list[TestIntegrityFinding]:
    if _hunk_has_reason(reasons or {}):
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
    files = _parse_unified_diff(diff_text)
    production_paths = sorted({file.path for file in files if _is_production_path(file.path)})
    if not production_paths:
        return []
    summary = _summarize_paths(production_paths)
    findings: list[TestIntegrityFinding] = []
    for file in files:
        if not _is_test_path(file.path):
            continue
        if file.path in DELETED_TEST_MODULES:
            continue
        known_fixtures: set[str] = set()
        reasons = _file_change_reasons(file)
        for hunk in file.hunks:
            findings.extend(_hunk_findings(file.path, hunk, summary, known_fixtures, reasons))
    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.category))
    return findings
