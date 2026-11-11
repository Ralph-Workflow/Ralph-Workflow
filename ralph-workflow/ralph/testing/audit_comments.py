"""Comment-policy audit — a deterministic gate over the repository's comments.

Enforces the comment rules of the clean-code policy on the lines a change
touched. The gate is a pure function over two content sets, so it never
shells out, never reads version control, and never touches the network.

RULES

Every rule has a named identifier and a severity. A BLOCKING rule is a
mechanically decidable violation of the policy: the gate fails on it. An
ADVISORY rule is reported for review but does not fail the gate.

``change-narration`` (blocking)
    A comment that narrates a change ("added for", "fixed", "now",
    "previously", "changed to", "new:") instead of describing the code.
``commented-out-code`` (blocking)
    A comment line that parses as Python (assignment, call, import,
    ``return``, ``if``/``for``/``while``/``def``/``class``) or matches a
    code-shaped pattern. A suppression directive that the typechecking or
    linting policy mandates — a coded type-ignore directive or a
    ``# noqa[:<code>]`` directive, each together with its ``# reason: <text>``
    suffix — is a documented suppression, not commented-out code, so this rule
    never claims it.
``section-banner`` (blocking)
    A comment-only line made of a repeated banner character, or an
    ``==== Section ====`` style line.
``reviewer-note`` (blocking)
    A comment that addresses the reviewer ("note:", "todo", "fixme",
    "hack", "xxx", "reviewer:", "don't forget") instead of the next
    maintainer of the code.
``ownerless-marker`` (blocking)
    A ``TODO`` / ``FIXME`` / ``HACK`` that carries neither an owner nor a
    removal trigger.
``empty-bypass-reason`` (blocking)
    A ``# comment-ok:`` marker with no reason. An empty reason is not a
    bypass, so the marker is reported as its own finding.
``restates-code`` (advisory)
    A comment that repeats the code it documents, ignoring whitespace and
    case. This rule encodes the "agents over-comment" claim, which is the
    WEAKEST evidence in this set: comment/code inconsistency is associated
    with defects, but the frequency of over-commenting is not yet backed by
    audit counts. The rule is therefore reported for review and does not
    fail the gate.

BASELINE-DIFF MODE

The gate never scans a whole tree and hopes for the best. It compares a
BASELINE content set against the CURRENT content set and reports only
findings on NEW or CHANGED lines. A finding that is already in the
baseline is pre-existing debt, not a regression, and it stays visible in
the baseline until somebody fixes it — but it does not turn every commit
red. The baseline is supplied by the caller (the coordinator wires a
git-diff based baseline); the gate itself never shells out and never reads
version control. EVERY rule honours that filter: an unchanged line can
never fail the gate.

BURN-DOWN COUNT

The gate also reports how many findings the committed baseline tree still
carries, so the burn-down the clean-code policy promises is a number
rather than a guess. The count is the debt of the committed baseline tree
itself: it is measured by scanning that tree on its own, not by the diff.

BYPASS

An inline ``# comment-ok: <reason>`` marker waives the comment rules on
its own line and on the line immediately below it, so a marker can sit on
the offending comment or directly above it. The reason is mandatory: an
empty reason is NOT a bypass and is reported as its own finding. The
marker constant is :data:`COMMENT_BYPASS_MARKER`.

SCOPE

The gate reads Python source (``.py`` / ``.pyi``) only. A comment rule
about "the following code line" is a statement about source code, and
reading every text file in the tree would be work the gate does not need.

INPUT SEAM

    audit_comments(baseline, current) -> list[CommentFinding]
    audit_comment_roots(baseline_root, current_root) -> tuple[list[CommentFinding], int]

``baseline`` and ``current`` are mappings of repository-relative path to
file text. The gate is a pure function over them: no network, no
subprocess, no clock. The roots seam adds the burn-down count: the number
of findings the committed baseline tree still carries.

Usage:
    python -m ralph.testing.audit_comments [baseline_root] [current_root]

With no arguments the gate scans default root vs committed HEAD baseline.
With one argument ([current_root]) it scans against an empty baseline.
With two arguments it reports only NEW or CHANGED lines vs baseline_root.

Exit 0 = no blocking finding, 1 = blocking findings, 2 = a root was not
found or the arguments were wrong.
"""

from __future__ import annotations

