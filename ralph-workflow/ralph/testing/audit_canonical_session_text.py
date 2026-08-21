"""Audit the canonical interactive-session text and PTY exemption contracts.

The 2026-08-17 Claude production failure emitted ``Session ID:`` text into
an interactive PTY transcript.  That text is expected transport metadata,
not corrupt JSONL.  This audit uses both literal and AST-scoped checks so a
refactor cannot silently remove the classifier vocabulary or Claude's PTY
transport exemption.

Usage: ``python -m ralph.testing.audit_canonical_session_text``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_SESSION_SOURCE_PATH = "agents/invoke/_session.py"
#: The corruption detector, which owns the interactive-PTY exemption.
#: Split out of ``display/raw_overflow.py`` (which owns WRITING a
#: capture) when that module outgrew the size limit; this audit parses
#: the source, so it has to follow the constant rather than the name.
_RAW_LOG_BREAKS_SOURCE_PATH = "display/raw_log_breaks.py"

_CANONICAL_SESSION_TEXT_PATTERN_SOURCES: tuple[str, ...] = (
    r"^Claude session ready\. Session ID:\s*([A-Za-z0-9._:-]+)$",
    r"^Session ID:\s*([A-Za-z0-9._:-]+)$",
    r"^Resume this session with --resume\s+([A-Za-z0-9._:-]+)$",
    r"^--resume\s+([A-Za-z0-9._:-]+)$",
    r"^--session\s+([A-Za-z0-9._:-]+)$",
)
_INTERACTIVE_PTY_TRANSPORT_NAMES: frozenset[str] = frozenset(
    {"CLAUDE_INTERACTIVE", "NANOCODER", "AGY"}
)
_INVARIANT_COUNT = 2


def _read(rel_path: str) -> str:
    return (_PACKAGE_ROOT / rel_path).read_text(encoding="utf-8")


def _assignment_value(source: str, constant_name: str) -> ast.expr | None:
    """Return the AST value assigned to one module-level named constant."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == constant_name
        ):
            return node.value
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == constant_name:
                    return node.value
    return None


def _string_literals(value: ast.expr | None) -> tuple[str, ...] | None:
    """Return direct string literals from a tuple expression."""
    if not isinstance(value, ast.Tuple):
        return None
    strings: list[str] = []
    for element in value.elts:
        if not (
            isinstance(element, ast.Call)
            and isinstance(element.func, ast.Attribute)
            and element.func.attr == "compile"
            and element.args
            and isinstance(element.args[0], ast.Constant)
            and isinstance(element.args[0].value, str)
        ):
            return None
        strings.append(element.args[0].value)
    return tuple(strings)


def _transport_names(value: ast.expr | None) -> frozenset[str] | None:
    """Return enum-member names from a ``frozenset({...})`` literal."""
    if not (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "frozenset"
        and value.args
        and isinstance(value.args[0], ast.Set)
    ):
        return None
    names: set[str] = set()
    for element in value.args[0].elts:
        if not (
            isinstance(element, ast.Attribute)
            and isinstance(element.value, ast.Name)
            and element.value.id == "AgentTransport"
        ):
            return None
        names.add(element.attr)
    return frozenset(names)


def _check_canonical_session_text_patterns() -> list[str]:
    """Pin the exact AST-scoped canonical session-text pattern vocabulary."""
    try:
        source = _read(_SESSION_SOURCE_PATH)
    except FileNotFoundError:
        return [f"  {_SESSION_SOURCE_PATH}: file not found"]
    patterns = _string_literals(_assignment_value(source, "_TRANSPORT_SESSION_TEXT_PATTERNS"))
    if patterns != _CANONICAL_SESSION_TEXT_PATTERN_SOURCES:
        return [
            f"  {_SESSION_SOURCE_PATH}: canonical session text patterns mismatch "
            f"(expected {len(_CANONICAL_SESSION_TEXT_PATTERN_SOURCES)} pinned patterns)"
        ]
    return []


def _check_interactive_pty_transports() -> list[str]:
    """Pin the exact AST-scoped interactive PTY transport exemption set."""
    try:
        source = _read(_RAW_LOG_BREAKS_SOURCE_PATH)
    except FileNotFoundError:
        return [f"  {_RAW_LOG_BREAKS_SOURCE_PATH}: file not found"]
    transports = _transport_names(_assignment_value(source, "_INTERACTIVE_PTY_TRANSPORTS"))
    if transports != _INTERACTIVE_PTY_TRANSPORT_NAMES:
        return [
            f"  {_RAW_LOG_BREAKS_SOURCE_PATH}: interactive PTY transports mismatch "
            f"(expected {sorted(_INTERACTIVE_PTY_TRANSPORT_NAMES)})"
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    """Run every invariant and return 0 when the Claude regression remains protected."""
    del argv
    problems = _check_canonical_session_text_patterns() + _check_interactive_pty_transports()
    if problems:
        print(f"CANONICAL SESSION TEXT AUDIT FAILED: {len(problems)} invariant violation(s)")
        for problem in problems:
            print(problem)
        return 1
    print(
        "canonical session text audit: OK "
        f"({len(_CANONICAL_SESSION_TEXT_PATTERN_SOURCES)} canonical patterns; "
        f"interactive PTY transports={sorted(_INTERACTIVE_PTY_TRANSPORT_NAMES)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
