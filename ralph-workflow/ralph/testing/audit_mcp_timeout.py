"""MCP timeout contract audit.

Enforces, via AST analysis (not regex), that no operation under ``ralph/mcp/``
performs blocking I/O without a bounded, fail-closed timeout. A blocking call
that can hang the MCP server thread starves the agent of output and trips the
idle watchdog — exactly the nanocoder hang this contract prevents.

Flagged (a contract violation that fails ``make verify``):
  - ``subprocess.run(...)`` without a ``timeout=`` keyword.
  - any ``.communicate(...)`` without a ``timeout=`` keyword (the first
    positional argument to ``communicate`` is ``input``, NOT a timeout).
  - any ``.wait(...)`` without a timeout (``wait``'s first positional IS the
    timeout, so ``.wait(5)`` and ``.wait(timeout=5)`` are both fine).
  - network calls (``httpx.*`` / ``requests.*`` request methods + clients,
    ``urllib.request.urlopen``, ``socket.create_connection``) without
    ``timeout=``.

NOT flagged (Python semantics — they take no ``timeout=``): ``subprocess.Popen``
construction, ``socket.socket(...)``, ``socket.getaddrinfo`` (bound via
``socket.setdefaulttimeout``).

Escape hatch: an inline ``# mcp-timeout-ok: <reason>`` marker on the call's line
suppresses the violation (the only allowlist mechanism — keep it rare and
justified).

Usage:
    python -m ralph.testing.audit_mcp_timeout [mcp_root]

Exit 0 = clean, 1 = violations, 2 = root not found.
"""

from __future__ import annotations

import ast
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

_ALLOW_MARKER = "mcp-timeout-ok"

# Network request methods / client constructors that perform blocking I/O and
# must carry an explicit ``timeout=``.
_HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "request", "stream"}
)
_HTTP_CLIENTS: frozenset[str] = frozenset({"Client", "AsyncClient", "Session"})
_HTTP_ROOTS: frozenset[str] = frozenset({"httpx", "requests"})


class McpTimeoutViolation:
    """A single MCP-timeout contract violation."""

    def __init__(self, file_path: str, line: int, category: str, detail: str) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


def _has_keyword(node: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in node.keywords)


def _dotted_name(node: ast.Call) -> str | None:
    """Return the dotted function name when the receiver chain is plain names."""
    func: ast.expr = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    else:
        return None
    return ".".join(reversed(parts))


def _is_network_call(name: str | None) -> bool:
    if name is None:
        return False
    parts = name.split(".")
    last = parts[-1]
    if last in {"urlopen", "create_connection"}:
        return True
    return parts[0] in _HTTP_ROOTS and (last in _HTTP_METHODS or last in _HTTP_CLIENTS)


class McpTimeoutAuditor(ast.NodeVisitor):
    """AST visitor that detects unbounded blocking I/O."""

    def __init__(self, file_path: str, source: str) -> None:
        self.file_path = file_path
        self.source_lines = source.splitlines()
        self.violations: list[McpTimeoutViolation] = []

    def _allowed(self, node: ast.AST) -> bool:
        lineno: int = getattr(node, "lineno", 0)
        if 1 <= lineno <= len(self.source_lines):
            return _ALLOW_MARKER in self.source_lines[lineno - 1]
        return False

    def _add(self, node: ast.AST, category: str, detail: str) -> None:
        if self._allowed(node):
            return
        lineno: int = getattr(node, "lineno", 0)
        self.violations.append(
            McpTimeoutViolation(
                file_path=self.file_path,
                line=lineno,
                category=category,
                detail=detail,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        # Method calls by attribute name, receiver-agnostic (catches chained
        # receivers like spawn().communicate() that a dotted-name resolver
        # would miss).
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr == "communicate" and not _has_keyword(node, "timeout"):
                self._add(node, "communicate", ".communicate() without timeout=")
            elif attr == "wait" and not node.args and not _has_keyword(node, "timeout"):
                self._add(node, "wait", ".wait() without a timeout")

        name = _dotted_name(node)
        if name == "subprocess.run" and not _has_keyword(node, "timeout"):
            self._add(node, "subprocess_run", "subprocess.run() without timeout=")
        elif _is_network_call(name) and not _has_keyword(node, "timeout"):
            self._add(node, "network", f"{name}() without timeout=")

        self.generic_visit(node)


def audit_mcp_file(file_path: Path) -> list[McpTimeoutViolation]:
    """Audit a single Python file for MCP-timeout violations."""
    source = file_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []
    auditor = McpTimeoutAuditor(str(file_path), source)
    auditor.visit(tree)
    return auditor.violations


def audit_mcp_directory(root: Path) -> tuple[list[McpTimeoutViolation], int]:
    """Audit every Python file under ``root``. Returns (violations, files_checked)."""
    all_violations: list[McpTimeoutViolation] = []
    files_checked = 0
    for py_file in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in py_file.relative_to(root).parts):
            continue
        all_violations.extend(audit_mcp_file(py_file))
        files_checked += 1
    return all_violations, files_checked


def main(argv: list[str] | None = None) -> int:
    """Run the MCP-timeout audit and return an exit code."""
    args = argv if argv is not None else sys.argv[1:]
    mcp_root = Path(args[0]) if args else (Path(__file__).parent.parent / "mcp")

    if not mcp_root.is_dir():
        print(f"Error: MCP root not found: {mcp_root}", file=sys.stderr)
        return 2

    print(f"Auditing MCP timeout contract in: {mcp_root}")
    print()

    violations, files_checked = audit_mcp_directory(mcp_root)

    if violations:
        print(
            f"MCP TIMEOUT CONTRACT VIOLATIONS: {len(violations)} in {files_checked} file(s)"
        )
        print("=" * 72)
        for v in violations:
            print(f"  {v}")
        print()
        print(
            "Every MCP operation must be bounded by a timeout and fail closed. "
            "Add a timeout=, or an inline '# mcp-timeout-ok: <reason>' marker if "
            "the call is genuinely unbounded by design."
        )
        return 1

    print(f"No MCP timeout violations found in {files_checked} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