import ast
import re
import sys
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing._audit_baseline import head_tree

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "COMMENT_BYPASS_MARKER",
    "RULE_CHANGE_NARRATION",
    "RULE_COMMENTED_OUT_CODE",
    "RULE_EMPTY_BYPASS_REASON",
    "RULE_OWNERLESS_MARKER",
    "RULE_RESTATES_CODE",
    "RULE_REVIEWER_NOTE",
    "RULE_SECTION_BANNER",
    "RULE_SEVERITIES",
    "SCANNED_SUFFIXES",
    "SEVERITY_ADVISORY",
    "SEVERITY_BLOCKING",
    "CommentFinding",
    "audit_comment_roots",
    "audit_comments",
    "main",
    "scan_comment_lines",
]

# Rule identifiers and severities

#: Inline marker that waives the comment rules on its line and on the line
#: immediately below it, in the style of the repository's existing
#: ``# mcp-timeout-ok:`` and ``# secret-scan-ok:`` bypasses.
COMMENT_BYPASS_MARKER = "comment-ok"

#: The comment narrates a change instead of describing the code.
RULE_CHANGE_NARRATION = "change-narration"

#: The comment repeats the code it documents.
RULE_RESTATES_CODE = "restates-code"

#: The comment holds commented-out code.
RULE_COMMENTED_OUT_CODE = "commented-out-code"

#: The comment is a section banner.
RULE_SECTION_BANNER = "section-banner"

#: The comment addresses the reviewer instead of the next maintainer.
RULE_REVIEWER_NOTE = "reviewer-note"

#: The comment is a deferred-work marker without an owner or a removal
#: trigger.
RULE_OWNERLESS_MARKER = "ownerless-marker"

#: The comment carries a bypass marker without a reason.
RULE_EMPTY_BYPASS_REASON = "empty-bypass-reason"

#: A finding that fails the gate.
SEVERITY_BLOCKING = "blocking"

#: A finding that is reported for review but does not fail the gate.
SEVERITY_ADVISORY = "advisory"

#: The severity of every rule. The comment/code inconsistency evidence is
#: strong enough to build a blocking gate on; the "agents over-comment"
#: claim is not, so ``restates-code`` stays advisory.
RULE_SEVERITIES: dict[str, str] = {
    RULE_CHANGE_NARRATION: SEVERITY_BLOCKING,
    RULE_RESTATES_CODE: SEVERITY_ADVISORY,
    RULE_COMMENTED_OUT_CODE: SEVERITY_BLOCKING,
    RULE_SECTION_BANNER: SEVERITY_BLOCKING,
    RULE_REVIEWER_NOTE: SEVERITY_BLOCKING,
    RULE_OWNERLESS_MARKER: SEVERITY_BLOCKING,
    RULE_EMPTY_BYPASS_REASON: SEVERITY_BLOCKING,
}

#: File suffixes the gate scans.
SCANNED_SUFFIXES: frozenset[str] = frozenset({".py", ".pyi"})

#: Directories never scanned: caches, virtualenvs and build output.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
        "venv",
    }
)

#: Accepted root-argument counts for the CLI: current-only, or baseline
#: plus current.
MAX_CLI_ROOT_ARGUMENTS = 2

# Rule patterns

#: Phrases that narrate a change. The single-word entries are matched on a
#: word boundary, so a word that merely contains one of them is not a hit.
CHANGE_NARRATION_PHRASES: tuple[str, ...] = (
    "added for",
    "changed to",
    "fixed",
    "new:",
    "now",
    "previously",
)

_CHANGE_NARRATION_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(phrase) for phrase in CHANGE_NARRATION_PHRASES) + r")(?!\w)",
    re.IGNORECASE,
)

#: Comment prefixes that address the reviewer rather than the next
#: maintainer of the code. The entries are regular-expression fragments;
#: the bare marker words carry a word boundary so "hacking" is not a
#: marker. ``todo`` / ``fixme`` / ``hack`` appear here because a
#: deferred-work marker IS a note to the reviewer; the more specific
#: ownerless rule takes precedence over this one for those prefixes, so a
#: single line never yields two findings from this family.
REVIEWER_NOTE_PREFIXES: tuple[str, ...] = (
    r"don't forget",
    "don\u2019t forget",
    r"hack\b",
    r"fixme\b",
    r"note:",
    r"reviewer:",
    r"todo\b",
    r"xxx\b",
)

