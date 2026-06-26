"""MCP timeout contract audit.

Enforces, via AST analysis (not regex), that no operation under ``ralph/mcp/``
performs blocking I/O without a bounded, fail-closed timeout. A blocking call
that can hang the MCP server thread starves the agent of output and trips the
idle watchdog — exactly the nanocoder hang this contract prevents.

Flagged (a contract violation that fails ``make verify``):
  - ``subprocess.run/call/check_call/check_output(...)`` without a ``timeout=``
    keyword (resolved through import aliases, so ``import subprocess as sp;
    sp.run(...)`` and ``from subprocess import run; run(...)`` are also caught).
  - ``subprocess.getoutput``/``getstatusoutput`` and ``os.system`` — these take
    no timeout at all, so they are always flagged unless marked.
  - any ``.communicate(...)`` / ``.communicate_and_cleanup(...)`` without a
    ``timeout=`` keyword (the first positional argument is ``input``, NOT a
    timeout).
  - any ``.wait(...)`` without a timeout (``wait``'s first positional IS the
    timeout, so ``.wait(5)`` and ``.wait(timeout=5)`` are both fine).
  - network calls (``httpx.*`` / ``requests.*`` request methods + clients,
    ``urllib.request.urlopen``, ``socket.create_connection``) without
    ``timeout=`` (also resolved through import aliases).

NOT flagged (Python semantics — they take no ``timeout=``): ``subprocess.Popen``
construction, ``socket.socket(...)``, ``socket.getaddrinfo`` (bound via
``socket.setdefaulttimeout``).

Escape hatch: an inline ``# mcp-timeout-ok: <reason>`` marker on the call's line
suppresses the violation (the only allowlist mechanism — keep it rare and
justified).

Best-effort scope: this is name-based AST analysis. It resolves ``import x as y``
and ``from x import y [as z]`` bindings, but deliberately-obfuscated indirection
(assignment rebind ``r = subprocess.run; r(...)``, ``getattr(subprocess, 'run')``,
``importlib.import_module('subprocess').run(...)``) is out of scope — closing that
would require dataflow tracking. The guard targets honest unbounded calls, not an
adversary working around it.

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

# subprocess functions that accept a ``timeout=`` and must carry one.
_SUBPROCESS_TIMEOUT_FUNCS: frozenset[str] = frozenset({"run", "call", "check_call", "check_output"})
# Blocking process calls that take NO timeout argument at all — always unbounded,
# so they require an explicit ``# mcp-timeout-ok`` marker to pass.
_ALWAYS_UNBOUNDED: frozenset[str] = frozenset(
    {"subprocess.getoutput", "subprocess.getstatusoutput", "os.system"}
)
# Methods whose first positional is NOT a timeout, so a bare ``timeout=`` keyword
# is required (mirrors ``.communicate``).
_TIMEOUT_KEYWORD_METHODS: frozenset[str] = frozenset({"communicate", "communicate_and_cleanup"})


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


def _is_subprocess_timeout_func(name: str | None) -> bool:
    if name is None:
        return False
    return name in {f"subprocess.{func}" for func in _SUBPROCESS_TIMEOUT_FUNCS}


def _collect_import_aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve ``import x as y`` / ``from x import y [as z]`` bindings so an
    aliased call cannot evade the contract.

    Returns (module_aliases, from_imports):
      - module_aliases: local module alias -> canonical module ("sp" -> "subprocess")
      - from_imports: local name -> canonical dotted path ("run" -> "subprocess.run")
    """
    module_aliases: dict[str, str] = {}
    from_imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None:
                    module_aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                local = alias.asname or alias.name
                from_imports[local] = f"{node.module}.{alias.name}"
    return module_aliases, from_imports


