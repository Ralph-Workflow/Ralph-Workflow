from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping

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

_HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "request", "stream"}
)
_HTTP_CLIENTS: frozenset[str] = frozenset({"Client", "AsyncClient", "Session"})
_HTTP_ROOTS: frozenset[str] = frozenset({"httpx", "requests"})

_SUBPROCESS_TIMEOUT_FUNCS: frozenset[str] = frozenset({"run", "call", "check_call", "check_output"})
_ALWAYS_UNBOUNDED: frozenset[str] = frozenset(
    {"subprocess.getoutput", "subprocess.getstatusoutput", "os.system"}
)
_TIMEOUT_KEYWORD_METHODS: frozenset[str] = frozenset({"communicate", "communicate_and_cleanup"})

_PACKAGE_DIR: Path = Path(__file__).resolve().parent.parent
_PACKAGE_PARENT: Path = _PACKAGE_DIR.parent

_ExemptionKey: TypeAlias = tuple[str, str, str, int]

STRUCTURAL_EXEMPTIONS: dict[_ExemptionKey, str] = {
    (
        "ralph.mcp.protocol.transport",
        "StdioTransport._read_loop",
        "blocking_stream_iter",
        0,
    ): "the MCP stdout pipe read is unblocked by close(), which the transport calls on shutdown",
    (
        "ralph.mcp.server._fallback_standalone_server",
        "_FallbackStandaloneServer.run._stop_watcher",
        "wait",
        0,
    ): "the daemon watcher thread blocks until the caller sets the stop event it waits on",
    (
        "ralph.process.manager._managed_async_process",
        "ManagedAsyncProcess.wait",
        "wait",
        0,
    ): "the wait is wrapped by asyncio.wait_for with a bounded timeout",
    (
        "ralph.process.manager._managed_async_process",
        "ManagedAsyncProcess.wait_supervised",
        "wait",
        0,
    ): "the supervised wait is bounded by the caller's activity-aware idle watchdog",
    (
        "ralph.process.manager._managed_async_process",
        "ManagedAsyncProcess.communicate",
        "communicate",
        0,
    ): "the communicate is wrapped by asyncio.wait_for with a bounded timeout",
    (
        "ralph.process.manager._managed_process",
        "ManagedProcess._wait_after_output_eof",
        "wait",
        0,
    ): "no inactivity and no absolute deadline apply, so the caller accepts an unbounded reap",
    (
        "ralph.process.manager._managed_pty_process",
        "ManagedPtyProcess.__exit__",
        "wait",
        0,
    ): "the reap runs only after the process was already terminated",
    (
        "ralph.process.manager._process_manager",
        "ProcessManager._reap_async_and_mark",
        "wait",
        0,
    ): "the reap is wrapped by asyncio.wait_for with a bounded timeout",
    (
        "ralph.process.manager._process_manager",
        "ProcessManager._terminate_root_only_async",
        "wait",
        0,
    ): "the graceful terminate wait is wrapped by asyncio.wait_for with a bounded timeout",
    (
        "ralph.process.manager._process_manager",
        "ProcessManager._terminate_root_only_async",
        "wait",
        1,
    ): "the force-kill follow-up wait is wrapped by asyncio.wait_for with a bounded timeout",
    (
        "ralph.agents.invoke._workspace_change_coalescer",
        "WorkspaceChangeCoalescer.close",
        "wait",
        0,
    ): "the teardown waits for the single active drain to finish before returning",
    (
        "ralph.agents.subprocess_executor",
        "SubprocessAgentExecutor._supervise",
        "wait",
        0,
    ): "the wait is wrapped by asyncio.wait_for with a bounded timeout",
    (
        "ralph.executor.process",
        "run_process_async",
        "communicate",
        0,
    ): "the communicate task is bounded by the asyncio.wait timeout of the caller",
    (
        "ralph.executor.process",
        "run_process_async",
        "wait",
        0,
    ): "the wait is wrapped by asyncio.wait_for with a bounded timeout",
}


class McpTimeoutViolation:
    def __init__(self, file_path: str, line: int, category: str, detail: str) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


def _has_keyword(node: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in node.keywords)


def _is_none_expr(node: ast.expr) -> bool:
    return (isinstance(node, ast.Constant) and node.value is None) or (
        isinstance(node, ast.Name) and node.id == "None"
    )


def _has_bounded_timeout_keyword(node: ast.Call) -> bool:
    return any(kw.arg == "timeout" and not _is_none_expr(kw.value) for kw in node.keywords)


def _first_positional_is_bounded_timeout(node: ast.Call) -> bool:
    if not node.args:
        return False
    return not _is_none_expr(node.args[0])


def _dotted_name(node: ast.Call) -> str | None:
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


