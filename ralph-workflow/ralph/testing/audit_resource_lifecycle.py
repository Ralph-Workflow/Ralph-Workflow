"""Resource-lifecycle audit (AST-based).

Enforces the resource-lifecycle contract documented in
``ralph-workflow/docs/agents/memory-lifecycle.md``:

1. ``threading.Thread(...)`` / ``Thread(...)`` calls MUST have
   ``daemon=True`` — non-daemon threads can block process exit on the
   interpreter shutdown atexit join that ``concurrent.futures``
   registers for its default executor.
2. ``httpx.Client(...)``, ``httpx.AsyncClient(...)``, and
   ``requests.Session(...)`` constructions MUST be the context-manager
   expression of a ``with`` statement — bare assignment leaks the
   underlying HTTP connection pool and may not be closed at interpreter
   exit.
3. ``os.open(...)``, ``os.openpty(...)``, and ``os.pipe(...)`` are
   allowed ONLY under ``ralph/process/`` (the centralized process
   lifecycle layer). Outside that allowlist, raw fd creation is a
   leak: it bypasses the centralized fd ownership policy and is not
   tracked by the zombie reaper.

The audit resolves ``import x as y`` / ``from x import y [as z]``
bindings so an aliased call cannot evade detection (``import httpx as
hx; hx.Client()`` and ``from httpx import Client; Client()`` are both
caught).

Escape hatch: an inline ``# resource-lifecycle-ok: <reason>`` marker
on the call's line suppresses the violation (the only allowlist
mechanism — keep it rare and justified).

Scope and exclusions (intentional, documented):

- ``ThreadPoolExecutor`` is NOT covered by the daemon-Thread rule; it
  has its own ``.shutdown()`` lifecycle owned by the caller.
- Bare ``open()`` is governed by ``audit_di_seam`` (composition-root
  env/open reads) and is OUT OF SCOPE here.
- ``loop.run_in_executor(None, ...)`` in ``ralph/interrupt/asyncio_bridge.py``
  is intentionally NOT covered — it is a bounded shutdown block
  owned by the asyncio bridge (different lifecycle), not a thread leak.
- The audit is AST-based and can only flag literal-name calls.
  Deliberate-obfuscation indirection (``getattr``, ``importlib``) is
  out of scope (would require dataflow tracking).

Usage:
    python -m ralph.testing.audit_resource_lifecycle [root1 ...]

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

# Inline marker that suppresses a violation (only escape hatch).
_ALLOW_MARKER = "resource-lifecycle-ok"

# threading.Thread / Thread constructors — these must carry daemon=True.
_THREAD_NAMES: frozenset[str] = frozenset({"threading.Thread", "Thread"})

# HTTP client constructors — these must be the context-manager expression
# of a with statement. Bare assignment leaks the connection pool.
_HTTP_CLIENT_NAMES: frozenset[str] = frozenset(
    {"httpx.Client", "httpx.AsyncClient", "requests.Session"}
)
_HTTP_ROOTS: frozenset[str] = frozenset({"httpx", "requests"})

# Raw os fd creation — only allowed under ralph/process/ (centralized
# process lifecycle layer). Outside that allowlist, this is a leak.
_RAW_OS_FD_NAMES: frozenset[str] = frozenset({"os.open", "os.openpty", "os.pipe"})

# Allowlist roots: directories where raw os fd creation is legitimate
# because the centralized process lifecycle owns the fd. Paths are
# matched against the relative-to-package-root path of the file under
# audit.
_RAW_OS_FD_ALLOWLIST_DIRS: tuple[str, ...] = (
    "process",
)


class ResourceLifecycleViolation:
    """A single resource-lifecycle contract violation."""

    def __init__(self, file_path: str, line: int, category: str, detail: str) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


def _keyword_truthy(node: ast.Call, name: str) -> bool:
    """Return True iff ``name=True`` is passed as an explicit boolean keyword.

    The ``daemon=True`` rule for ``threading.Thread`` must require the
    explicit ``True`` value — ``daemon=False``, ``daemon=1``,
    ``daemon="yes"``, or any non-boolean expression does NOT satisfy
    the contract because:

    - ``daemon=False`` is the same lifecycle hazard as omitting the
      argument (a non-daemon thread blocks interpreter exit);
    - ``daemon=1`` / ``daemon=0`` / ``daemon="yes"`` are truthy but
      ``threading.Thread.__init__`` rejects non-bool ``daemon`` values
      at runtime (``TypeError: daemon must be explicitly set to True``
      in Python 3.13+), so the call would already crash — flagging
      it at audit time surfaces the latent bug before it ships;
    - ``daemon=expr`` (any non-constant expression) cannot be
      statically resolved and MUST be flagged for human review.

    Only the literal ``True`` constant (``ast.Constant(value=True)``)
    is accepted. Everything else — ``False``, ``1``, ``0``,
    expressions, calls, names — is treated as "not explicitly
    daemon=True" so the violation is surfaced rather than silently
    accepted.
    """
    for kw in node.keywords:
        if kw.arg != name:
            continue
        value: ast.expr = kw.value
        if isinstance(value, ast.Constant):
            return value.value is True
        return False
    return False


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


def _collect_import_aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve ``import x as y`` / ``from x import y [as z]`` bindings.

    Returns:
        module_aliases: local module alias -> canonical module ("sp" -> "subprocess")
        from_imports: local name -> canonical dotted path ("Client" -> "httpx.Client")
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


def _canonical_name(
    name: str | None,
    module_aliases: dict[str, str],
    from_imports: dict[str, str],
) -> str | None:
    """Resolve a dotted call name through the module's import aliases so
    ``sp.run`` -> ``subprocess.run``, ``th.Thread`` -> ``threading.Thread``,
    and bare ``Client`` -> ``httpx.Client`` are caught.
    """
    if name is None:
        return None
    parts = name.split(".")
    if len(parts) == 1:
        return from_imports.get(parts[0], parts[0])
    head = module_aliases.get(parts[0], parts[0])
    return ".".join([head, *parts[1:]])


def _is_in_with(node: ast.Call, tree: ast.Module) -> bool:
    """Return True if ``node`` is the context-manager expression of a with statement.

    Both ``with httpx.Client() as client:`` (sync) and ``async with
    httpx.AsyncClient() as client:`` (async) are legitimate patterns
    and must NOT be flagged. Bare assignment (``client = httpx.Client()``
    or ``client = httpx.AsyncClient()``) is the violation. We walk
    the AST tree and accept either ``ast.With`` or ``ast.AsyncWith``
    whose ``items[i].context_expr`` is the same call node.
    """
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.With, ast.AsyncWith)):
            for item in parent.items:
                if item.context_expr is node:
                    return True
    return False


class ResourceLifecycleAuditor(ast.NodeVisitor):
    """AST visitor that detects resource-lifecycle contract violations."""

    def __init__(
        self,
        file_path: str,
        source: str,
        rel_path: str,
        *,
        module_aliases: dict[str, str] | None = None,
        from_imports: dict[str, str] | None = None,
    ) -> None:
        self.file_path = file_path
        self.rel_path = rel_path
        self.source_lines = source.splitlines()
        self.tree: ast.Module | None = None
        self.violations: list[ResourceLifecycleViolation] = []
        self._module_aliases = module_aliases or {}
        self._from_imports = from_imports or {}

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
            ResourceLifecycleViolation(
                file_path=self.file_path,
                line=lineno,
                category=category,
                detail=detail,
            )
        )

    def visit_Module(self, node: ast.Module) -> None:
        # Capture the module so we can check whether each Call is the
        # context-manager expression of a with statement. Stored on
        # self so ``_is_in_with`` can walk it without re-walking the
        # tree on every call.
        self.tree = node
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        canonical = _canonical_name(
            _dotted_name(node), self._module_aliases, self._from_imports
        )

        if canonical in _THREAD_NAMES and not _keyword_truthy(node, "daemon"):
            self._add(
                node,
                "non_daemon_thread",
                f"{canonical}() without daemon=True — non-daemon "
                "threads can block process exit",
            )

        if canonical in _HTTP_CLIENT_NAMES:
            tree = self.tree
            assert tree is not None, "visit_Module must run before visit_Call"
            if not _is_in_with(node, tree):
                self._add(
                    node,
                    "bare_http_client",
                    f"{canonical}() constructed outside a `with` "
                    "statement — leaks the HTTP connection pool",
                )

        if canonical in _RAW_OS_FD_NAMES and not self._is_in_raw_fd_allowlist():
            self._add(
                node,
                "raw_os_fd",
                f"{canonical}() outside ralph/process/ — raw fd "
                "creation must be centralized in the process lifecycle "
                "layer (relocate or add # resource-lifecycle-ok: <reason>)",
            )

        self.generic_visit(node)

    def _is_in_raw_fd_allowlist(self) -> bool:
        """True if ``self.rel_path`` is under one of the raw-fd allowlist dirs.

        The allowlist is a TUPLE of directory prefixes (relative to the
        ralph package root). A file whose relative path starts with
        one of these prefixes is allowed to create raw fds because the
        centralized process lifecycle owns those fds. Files in
        ``ralph/process/pty.py`` (``process/pty.py``) and any
        ``process/.../...py`` match the ``process`` allowlist.
        """
        rel = self.rel_path.replace("\\", "/")
        for prefix in _RAW_OS_FD_ALLOWLIST_DIRS:
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
        return False


def audit_resource_lifecycle_file(file_path: Path) -> list[ResourceLifecycleViolation]:
    """Audit a single Python file for resource-lifecycle violations.

    The ``file_path`` is resolved against the ralph package root to
    compute a relative path; the relative path is used to decide
    whether the file is in the raw-fd allowlist (ralph/process/).
    """
    package_root = Path(__file__).parent.parent
    try:
        rel_path = file_path.resolve().relative_to(package_root.resolve()).as_posix()
    except ValueError:
        # Outside the package root — treat as outside-allowlist for
        # raw fd checks. This is the conservative choice (flag any
        # raw os fd outside ralph/process/) and matches the contract.
        rel_path = file_path.as_posix()
    source = file_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []
    module_aliases, from_imports = _collect_import_aliases(tree)
    auditor = ResourceLifecycleAuditor(
        str(file_path),
        source,
        rel_path,
        module_aliases=module_aliases,
        from_imports=from_imports,
    )
    auditor.visit(tree)
    return auditor.violations


def audit_resource_lifecycle_directory(
    root: Path,
) -> tuple[list[ResourceLifecycleViolation], int]:
    """Audit every Python file under ``root``.

    Returns (violations, files_checked).
    """
    all_violations: list[ResourceLifecycleViolation] = []
    files_checked = 0
    for py_file in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in py_file.relative_to(root).parts):
            continue
        all_violations.extend(audit_resource_lifecycle_file(py_file))
        files_checked += 1
    return all_violations, files_checked


def _default_roots() -> list[Path]:
    """Roots audited when no explicit root is given.

    Covers every production package the resource-lifecycle contract
    applies to: ``ralph/mcp`` (HTTP client + daemon thread),
    ``ralph/agents`` (subprocess agent executor + daemon threads),
    ``ralph/executor`` (sync + async process runners),
    ``ralph/process`` (centralized process lifecycle; the raw-fd
    allowlist root), ``ralph/pipeline`` (run loop + interrupt threads),
    ``ralph/runtime`` (runtime helper modules),
    ``ralph/pro_support`` (Pro heartbeat client — daemon thread +
    HTTP client), and ``ralph/recovery`` (recovery control flow).
    """
    package_root = Path(__file__).parent.parent
    return [
        package_root / "mcp",
        package_root / "agents",
        package_root / "executor",
        package_root / "process",
        package_root / "pipeline",
        package_root / "runtime",
        package_root / "pro_support",
        package_root / "recovery",
    ]


def main(argv: list[str] | None = None) -> int:
    """Run the resource-lifecycle audit and return an exit code.

    When ``argv`` (or ``sys.argv[1:]``) is empty, audit the default
    production roots. When explicit roots are provided, audit EVERY
    one of them — a missing root short-circuits to exit 2 before any
    audit work, so a partial-pass output cannot hide a violating root.
    """
    args = argv if argv is not None else sys.argv[1:]
    roots = [Path(a) for a in args] if args else _default_roots()

    for root in roots:
        if not root.is_dir():
            print(f"Error: audit root not found: {root}", file=sys.stderr)
            return 2

    all_violations: list[ResourceLifecycleViolation] = []
    total_files = 0
    for root in roots:
        print(f"Auditing resource-lifecycle contract in: {root}")
        violations, files_checked = audit_resource_lifecycle_directory(root)
        all_violations.extend(violations)
        total_files += files_checked
    print()

    if all_violations:
        print(
            f"RESOURCE-LIFECYCLE CONTRACT VIOLATIONS: {len(all_violations)} "
            f"in {total_files} file(s)"
        )
        print("=" * 72)
        for v in all_violations:
            print(f"  {v}")
        print()
        print(
            "Production code MUST use daemon=True threads, with-managed HTTP "
            "clients, and raw os fd creation only under ralph/process/. Add an "
            "inline '# resource-lifecycle-ok: <reason>' marker if the call is "
            "genuinely bounded by a try/finally lifecycle (rare — keep it "
            "justified)."
        )
        return 1

    print(f"No resource-lifecycle violations found in {total_files} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