_REVIEWER_NOTE_RE = re.compile(r"(?i)^(?:" + "|".join(REVIEWER_NOTE_PREFIXES) + r")")

#: Prefixes that mark deferred work and therefore need an owner AND a
#: removal trigger to be acceptable.
DEFERRED_WORK_PREFIXES: tuple[str, ...] = ("hack", "fixme", "todo")

_DEFERRED_WORK_RE = re.compile(
    r"(?i)^(?:" + "|".join(DEFERRED_WORK_PREFIXES) + r")\b\s*:?\s*(?P<body>.*)$",
)

#: An owner is an explicit attribution: an ``@handle`` or an ``owner:``
#: / ``owner=`` prefix.
OWNER_PATTERN = re.compile(r"(?i)(?:\bowner\s*[:=]\s*\S+)|(?:@[\w.-]+)")

#: A removal trigger is an explicit removal condition, an expiry, or a
#: deadline. A bare "later" is not a trigger.
REMOVAL_TRIGGER_PATTERN = re.compile(
    r"(?i)\b(?:remove|delete|drop|retire|revoke)\s+(?:when|after|once|by|until|on)\b"
    r"|\b(?:removal\s+trigger|expires?|expiry|deadline)\b"
    r"|\buntil\s+\S"
)

#: The characters a section banner is built from.
BANNER_CHARACTERS = frozenset("=-*_#~+")

#: How many times a banner character must repeat to make a banner.
MIN_BANNER_REPEAT = 3

#: A line of one repeated character, e.g. "====".
_REPEATED_CHAR_RE = re.compile(r"^(.)\1{2,}$")

#: A ``==== Section ====`` style banner.
_BANNER_SECTION_RE = re.compile(r"^#?[=\-*~+#_]{3,}\s*\S.*?\s*[=\-*~+#_]{3,}$")

#: The statements that make a comment line commented-out code.
COMMENTED_OUT_CODE_STATEMENTS: tuple[type[ast.stmt], ...] = (
    ast.AnnAssign,
    ast.Assert,
    ast.Assign,
    ast.AsyncFor,
    ast.AsyncFunctionDef,
    ast.AsyncWith,
    ast.AugAssign,
    ast.Break,
    ast.ClassDef,
    ast.Continue,
    ast.Delete,
    ast.For,
    ast.FunctionDef,
    ast.Global,
    ast.If,
    ast.Import,
    ast.ImportFrom,
    ast.Nonlocal,
    ast.Raise,
    ast.Return,
    ast.Try,
    ast.While,
    ast.With,
)

#: A code shape that ``ast.parse`` cannot read because the block has no
#: body ("if x:", "for item in items:") or because the line carries a
#: second comment.
CODE_SHAPE_PATTERN = re.compile(
    r"(?i)^(?:return|raise|yield|import|from|global|nonlocal|del)\b"
    r"|^[\w.]+\s*[-+*/%]?=\s*\S"
    r"|^[\w.]+\s*\([^()]*\)\s*$"
    r"|^(?:if|elif|else|for|while|def|class|with|try|except|finally|async)\b.*:\s*$"
)

#: The error codes a suppression directive may carry.
_SUPPRESSION_CODES = r"[A-Za-z0-9_]+(?:\s*,\s*[A-Za-z0-9_]+)*"

#: A suppression directive the typechecking and linting policies mandate:
#: a coded type-ignore directive or a ``noqa`` directive (the token that
#: follows the comment's leading hash sign, optionally carrying
#: ``:<code>``), each together with the ``# reason: <text>`` suffix those
#: policies require. The reason is what makes the directive a documented
#: suppression rather than an unexplained marker, so the suffix is part of
#: the recognised form. The reason text itself is not constrained: the
#: typechecking and linting gates own that rule, and this gate only decides
#: what the comment IS.
SUPPRESSION_DIRECTIVE_RE = re.compile(
    r"(?:\Atype:\s*ignore\[[^\]\s]+\]|\Anoqa(?::\s*" + _SUPPRESSION_CODES + r")?)"
    r"\s*#\s*reason:\s*\S",
    re.IGNORECASE,
)

#: The shortest comment text that can be a restatement of the code.
MIN_RESTATED_LENGTH = 3