def module_identity(file_path: Path) -> str:
    resolved = file_path.resolve()
    try:
        relative = resolved.relative_to(_PACKAGE_PARENT)
    except ValueError:
        return resolved.stem
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class McpTimeoutAuditor(ast.NodeVisitor):
    def __init__(
        self,
        file_path: str,
        *,
        module_aliases: dict[str, str] | None = None,
        from_imports: dict[str, str] | None = None,
        exemptions: Mapping[_ExemptionKey, str] | None = None,
        module: str = "",
    ) -> None:
        self.file_path = file_path
        self.violations: list[McpTimeoutViolation] = []
        self.used_exemptions: set[_ExemptionKey] = set()
        self._module_aliases = module_aliases or {}
        self._from_imports = from_imports or {}
        self._function_scopes: list[set[str]] = []
        self._callable_stack: list[str] = []
        self._module = module
        self._exemptions: Mapping[_ExemptionKey, str] = (
            STRUCTURAL_EXEMPTIONS if exemptions is None else exemptions
        )
        self._category_counts: dict[tuple[str, str], int] = {}

    def _canonical_name(self, name: str | None) -> str | None:
        if name is None:
            return None
        parts = name.split(".")
        if len(parts) == 1:
            return self._from_imports.get(parts[0], parts[0])
        head = self._module_aliases.get(parts[0], parts[0])
        return ".".join([head, *parts[1:]])

    def _qualified_callable(self) -> str:
        return ".".join(self._callable_stack)

    def _add(self, node: ast.AST, category: str, detail: str) -> None:
        callable_name = self._qualified_callable()
        counter_key = (callable_name, category)
        index = self._category_counts.get(counter_key, 0)
        self._category_counts[counter_key] = index + 1
        key: _ExemptionKey = (self._module, callable_name, category, index)
        if key in self._exemptions:
            self.used_exemptions.add(key)
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

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._function_scopes.append(set())
        self._callable_stack.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self._callable_stack.pop()
            self._function_scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._callable_stack.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self._callable_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        if (
            self._function_scopes
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "submit"
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._function_scopes[-1].add(target.id)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        if isinstance(node.iter, ast.Attribute) and node.iter.attr in {"stdout", "stderr"}:
            self._add(
                node,
                "blocking_stream_iter",
                f"blocking iteration over .{node.iter.attr} (use a bounded/"
                "interruptible read, or mark if interrupted by close())",
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in _TIMEOUT_KEYWORD_METHODS and not _has_bounded_timeout_keyword(node):
                self._add(node, "communicate", f".{attr}() without bounded timeout=")
            elif attr == "wait":
                bounded = _first_positional_is_bounded_timeout(
                    node
                ) or _has_bounded_timeout_keyword(node)
                if not bounded:
                    self._add(node, "wait", ".wait() without a bounded timeout")
            elif (
                attr == "result"
                and isinstance(node.func.value, ast.Name)
                and self._function_scopes
                and node.func.value.id in self._function_scopes[-1]
                and not _has_bounded_timeout_keyword(node)
            ):
                self._add(
                    node,
                    "future_result",
                    ".result() on submit-bound Future without bounded timeout=",
                )

        name = self._canonical_name(_dotted_name(node))
        if name in _ALWAYS_UNBOUNDED:
            self._add(node, "unbounded_call", f"{name}() is unbounded (takes no timeout)")
        elif _is_subprocess_timeout_func(name) and not _has_bounded_timeout_keyword(node):
            self._add(node, "subprocess_run", f"{name}() without bounded timeout=")
        elif _is_network_call(name) and not _has_bounded_timeout_keyword(node):
            self._add(node, "network", f"{name}() without bounded timeout=")

        self.generic_visit(node)


def _audit_file(
    file_path: Path,
    exemptions: Mapping[_ExemptionKey, str],
) -> tuple[list[McpTimeoutViolation], set[_ExemptionKey]]:
    source = file_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return [], set()
    module_aliases, from_imports = _collect_import_aliases(tree)
    auditor = McpTimeoutAuditor(
        str(file_path),
        module_aliases=module_aliases,
        from_imports=from_imports,
        exemptions=exemptions,
        module=module_identity(file_path),
    )
    auditor.visit(tree)
    return auditor.violations, auditor.used_exemptions


def audit_mcp_file(
    file_path: Path,
    *,
    exemptions: Mapping[_ExemptionKey, str] | None = None,
) -> list[McpTimeoutViolation]:
    registry = STRUCTURAL_EXEMPTIONS if exemptions is None else exemptions
    violations, _used = _audit_file(file_path, registry)
    return violations


def _stale_exemption_violations(
    registry: Mapping[_ExemptionKey, str],
    audited_modules: set[str],
    used_by_module: Mapping[str, set[_ExemptionKey]],
) -> list[McpTimeoutViolation]:
    violations: list[McpTimeoutViolation] = []
    for key in sorted(registry):
        module = key[0]
        if module not in audited_modules:
            continue
        if key in used_by_module.get(module, frozenset()):
            continue
        violations.append(
            McpTimeoutViolation(
                file_path=module,
                line=0,
                category="stale_exemption",
                detail=(
                    f"structural exemption for {module} {key[1]} category {key[2]} "
                    f"occurrence {key[3]} matches no audited call site"
                ),
            )
        )
    return violations


def audit_mcp_directory(
    root: Path,
    *,
    exemptions: Mapping[_ExemptionKey, str] | None = None,
) -> tuple[list[McpTimeoutViolation], int]:
    registry = STRUCTURAL_EXEMPTIONS if exemptions is None else exemptions
    all_violations: list[McpTimeoutViolation] = []
    used_by_module: dict[str, set[_ExemptionKey]] = {}
    audited_modules: set[str] = set()
    files_checked = 0
    for py_file in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in py_file.relative_to(root).parts):
            continue
        violations, used = _audit_file(py_file, registry)
        module = module_identity(py_file)
        all_violations.extend(violations)
        used_by_module.setdefault(module, set()).update(used)
        audited_modules.add(module)
        files_checked += 1
    all_violations.extend(_stale_exemption_violations(registry, audited_modules, used_by_module))
    return all_violations, files_checked


def _default_roots() -> list[Path]:
    package_root = Path(__file__).parent.parent
    return [
        package_root / "mcp",
        package_root / "git",
        package_root / "process",
        package_root / "executor",
        package_root / "agents",
        package_root / "pro_support",
        package_root / "api",
        package_root / "update_check",
        package_root / "contrib",
    ]


def main(argv: list[str] | None = None) -> int:
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
            "Add a timeout=, or register a structural exemption in "
            "ralph/testing/audit_mcp_timeout.py when the call is genuinely "
            "unbounded by design."
        )
        return 1

    print(f"No bounded-subprocess violations found in {total_files} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
