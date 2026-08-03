"""Artifact-submission canonical-path audit.

Enforces the single-writer contract for run-scoped completion receipts,
completion sentinels, and canonical artifact files. Any code outside the
allowlisted canonical sites that writes one of these files is a bypass and
fails ``make verify``.

Scans ``ralph/`` (skipping the audit module itself, the canonical submit
module, and ``tests/``). Uses AST analysis to find:

- Direct writes to ``.agent/receipts/``, ``.agent/completion_seen_*.json``,
  ``.agent/artifacts/<canonical-type>.md`` (and its obsolete ``.json`` form), or
  ``.agent/tmp/<canonical-type>.json`` (via ``write_text``, ``write_bytes``,
  ``open(...)``, or equivalent file-copy helpers).
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

from ralph.mcp.tools.artifact import KNOWN_ARTIFACT_TYPES

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

_CANONICAL_TYPES: frozenset[str] = KNOWN_ARTIFACT_TYPES


# File paths (relative to codebase root) that own the audited writes/calls.
_FILE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ralph/mcp/artifacts/canonical_submit.py",
    }
)

_ARG_INDEX_2 = 2

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
        r"\.agent/artifacts/(?:" + "|".join(_CANONICAL_TYPES) + r")\.(?:md|json)",
        "canonical_artifact_write",
        "direct write to .agent/artifacts/<canonical-type>.(md|json) outside canonical submit",
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
        "write_artifact_receipt",
        "receipt_helper",
        "call to write_artifact_receipt outside canonical submit",
    ),
    (
        "delete_artifact_receipt",
        "receipt_helper",
        "call to delete_artifact_receipt outside canonical submit",
    ),
    (
        "shutil.copy",
        "shutil_bypass",
        "shutil.copy into protected path outside canonical submit",
    ),
    (
        "shutil.copy2",
        "shutil_bypass",
        "shutil.copy2 into protected path outside canonical submit",
    ),
    (
        "shutil.copyfile",
        "shutil_bypass",
        "shutil.copyfile into protected path outside canonical submit",
    ),
    (
        "shutil.copytree",
        "shutil_bypass",
        "shutil.copytree into protected path outside canonical submit",
    ),
    (
        "shutil.move",
        "shutil_bypass",
        "shutil.move into protected path outside canonical submit",
    ),
    (
        "os.rename",
        "os_rename_bypass",
        "os.rename into protected path outside canonical submit",
    ),
    (
        "os.renames",
        "os_rename_bypass",
        "os.renames into protected path outside canonical submit",
    ),
    (
        "os.replace",
        "os_rename_bypass",
        "os.replace into protected path outside canonical submit",
    ),
    (
        "Path.replace",
        "path_replace_bypass",
        "Path.replace into protected path outside canonical submit",
    ),
    (
        "Path.rename",
        "path_replace_bypass",
        "Path.rename into protected path outside canonical submit",
    ),
)

_AUDIT_MODULE_ROOT = Path(__file__).parent.parent.parent

# Cheap text pre-filter: any source that contains none of these substrings
# cannot match a forbidden path literal or forbidden call target, so the
# full AST pass on it is pure overhead. On a 1121-file tree the pre-filter
# collapses the AST workload from ~1100 files to ~150 files and reduces
# the audit end-to-end wall time from ~29s to well under the 30s
# per-step cap ``_VERIFY_STEP_TIMEOUT_SECONDS`` enforced by
# ``ralph/verify.py``. Keep this list synced with
# ``_FORBIDDEN_PATH_PATTERNS`` (string side) and ``_FORBIDDEN_CALLS``
# (function side). A module is only worth a full AST walk if it
# contains at least one of these needles.
_AUDIT_PRE_FILTER_NEEDLES: tuple[str, ...] = (
    ".agent",
    "write_artifact_receipt",
    "delete_artifact_receipt",
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.copytree",
    "shutil.move",
    "os.rename",
    "os.renames",
    "os.replace",
    "Path.rename",
    "Path.replace",
)


def _assert_invariants() -> None:
    """Import-time guard: if/raise RuntimeError (NOT assert) so python -O cannot strip."""
    if not _CANONICAL_TYPES:
        raise RuntimeError("_CANONICAL_TYPES must not be empty")
    if "commit_message" not in _CANONICAL_TYPES:
        raise RuntimeError("_CANONICAL_TYPES must contain 'commit_message'")
    if "plan" not in _CANONICAL_TYPES:
        raise RuntimeError("_CANONICAL_TYPES must contain 'plan'")
    if not _FILE_ALLOWLIST:
        raise RuntimeError("_FILE_ALLOWLIST must not be empty")
    for _path in _FILE_ALLOWLIST:
        if not (_AUDIT_MODULE_ROOT / _path).is_file():
            raise RuntimeError(f"_FILE_ALLOWLIST entry does not exist: {_path}")
    if not _SKIP_DIRS:
        raise RuntimeError("_SKIP_DIRS must not be empty")
    if not _AUDIT_PRE_FILTER_NEEDLES:
        raise RuntimeError("_AUDIT_PRE_FILTER_NEEDLES must not be empty")
    # The pre-filter must mention every forbidden literal substring so the
    # AST short-circuit cannot silently drop a file that contains an
    # audited literal or call. ``.agent/`` covers every
    # ``_FORBIDDEN_PATH_PATTERNS`` entry; each ``_FORBIDDEN_CALLS`` target
    # is added verbatim so files importing ``shutil.copy`` etc. survive
    # the pre-filter.
    _needed_targets = {".agent", *(target for target, _c, _d in _FORBIDDEN_CALLS)}
    missing = sorted(
        target
        for target in _needed_targets
        if not any(needle in target for needle in _AUDIT_PRE_FILTER_NEEDLES)
    )
    if missing:
        raise RuntimeError(
            "_AUDIT_PRE_FILTER_NEEDLES is missing forbidden-pattern needles: "
            + ", ".join(missing)
        )


_assert_invariants()


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
        return f"{self.file_path}:{self.line}: [ARTIFACT-BYPASS] {self.category}: {self.detail}"


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


def _path_segments(node: ast.AST) -> list[str | None]:
    """Return path segments for a ``Path(...) / ... / ...`` expression.

    Literal segments are returned as strings; non-literal segments (names,
    attribute lookups, pure f-string placeholders, etc.) are returned as
    ``None``. This preserves the structure needed to reconstruct a path with
    ``/`` separators while still detecting variable-composed paths.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _path_segments(node.left) + _path_segments(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        parts: list[str | None] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append(None)
        return parts or [None]
    if isinstance(node, ast.Call):
        func_name = _dotted_name(node.func)
        if func_name == "Path" and node.args:
            return _path_segments(node.args[0])
    return [None]


def _join_path_segments(segments: list[str | None]) -> str | None:
    """Join literal path segments with ``/``, skipping non-literal segments."""
    literal_segments = [segment for segment in segments if segment is not None]
    if not literal_segments:
        return None
    return "/".join(literal_segments)


def _path_has_variable_segment(segments: list[str | None]) -> bool:
    return any(segment is None for segment in segments)


def _path_matches_forbidden(path_expr: ast.expr) -> tuple[str, str] | None:
    """Return (category, detail) if the path expression contains a forbidden pattern."""
    # Candidate 1: legacy flat concatenation of all string literals in the
    # expression. Catches simple cases like ``Path('.agent/receipts/x.json')``.
    literals = _collect_string_literals(path_expr)
    if literals:
        combined = "".join(literals)
        for pattern, category, detail in _FORBIDDEN_PATH_PATTERNS:
            if re.search(pattern, combined):
                return category, detail

    # Candidate 2: reconstructed ``Path(...) / ...`` composition with ``/``
    # separators. Catches ``Path('.agent') / 'receipts' / run_id / ...``.
    segments = _path_segments(path_expr)
    joined = _join_path_segments(segments)
    if joined is not None:
        for pattern, category, detail in _FORBIDDEN_PATH_PATTERNS:
            if re.search(pattern, joined):
                return category, detail

        # Candidate 3: variable-composed paths under ``.agent/artifacts/`` or
        # ``.agent/tmp/`` whose filename we cannot resolve statically. Because
        # the type may be a canonical artifact type, treat canonical-directory
        # writes as bypasses; literal fully-known paths are handled by candidate
        # 2. Markdown writes under ``.agent/tmp/`` remain the supported agent
        # fallback and are intentionally not forbidden.
        if _path_has_variable_segment(segments):
            lower = joined.lower()
            if ".agent/artifacts/" in lower:
                return (
                    "canonical_artifact_write",
                    "direct write to .agent/artifacts/<variable> outside canonical submit",
                )
            if ".agent/tmp/" in lower and joined.endswith(".json"):
                return (
                    "fallback_tmp_write",
                    "direct write to .agent/tmp/<variable>.json outside canonical submit",
                )

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


def _is_write_bytes_call(node: ast.Call) -> bool:
    """Return True for backend.write_bytes / Path.write_bytes style calls."""
    if not isinstance(node.func, ast.Attribute):
        return False
    return node.func.attr == "write_bytes"


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


def _finding_from_path_match(
    match: tuple[str, str] | None,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    """Return a finding from a forbidden-path match, or None."""
    if match is None:
        return None
    category, detail = match
    return BypassFinding(
        file_path=rel_path,
        line=lineno,
        category=category,
        detail=detail,
    )


def _find_write_text_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    """Check a ``write_text`` call for forbidden paths."""
    candidates: list[ast.expr] = []
    if node.args:
        candidates.append(node.args[0])
    if isinstance(node.func, ast.Attribute):
        candidates.append(node.func.value)
    for path_expr in candidates:
        finding = _finding_from_path_match(_path_matches_forbidden(path_expr), rel_path, lineno)
        if finding is not None:
            return finding
    return None


def _find_write_bytes_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    """Check a ``write_bytes`` call for forbidden paths."""
    candidates: list[ast.expr] = []
    if node.args:
        candidates.append(node.args[0])
    if isinstance(node.func, ast.Attribute):
        candidates.append(node.func.value)
    for path_expr in candidates:
        finding = _finding_from_path_match(_path_matches_forbidden(path_expr), rel_path, lineno)
        if finding is not None:
            return finding
    return None


def _find_open_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    """Check an ``open`` call for forbidden paths."""
    if not node.args:
        return None
    return _finding_from_path_match(_path_matches_forbidden(node.args[0]), rel_path, lineno)


_SHUTIL_METHODS: frozenset[str] = frozenset({"copy", "copy2", "copyfile", "copytree", "move"})
_OS_RENAME_METHODS: frozenset[str] = frozenset({"rename", "renames", "replace"})
_PATH_REPLACE_METHODS: frozenset[str] = frozenset({"replace", "rename"})


def _method_name(node: ast.Call) -> str | None:
    """Return the method name for a call like obj.method(...), or None."""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_shutil_move_call(node: ast.Call) -> bool:
    """Return True for any call whose method name matches a shutil copy/move function."""
    name = _method_name(node)
    return name is not None and name in _SHUTIL_METHODS


def _is_os_rename_call(node: ast.Call) -> bool:
    """Return True for any call whose method name matches os.rename/renames/replace."""
    name = _method_name(node)
    if name is None or name not in _OS_RENAME_METHODS:
        return False
    dotted = _dotted_name(node.func)
    if dotted is not None and (dotted.startswith("Path.") or dotted.startswith("pathlib.")):
        return False
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
        base_func = _dotted_name(node.func.value.func)
        if base_func in {"Path", "pathlib.Path"}:
            return False
    return True


def _is_path_replace_call(node: ast.Call) -> bool:
    """Return True for any call whose method name matches Path.replace/rename."""
    name = _method_name(node)
    if name is None or name not in _PATH_REPLACE_METHODS:
        return False
    dotted = _dotted_name(node.func)
    return not (dotted is not None and dotted.startswith("os."))


def _find_shutil_destination(node: ast.Call) -> ast.expr | None:
    """Extract the destination argument from a shutil call (2nd positional or dst= keyword)."""
    if len(node.args) >= _ARG_INDEX_2:
        return node.args[1]
    for kw in node.keywords:
        if kw.arg == "dst":
            return kw.value
    return None


def _find_os_rename_destination(node: ast.Call) -> ast.expr | None:
    """Extract the destination argument from os.rename/replace (2nd positional)."""
    if len(node.args) >= _ARG_INDEX_2:
        return node.args[1]
    return None


def _find_path_replace_destination(node: ast.Call) -> ast.expr | None:
    """Extract the destination argument from Path.replace/rename (1st positional)."""
    if node.args:
        return node.args[0]
    return None


def _find_shutil_move_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    """Check a shutil copy/move call for forbidden destination paths."""
    dst = _find_shutil_destination(node)
    if dst is None:
        return None
    return _finding_from_path_match(_path_matches_forbidden(dst), rel_path, lineno)


def _find_os_rename_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    """Check an os.rename/replace call for forbidden destination paths."""
    dst = _find_os_rename_destination(node)
    if dst is None:
        return None
    return _finding_from_path_match(_path_matches_forbidden(dst), rel_path, lineno)


def _find_path_replace_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    """Check a Path.replace/rename call for forbidden destination paths."""
    dst = _find_path_replace_destination(node)
    if dst is None:
        return None
    return _finding_from_path_match(_path_matches_forbidden(dst), rel_path, lineno)


def _find_forbidden_call_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    """Check a function call for forbidden lower-level helpers."""
    match = _is_forbidden_function_call(node)
    return _finding_from_path_match(match, rel_path, lineno)


def _process_call_node(
    node: ast.Call,
    rel_path: str,
) -> BypassFinding | None:
    """Inspect a single AST call node and return any bypass finding."""
    lineno: int = node.lineno if isinstance(node.lineno, int) else 0

    finding: BypassFinding | None = None
    if _is_write_text_call(node):
        finding = _find_write_text_finding(node, rel_path, lineno)
    elif _is_write_bytes_call(node):
        finding = _find_write_bytes_finding(node, rel_path, lineno)
    elif _is_open_call(node):
        finding = _find_open_finding(node, rel_path, lineno)
    elif _is_shutil_move_call(node):
        finding = _find_shutil_move_finding(node, rel_path, lineno)
    elif _is_os_rename_call(node):
        finding = _find_os_rename_finding(node, rel_path, lineno)
    elif _is_path_replace_call(node):
        finding = _find_path_replace_finding(node, rel_path, lineno)
    else:
        finding = _find_forbidden_call_finding(node, rel_path, lineno)
    return finding


def audit_file(file_path: Path, rel_path: str) -> list[BypassFinding]:
    """Audit a single Python file for canonical-path bypasses.

    Modules whose source contains no ``_AUDIT_PRE_FILTER_NEEDLES`` substring
    cannot match a forbidden literal or forbidden call target; the AST pass
    on them is pure overhead. The cheap bytes-level pre-filter short-circuits
    those files before ``ast.parse`` is paid for.
    """
    findings: list[BypassFinding] = []
    try:
        source_bytes = file_path.read_bytes()
    except OSError:
        return findings
    if not any(needle.encode("utf-8") in source_bytes for needle in _AUDIT_PRE_FILTER_NEEDLES):
        return findings
    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return findings

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        finding = _process_call_node(node, rel_path)
        if finding is not None:
            findings.append(finding)

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