#: The inline bypass marker with its (mandatory) reason.
_BYPASS_RE = re.compile(
    r"#\s*" + re.escape(COMMENT_BYPASS_MARKER) + r"\s*:\s*(?P<reason>.*)$",
    re.IGNORECASE,
)

#: The code part of a line, up to its comment. The alternatives consume
#: string literals whole, so a "#" inside a string is never a comment, and
#: the triple-quote alternatives keep a docstring out of the comment
#: stream.
_CODE_PREFIX_RE = re.compile(
    r"(?:[^#'\"\\]|\\.|\"\"\"(?:.*?)\"\"\"|'''(?:.*?)'''"
    r"|\"(?!\")(?:\\.|[^\"\\])*\"|'(?!')(?:\\.|[^'\\])*')*",
    re.DOTALL,
)

#: The quote pairs that open a triple-quoted string.
_TRIPLE_QUOTES: tuple[str, ...] = ('"""', "'''")

#: The quote pairs that open an empty string.
_EMPTY_QUOTES: tuple[str, ...] = ('""', "''")

#: The characters that make a segment worth the full pattern scan: a "#"
#: can open a comment, a quote can open a string, and a backslash can
#: escape the character that follows it. A segment with none of them
#: carries neither a comment nor an opened quote.
_NEEDS_QUOTE_SCAN_RE = re.compile(r"[#'\"\\]")


@dataclass(frozen=True)
class CommentFinding:
    """One comment-policy finding."""

    file_path: str
    line: int
    rule_id: str
    severity: str
    detail: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.rule_id}] {self.detail}"


# Source scanning


def _split_line(segment: str) -> tuple[str, str | None, str | None]:
    """Split one code segment into (code text, comment text, opened quote).

    ``segment`` is the part of a physical line that is code (the text
    before a comment, or the text after a closing triple quote).
    """
    if not _NEEDS_QUOTE_SCAN_RE.search(segment):
        # A segment with no "#", no quote and no backslash carries neither
        # a comment nor an opened quote, and the pattern below consumes it
        # whole. The gate scans the whole tree twice (the current tree and
        # the committed baseline), so this fast path keeps the burn-down
        # count inside the verify step's timeout.
        return segment.strip(), None, None
    while True:
        match = _CODE_PREFIX_RE.match(segment)
        end = len(segment) if match is None else match.end()
        rest = segment[end:]
        if rest.startswith("#"):
            return segment[:end].strip(), rest, None
        if rest.startswith(_TRIPLE_QUOTES):
            return segment[:end].strip(), None, rest[:3]
        if rest.startswith(_EMPTY_QUOTES):
            segment = rest[2:]
            continue
        return segment[:end].strip(), None, None


def _scan_source(source: str) -> list[tuple[int, str, str | None]]:
    """Return ``(row, code text, comment text)`` for every line with content.

    A row is reported when it carries code, a comment, or both. Lines
    inside a triple-quoted string carry neither and are skipped, so a "#"
    inside a docstring is never a comment.
    """
    records: list[tuple[int, str, str | None]] = []
    opened: str | None = None
    for row, raw in enumerate(source.splitlines(), start=1):
        if opened is not None:
            index = raw.find(opened)
            if index < 0:
                continue
            segment = raw[index + len(opened) :]
            opened = None
        else:
            segment = raw
        code_text, comment_text, opened_quote = _split_line(segment)
        opened = opened_quote
        if code_text or comment_text is not None:
            records.append((row, code_text, comment_text))
    return records


def _comment_body(comment_text: str) -> str:
    """Return the comment text without its leading markers."""
    return comment_text.lstrip("#").strip()


def _regex_group_str(match: re.Match[str], group: str) -> str:
    """Return a named regex group as ``str`` (never ``Any``)."""
    value = match.group(group)
    if not isinstance(value, str):
        msg = "expected regex capture group to be str"
        raise TypeError(msg)
    return value


def _normalize_code(text: str) -> str:
    """Return the comparison form of a code line or a comment body."""
    return " ".join(text.split()).lower().strip(" :;=.,")


# The individual rules


def _is_suppression_directive(body: str) -> bool:
    """Return True when the comment body is a mandated suppression directive."""
    return SUPPRESSION_DIRECTIVE_RE.match(body) is not None


