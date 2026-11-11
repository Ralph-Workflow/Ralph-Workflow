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


_FILE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ralph/mcp/artifacts/canonical_submit.py",
    }
)

_ARG_INDEX_2 = 2

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
    _needed_targets = {".agent", *(target for target, _c, _d in _FORBIDDEN_CALLS)}
    missing = sorted(
        target
        for target in _needed_targets
        if not any(needle in target for needle in _AUDIT_PRE_FILTER_NEEDLES)
    )
    if missing:
        raise RuntimeError(
            "_AUDIT_PRE_FILTER_NEEDLES is missing forbidden-pattern needles: " + ", ".join(missing)
        )


_assert_invariants()


class BypassFinding:
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
    literal_segments = [segment for segment in segments if segment is not None]
    if not literal_segments:
        return None
    return "/".join(literal_segments)


def _path_has_variable_segment(segments: list[str | None]) -> bool:
    return any(segment is None for segment in segments)


def _path_matches_forbidden(path_expr: ast.expr) -> tuple[str, str] | None:
    literals = _collect_string_literals(path_expr)
    if literals:
        combined = "".join(literals)
        for pattern, category, detail in _FORBIDDEN_PATH_PATTERNS:
            if re.search(pattern, combined):
                return category, detail

    segments = _path_segments(path_expr)
    joined = _join_path_segments(segments)
    if joined is not None:
        for pattern, category, detail in _FORBIDDEN_PATH_PATTERNS:
            if re.search(pattern, joined):
                return category, detail

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
    if not isinstance(node.func, ast.Attribute):
        return False
    return node.func.attr == "write_text"


def _is_write_bytes_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    return node.func.attr == "write_bytes"


def _is_open_call(node: ast.Call) -> bool:
    name = _dotted_name(node.func)
    return name in {"open", "io.open"}


def _is_forbidden_function_call(node: ast.Call) -> tuple[str, str] | None:
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
    if not node.args:
        return None
    return _finding_from_path_match(_path_matches_forbidden(node.args[0]), rel_path, lineno)


_SHUTIL_METHODS: frozenset[str] = frozenset({"copy", "copy2", "copyfile", "copytree", "move"})
_OS_RENAME_METHODS: frozenset[str] = frozenset({"rename", "renames", "replace"})
_PATH_REPLACE_METHODS: frozenset[str] = frozenset({"replace", "rename"})


def _method_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_shutil_move_call(node: ast.Call) -> bool:
    name = _method_name(node)
    return name is not None and name in _SHUTIL_METHODS


def _is_os_rename_call(node: ast.Call) -> bool:
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
    name = _method_name(node)
    if name is None or name not in _PATH_REPLACE_METHODS:
        return False
    dotted = _dotted_name(node.func)
    return not (dotted is not None and dotted.startswith("os."))


def _find_shutil_destination(node: ast.Call) -> ast.expr | None:
    if len(node.args) >= _ARG_INDEX_2:
        return node.args[1]
    for kw in node.keywords:
        if kw.arg == "dst":
            return kw.value
    return None


def _find_os_rename_destination(node: ast.Call) -> ast.expr | None:
    if len(node.args) >= _ARG_INDEX_2:
        return node.args[1]
    return None


def _find_path_replace_destination(node: ast.Call) -> ast.expr | None:
    if node.args:
        return node.args[0]
    return None


def _find_shutil_move_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    dst = _find_shutil_destination(node)
    if dst is None:
        return None
    return _finding_from_path_match(_path_matches_forbidden(dst), rel_path, lineno)


def _find_os_rename_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    dst = _find_os_rename_destination(node)
    if dst is None:
        return None
    return _finding_from_path_match(_path_matches_forbidden(dst), rel_path, lineno)


def _find_path_replace_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    dst = _find_path_replace_destination(node)
    if dst is None:
        return None
    return _finding_from_path_match(_path_matches_forbidden(dst), rel_path, lineno)


def _find_forbidden_call_finding(
    node: ast.Call,
    rel_path: str,
    lineno: int,
) -> BypassFinding | None:
    match = _is_forbidden_function_call(node)
    return _finding_from_path_match(match, rel_path, lineno)


def _process_call_node(
    node: ast.Call,
    rel_path: str,
) -> BypassFinding | None:
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