class McpTimeoutAuditor(ast.NodeVisitor):
    """AST visitor that detects unbounded blocking I/O."""

    def __init__(
        self,
        file_path: str,
        source: str,
        *,
        module_aliases: dict[str, str] | None = None,
        from_imports: dict[str, str] | None = None,
    ) -> None:
        self.file_path = file_path
        self.source_lines = source.splitlines()
        self.violations: list[McpTimeoutViolation] = []
        self._module_aliases = module_aliases or {}
        self._from_imports = from_imports or {}

    def _canonical_name(self, name: str | None) -> str | None:
        """Resolve a dotted call name through the module's import aliases so
        ``sp.run`` -> ``subprocess.run`` and bare ``run`` -> ``subprocess.run``."""
        if name is None:
            return None
        parts = name.split(".")
        if len(parts) == 1:
            return self._from_imports.get(parts[0], parts[0])
        head = self._module_aliases.get(parts[0], parts[0])
        return ".".join([head, *parts[1:]])

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

    def visit_For(self, node: ast.For) -> None:
        # A ``for line in <proc>.stdout:`` (or .stderr) is a blocking, unbounded
        # line read over a live pipe — it cannot be interrupted by a timeout and
        # wedges the reader on a hung child. Iterating a tuple of pipes or a
        # ``.splitlines()`` result is fine (node.iter is a Tuple/Call, not Attribute).
        if isinstance(node.iter, ast.Attribute) and node.iter.attr in {"stdout", "stderr"}:
            self._add(
                node,
                "blocking_stream_iter",
                f"blocking iteration over .{node.iter.attr} (use a bounded/"
                "interruptible read, or mark if interrupted by close())",
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Method calls by attribute name, receiver-agnostic (catches chained
        # receivers like spawn().communicate() that a dotted-name resolver
        # would miss).
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in _TIMEOUT_KEYWORD_METHODS and not _has_keyword(node, "timeout"):
                self._add(node, "communicate", f".{attr}() without timeout=")
            elif attr == "wait" and not node.args and not _has_keyword(node, "timeout"):
                self._add(node, "wait", ".wait() without a timeout")

        name = self._canonical_name(_dotted_name(node))
        if name in _ALWAYS_UNBOUNDED:
            self._add(node, "unbounded_call", f"{name}() is unbounded (takes no timeout)")
        elif _is_subprocess_timeout_func(name) and not _has_keyword(node, "timeout"):
            self._add(node, "subprocess_run", f"{name}() without timeout=")
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
    module_aliases, from_imports = _collect_import_aliases(tree)
    auditor = McpTimeoutAuditor(
        str(file_path),
        source,
        module_aliases=module_aliases,
        from_imports=from_imports,
    )
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


def _default_roots() -> list[Path]:
    """Roots audited when no explicit root is given.

    Covers ``ralph/mcp`` (the MCP server thread), ``ralph/git`` (git invoked
    outside the MCP layer — operations, rebase, vendor-drift checks),
    ``ralph/process`` (the subprocess layer the MCP/git paths call
    into synchronously, including ``ProcessManager`` and the rest of the
    tree), ``ralph/executor`` (the sync + async process runners
    ``run_process`` / ``run_process_async``), ``ralph/agents`` (the
    subprocess agent executor), and ``ralph/pro_support`` (the bounded
    Pro heartbeat client that performs network I/O). An unbounded call
    in any of these can hang the agent just as badly, so all are held
    to the same bounded-subprocess contract.
    """
    package_root = Path(__file__).parent.parent
    return [
        package_root / "mcp",
        package_root / "git",
        package_root / "process",
        package_root / "executor",
        package_root / "agents",
        package_root / "pro_support",
    ]


def main(argv: list[str] | None = None) -> int:
    """Run the bounded-subprocess audit and return an exit code."""
    args = argv if argv is not None else sys.argv[1:]
    roots = [Path(args[0])] if args else _default_roots()

    all_violations: list[McpTimeoutViolation] = []
    total_files = 0
    for root in roots:
        if not root.is_dir():
            print(f"Error: audit root not found: {root}", file=sys.stderr)
            return 2
        print(f"Auditing bounded-subprocess contract in: {root}")
        violations, files_checked = audit_mcp_directory(root)
        all_violations.extend(violations)
        total_files += files_checked
    print()

    if all_violations:
        print(
            f"BOUNDED-SUBPROCESS CONTRACT VIOLATIONS: {len(all_violations)}"
            f" in {total_files} file(s)"
        )
        print("=" * 72)
        for v in all_violations:
            print(f"  {v}")
        print()
        print(
            "Every MCP/git operation must be bounded by a timeout and fail closed. "
            "Add a timeout=, or an inline '# mcp-timeout-ok: <reason>' marker if "
            "the call is genuinely unbounded by design."
        )
        return 1

    print(f"No bounded-subprocess violations found in {total_files} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