def _is_commented_out_code(body: str) -> bool:
    """Return True when the comment body is code, not prose."""
    candidate = body.strip()
    if not candidate:
        return False
    if _is_suppression_directive(candidate):
        return False
    try:
        tree = ast.parse(candidate)
    except SyntaxError:
        return CODE_SHAPE_PATTERN.match(candidate) is not None
    return any(_is_code_statement(node) for node in tree.body)


def _is_code_statement(node: ast.stmt) -> bool:
    """Return True when the statement is code rather than a bare name."""
    if isinstance(node, ast.Expr):
        return isinstance(node.value, ast.Call)
    return isinstance(node, COMMENTED_OUT_CODE_STATEMENTS)


def _is_section_banner(comment_text: str) -> bool:
    """Return True when the comment is a section banner."""
    body = comment_text.strip()
    if body.startswith("#"):
        body = body[1:].strip()
    if _REPEATED_CHAR_RE.match(body) is not None:
        return True
    return _BANNER_SECTION_RE.match(comment_text.strip()) is not None


def _lacks_owner_or_trigger(body: str) -> bool:
    """Return True when a deferred-work marker has no owner or no trigger."""
    match = _DEFERRED_WORK_RE.match(body)
    rest = _regex_group_str(match, "body") if match is not None else body
    if OWNER_PATTERN.search(rest) is not None:
        return REMOVAL_TRIGGER_PATTERN.search(rest) is None
    return True


def _reviewer_rule(body: str) -> str | None:
    """Return the rule for a note-to-the-reviewer comment, or None.

    A deferred-work marker is the more specific rule: it is reported only
    when it lacks an owner AND a removal trigger, and a marker that carries
    both is allowed.
    """
    if _DEFERRED_WORK_RE.match(body) is not None:
        return RULE_OWNERLESS_MARKER if _lacks_owner_or_trigger(body) else None
    if _REVIEWER_NOTE_RE.match(body) is not None:
        return RULE_REVIEWER_NOTE
    return None


def _restates_code(body: str, code_text: str) -> bool:
    """Return True when the comment repeats the code it documents."""
    comment_form = _normalize_code(body)
    if len(comment_form) < MIN_RESTATED_LENGTH:
        return False
    return comment_form == _normalize_code(code_text)


# The bypass marker


def _bypass_state(
    records: list[tuple[int, str, str | None]],
) -> tuple[set[int], list[int]]:
    """Return (waived rows, rows carrying an empty-reason marker).

    A marker with a reason waives its own row and the row immediately
    below it, so the marker can sit on the offending comment or directly
    above it. A marker without a reason waives nothing and is reported.
    """
    waived: set[int] = set()
    empty: list[int] = []
    for row, _code_text, comment_text in records:
        if comment_text is None:
            continue
        match = _BYPASS_RE.search(comment_text)
        if match is None:
            continue
        if not _regex_group_str(match, "reason").strip():
            empty.append(row)
            continue
        waived.add(row)
        waived.add(row + 1)
    return waived, empty


# The gate


