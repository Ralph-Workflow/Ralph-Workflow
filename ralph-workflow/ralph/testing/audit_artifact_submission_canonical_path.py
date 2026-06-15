"""Artifact-submission canonical-path audit.

Enforces the single-writer contract for run-scoped completion receipts,
completion sentinels, and canonical artifact files. Any code outside the
allowlisted canonical sites that writes one of these files is a bypass and
fails ``make verify``.

Scans ``ralph/`` (skipping the audit module itself, the canonical submit
module, the marked executor block in ``tools/artifact.py``, the type-specific
artifact layout modules, and ``tests/``). Uses AST analysis to find:

- Direct writes to ``.agent/receipts/``, ``.agent/completion_seen_*.json``,
  ``.agent/artifacts/<canonical-type>.json``, or
  ``.agent/tmp/<canonical-type>.json``.
- Calls to the lower-level ``store.submit_artifact`` outside allowlisted sites.
- Calls to ``write_artifact_receipt`` / ``delete_artifact_receipt`` outside
  allowlisted sites.

Usage:
    python -m ralph.testing.audit_artifact_submission_canonical_path [codebase_root]

Exit 0 = clean, 1 = bypass found, 2 = root not found.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

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

_CANONICAL_TYPES: frozenset[str] = frozenset(
    {
        "commit_message",
        "plan",
        "smoke_test_result",
        "issues",
        "fix_result",
        "development_result",
        "review_analysis_decision",
        "planning_analysis_decision",
        "development_analysis_decision",
        "product_spec",
        "review",
        "commit_cleanup",
        "verification",
    }
)

# File paths (relative to codebase root) that are allowed to perform the
# audited writes/calls because they are part of the canonical chain or
# type-specific layout modules.
_FILE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ralph/mcp/artifacts/canonical_submit.py",
        "ralph/mcp/artifacts/commit_message.py",
        "ralph/mcp/artifacts/smoke_test_result.py",
    }
)

# Markers bounding the canonical submit call block in tools/artifact.py. The
# executor block itself is historical; after the refactor it contains a call to
# the canonical entry point, but the marker is preserved so future maintainers
# have a clear seam and the audit stays narrow.
_CANONICAL_BLOCK_START = "# === BEGIN CANONICAL SUBMIT OPS ==="
_CANONICAL_BLOCK_END = "# === END CANONICAL SUBMIT OPS ==="

# Forbidden path patterns. Stored as (regex, category, detail) tuples.
_FORBIDDEN_PATH_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        r"\.agent/receipts/",
        "receipt_write",
        "direct write to .agent/receipts/ outside canonical submit",
    ),
    (
        r"\.agent/completion_seen_",
        "sentinel_write",
        "direct write to .agent/completion_seen_*.json outside canonical submit",
    ),
    (
        r"\.agent/artifacts/(?:" + "|".join(_CANONICAL_TYPES) + r")\.json",
        "canonical_artifact_write",
        "direct write to .agent/artifacts/<canonical-type>.json outside canonical submit",
    ),
    (
        r"\.agent/tmp/(?:" + "|".join(_CANONICAL_TYPES) + r")\.json",
        "fallback_tmp_write",
        "direct write to .agent/tmp/<canonical-type>.json outside canonical submit",
    ),
)

# Lower-level functions that may only be called from allowlisted sites.
_FORBIDDEN_CALLS: tuple[tuple[str, str, str], ...] = (
    (
        "store.submit_artifact",
        "store_submit_artifact",
        "call to store.submit_artifact outside canonical submit",
    ),
    (
        "write_artifact_receipt",
        "receipt_helper",
        "call to write_artifact_receipt outside canonical submit",
    ),
    (
        "delete_artifact_receipt",
        "receipt_helper",
        "call to delete_artifact_receipt outside canonical submit",
    ),
)


class BypassFinding:
    """A single canonical-path bypass finding."""

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
        return (
            f"{self.file_path}:{self.line}: [ARTIFACT-BYPASS] "
            f"{self.category}: {self.detail}"
        )


def _collect_string_literals(node: ast.AST) -> list[str]:
    """Recursively collect every string constant anywhere in ``node``."""
    literals: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        literals.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                literals.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                literals.extend(_collect_string_literals(value.value))
    else:
        for child in ast.iter_child_nodes(node):
            literals.extend(_collect_string_literals(child))
    return literals


def _path_matches_forbidden(path_expr: ast.expr) -> tuple[str, str] | None:
    """Return (category, detail) if the path expression contains a forbidden pattern."""
    literals = _collect_string_literals(path_expr)
    if not literals:
        return None
    combined = "".join(literals)
    for pattern, category, detail in _FORBIDDEN_PATH_PATTERNS:
        if re.search(pattern, combined):
            return category, detail
    return None


def _dotted_name(node: ast.expr) -> str | None:
    """Return a dotted name for simple attribute/name chains."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    else:
        return None
    return ".".join(reversed(parts))