def scan_comment_lines(
    file_path: str,
    text: str,
    line_numbers: set[int],
) -> list[CommentFinding]:
    """Scan the given line numbers of *text* for banned comment shapes.

    Every rule honours *line_numbers*: a line outside the set is never
    reported, so an unchanged line can never fail the gate.
    """
    findings: list[CommentFinding] = []
    records = _scan_source(text)
    waived, empty_rows = _bypass_state(records)
    code_rows = [row for row, code_text, _comment in records if code_text]
    code_texts = {row: code_text for row, code_text, _comment in records if code_text}

    findings.extend(
        CommentFinding(
            file_path=file_path,
            line=row,
            rule_id=RULE_EMPTY_BYPASS_REASON,
            severity=SEVERITY_BLOCKING,
            detail=(
                f"bypass marker '{COMMENT_BYPASS_MARKER}' without a reason; "
                "write the reason after the marker, or delete the marker"
            ),
        )
        for row in empty_rows
        if row in line_numbers
    )

    for row, code_text, comment_text in records:
        if comment_text is None or row not in line_numbers or row in waived:
            continue
        body = _comment_body(comment_text)
        if not body:
            continue

        narration = _CHANGE_NARRATION_RE.search(body)
        if narration is not None:
            findings.append(
                CommentFinding(
                    file_path=file_path,
                    line=row,
                    rule_id=RULE_CHANGE_NARRATION,
                    severity=SEVERITY_BLOCKING,
                    detail=(
                        f"comment narrates a change ('{narration.group(0)}'); delete it, "
                        "or rewrite it to record what the code cannot say (intent, a "
                        "non-obvious constraint, an invariant, a workaround with its "
                        "removal trigger, or an external contract)"
                    ),
                )
            )

        if _is_commented_out_code(body):
            findings.append(
                CommentFinding(
                    file_path=file_path,
                    line=row,
                    rule_id=RULE_COMMENTED_OUT_CODE,
                    severity=SEVERITY_BLOCKING,
                    detail=(
                        "comment holds commented-out code; delete the comment, or "
                        "restore the statement as live code"
                    ),
                )
            )

        if not code_text and _is_section_banner(comment_text):
            findings.append(
                CommentFinding(
                    file_path=file_path,
                    line=row,
                    rule_id=RULE_SECTION_BANNER,
                    severity=SEVERITY_BLOCKING,
                    detail=(
                        "comment is a section banner; delete it — the module "
                        "structure already carries the outline"
                    ),
                )
            )

        reviewer_rule = _reviewer_rule(body)
        if reviewer_rule == RULE_OWNERLESS_MARKER:
            findings.append(
                CommentFinding(
                    file_path=file_path,
                    line=row,
                    rule_id=RULE_OWNERLESS_MARKER,
                    severity=SEVERITY_BLOCKING,
                    detail=(
                        "deferred-work marker without an owner and a removal trigger; "
                        "add both (for example 'TODO(@owner): remove when <condition>'), "
                        "or delete the marker"
                    ),
                )
            )
        elif reviewer_rule == RULE_REVIEWER_NOTE:
            findings.append(
                CommentFinding(
                    file_path=file_path,
                    line=row,
                    rule_id=RULE_REVIEWER_NOTE,
                    severity=SEVERITY_BLOCKING,
                    detail=(
                        "comment addresses the reviewer; rewrite it as a durable "
                        "statement about the code, or delete it"
                    ),
                )
            )

        compared = code_text
        if not compared:
            index = bisect_right(code_rows, row)
            if index < len(code_rows):
                compared = code_texts[code_rows[index]]
        if _restates_code(body, compared):
            findings.append(
                CommentFinding(
                    file_path=file_path,
                    line=row,
                    rule_id=RULE_RESTATES_CODE,
                    severity=SEVERITY_ADVISORY,
                    detail=(
                        "comment repeats the code it documents; delete it — the code "
                        "already says this"
                    ),
                )
            )

    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.rule_id))
    return findings


def _new_line_numbers(baseline_text: str | None, current_text: str) -> set[int]:
    """Return the 1-based numbers of the current lines that are new or changed.

    A line is new when the baseline does not contain it: either the file
    is new, or the line was added, or its content was modified. Lines that
    merely moved inside the file are unchanged content and are not
    reported. The comparison is multiset-based (not positional), so an
    unrelated edit earlier in the file cannot turn every following line
    into a false regression.
    """
    current_lines = current_text.splitlines()
    if baseline_text is None:
        return set(range(1, len(current_lines) + 1))
    remaining: dict[str, int] = {}
    for line in baseline_text.splitlines():
        remaining[line] = remaining.get(line, 0) + 1
    new_lines: set[int] = set()
    for index, line in enumerate(current_lines, start=1):
        if remaining.get(line, 0) > 0:
            remaining[line] -= 1
        else:
            new_lines.add(index)
    return new_lines


def audit_comments(
    baseline: Mapping[str, str],
    current: Mapping[str, str],
) -> list[CommentFinding]:
    """Audit the change between two content sets and return every finding.

    Args:
        baseline: Content set of the BASELINE tree (path -> text).
        current: Content set of the CURRENT tree (path -> text).

    Returns:
        The findings, ordered by path then line then rule. Only NEW or
        CHANGED lines are reported.
    """
    findings: list[CommentFinding] = []
    for path in sorted(current):
        if Path(path).suffix not in SCANNED_SUFFIXES:
            continue
        current_text = current[path]
        baseline_text = baseline.get(path)
        line_numbers = _new_line_numbers(baseline_text, current_text)
        findings.extend(scan_comment_lines(path, current_text, line_numbers))
    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.rule_id))
    return findings


def _read_python_sources(root: Path) -> dict[str, str]:
    """Return ``{relative posix path: text}`` for every Python file under *root*.

    Only Python source is read: the gate's rules are statements about
    code comments, so reading every text file in the tree would be work
    the gate does not need. Unreadable and non-UTF-8 files are skipped
    silently: they carry no reviewable content, and a decode error must
    never turn into a gate failure with no actionable finding.
    """
    contents: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.suffix not in SCANNED_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        try:
            contents[relative.as_posix()] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return contents


def audit_comment_roots(
    baseline_root: Path | None,
    current_root: Path,
) -> tuple[list[CommentFinding], int]:
    """Audit the change between two repository roots (or one root, no baseline).

    Args:
        baseline_root: The committed baseline tree, or None for an empty baseline.
        current_root: The current working tree.

    Returns:
        The findings the change introduced and the number of findings the
        committed baseline tree still carries — the burn-down count.
    """
    baseline = _read_python_sources(baseline_root) if baseline_root is not None else {}
    current = _read_python_sources(current_root)
    findings = audit_comments(baseline, current)
    baseline_count = len(audit_comments({}, baseline))
    return findings, baseline_count


def _default_root() -> Path:
    """Return the repository root the gate scans when no root is given."""
    return Path(__file__).parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    """Run the comment-policy gate and return an exit code.

    The run reports the burn-down count: how many findings the committed
    baseline tree still carries.

    Exit code 0: no blocking finding. Exit code 1: blocking findings.
    Exit code 2: a root was not found, or the arguments were wrong.
    """
    args = argv if argv is not None else sys.argv[1:]
    if len(args) > MAX_CLI_ROOT_ARGUMENTS:
        print(
            "Usage: python -m ralph.testing.audit_comments [baseline_root] [current_root]",
            file=sys.stderr,
        )
        return 2
    if len(args) == MAX_CLI_ROOT_ARGUMENTS:
        baseline_root: Path | None = Path(args[0])
        current_root = Path(args[1])
    elif len(args) == 1:
        baseline_root = None
        current_root = Path(args[0])
    else:
        current_root = _default_root()
        baseline_root = head_tree(current_root)
        if baseline_root is None:
            print(
                "Error: no committed baseline is available for this package; "
                "pass <baseline_root> <current_root> explicitly.",
                file=sys.stderr,
            )
            return 2
    for root in (baseline_root, current_root):
        if root is not None and not root.is_dir():
            print(f"Error: audit root not found: {root}", file=sys.stderr)
            return 2

    baseline_label = str(baseline_root) if baseline_root is not None else "(empty baseline)"
    print(f"Auditing comments (baseline diff): baseline={baseline_label} current={current_root}")
    print()

    findings, baseline_count = audit_comment_roots(baseline_root, current_root)
    blocking = [finding for finding in findings if finding.severity == SEVERITY_BLOCKING]
    advisory = [finding for finding in findings if finding.severity == SEVERITY_ADVISORY]

    if blocking:
        print(
            f"COMMENT-POLICY FINDINGS: {len(blocking)} blocking finding(s), "
            f"{len(advisory)} advisory; {baseline_count} pre-existing finding(s) "
            "in the baseline"
        )
        print("=" * 72)
        for finding in blocking:
            print(f"  {finding}")
        print()
        print(
            "A comment MUST record only what the code cannot say: intent, a "
            "non-obvious constraint, an invariant, a workaround with its removal "
            "trigger, or an external contract. Delete the comment, or rewrite it. "
            f"Add an inline '# {COMMENT_BYPASS_MARKER}: <reason>' marker only for a "
            "false positive."
        )
    if advisory:
        print(f"ADVISORY COMMENT FINDINGS: {len(advisory)} finding(s)")
        print("=" * 72)
        for finding in advisory:
            print(f"  {finding}")
        print()
        print(
            "Advisory findings are reported for review and do not fail the gate: "
            "the over-commenting evidence is weaker than the comment/code "
            "inconsistency evidence."
        )

    if blocking:
        return 1

    print(
        f"No blocking comment-policy findings ({len(advisory)} advisory; "
        f"{baseline_count} pre-existing finding(s) in the baseline)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