def _is_write_text_call(node: ast.Call) -> bool:
    """Return True for backend.write_text / Path.write_text style calls."""
    if not isinstance(node.func, ast.Attribute):
        return False
    return node.func.attr == "write_text"


def _is_open_call(node: ast.Call) -> bool:
    """Return True for open(...) / io.open(...) calls."""
    name = _dotted_name(node.func)
    return name in {"open", "io.open"}


def _is_forbidden_function_call(node: ast.Call) -> tuple[str, str] | None:
    """Return (category, detail) for calls to forbidden lower-level helpers."""
    name = _dotted_name(node.func)
    if name is None and isinstance(node.func, ast.Name):
        name = node.func.id
    for target, category, detail in _FORBIDDEN_CALLS:
        if name is not None and (name == target or name.endswith("." + target)):
            return category, detail
    return None


def _line_in_canonical_block(source_lines: list[str], lineno: int) -> bool:
    """Return True when lineno lies inside a canonical submit ops marker block."""
    in_block = False
    for idx, line in enumerate(source_lines, start=1):
        if _CANONICAL_BLOCK_START in line:
            in_block = True
        if _CANONICAL_BLOCK_END in line:
            if in_block and idx >= lineno:
                return True
            in_block = False
    return in_block


def audit_file(file_path: Path, rel_path: str) -> list[BypassFinding]:
    """Audit a single Python file for canonical-path bypasses."""
    findings: list[BypassFinding] = []
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return findings

    source_lines = source.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        lineno: int = node.lineno if isinstance(node.lineno, int) else 0
        if _line_in_canonical_block(source_lines, lineno):
            continue

        if _is_write_text_call(node):
            # ``backend.write_text(path, ...)`` carries the path in args[0];
            # ``Path(path).write_text(...)`` carries it in func.value. Check both
            # so pathlib-style direct writes are caught as well.
            candidates: list[ast.expr] = []
            if node.args:
                candidates.append(node.args[0])
            if isinstance(node.func, ast.Attribute):
                candidates.append(node.func.value)
            for path_expr in candidates:
                match = _path_matches_forbidden(path_expr)
                if match is not None:
                    category, detail = match
                    findings.append(
                        BypassFinding(
                            file_path=rel_path,
                            line=lineno,
                            category=category,
                            detail=detail,
                        )
                    )
                    break
            continue

        if _is_open_call(node):
            if node.args:
                match = _path_matches_forbidden(node.args[0])
                if match is not None:
                    category, detail = match
                    findings.append(
                        BypassFinding(
                            file_path=rel_path,
                            line=lineno,
                            category=category,
                            detail=detail,
                        )
                    )
            continue

        match = _is_forbidden_function_call(node)
        if match is not None:
            category, detail = match
            findings.append(
                BypassFinding(
                    file_path=rel_path,
                    line=lineno,
                    category=category,
                    detail=detail,
                )
            )

    return findings


def audit(codebase_root: Path | None = None) -> list[BypassFinding]:
    """Audit the codebase for artifact-submission bypasses.

    Args:
        codebase_root: Root directory to scan. Defaults to the ralph-workflow
            package root (three directories above this module).

    Returns:
        A list of bypass findings; empty when clean.
    """
    if codebase_root is None:
        codebase_root = Path(__file__).parent.parent.parent

    findings: list[BypassFinding] = []

    for py_file in sorted(codebase_root.rglob("*.py")):
        rel_path = str(py_file.relative_to(codebase_root))
        if any(part in _SKIP_DIRS for part in Path(rel_path).parts):
            continue
        if rel_path.startswith("tests/"):
            continue
        if rel_path == "ralph/testing/audit_artifact_submission_canonical_path.py":
            continue
        if rel_path in _FILE_ALLOWLIST:
            continue
        if rel_path == "ralph/mcp/tools/artifact.py":
            # Allowlisted block is handled per-line inside audit_file.
            pass

        findings.extend(audit_file(py_file, rel_path))

    return findings


def main(argv: list[str] | None = None) -> int:
    """Run the canonical-path audit and return an exit code."""
    args = argv if argv is not None else sys.argv[1:]
    codebase_root = Path(args[0]) if args else None

    if codebase_root is not None and not codebase_root.is_dir():
        print(f"Error: directory not found: {codebase_root}", file=sys.stderr)
        return 2

    root_for_print = codebase_root or Path(__file__).parent.parent.parent
    print(f"Auditing artifact-submission canonical path in: {root_for_print}")

    findings = audit(codebase_root)

    if findings:
        print(
            f"ARTIFACT SUBMISSION BYPASS(ES) FOUND: {len(findings)} finding(s)",
            file=sys.stderr,
        )
        print("=" * 72, file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        print(
            "Bypasses weaken the single-source-of-truth contract. Route through "
            "ralph.mcp.artifacts.canonical_submit.submit_artifact_canonical instead.",
            file=sys.stderr,
        )
        return 1

    print("No artifact-submission canonical-path bypasses found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
