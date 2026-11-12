from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing._audit_scope import qualified_scope

if TYPE_CHECKING:
    from collections.abc import Sequence

_DEFAULT_PACKAGE_ROOTS: tuple[str, ...] = ("ralph",)
_DEFAULT_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "executor/process.py",
        "process/manager/__init__.py",
        "process/manager/_process_manager.py",
        "testing/audit_filesystem_polling_invocation.py",
    }
)
_GETATTR_MIN_ARGS = 2

EXEMPT_CALL_SITES: frozenset[tuple[str, str, str]] = frozenset(
    {
        (
            "ralph/diagnostics/host_pressure.py",
            "_macos_maxfiles_pressure",
            "raw_subprocess_invocation",
        ),
        ("ralph/git/_lock_recovery.py", "_find_lock_holder", "raw_subprocess_invocation"),
        ("ralph/git/_lock_recovery.py", "_probe_lsof_fuser", "raw_subprocess_invocation"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_measure_operational", "raw_sleep_poll"),
        (
            "ralph/mcp/explore/_bench_r6_metrics.py",
            "measure_cold_build",
            "raw_subprocess_invocation",
        ),
        (
            "ralph/mcp/explore/_bench_r6_subprocess.py",
            "run_build_subprocess",
            "raw_subprocess_invocation",
        ),
        (
            "ralph/mcp/explore/_store_class_init.py",
            "_InitializeMethods._initialize",
            "raw_sleep_poll",
        ),
        (
            "ralph/mcp/explore/_store_class_init.py",
            "_InitializeMethods._initialize_with_bounded_retry",
            "raw_sleep_poll",
        ),
        (
            "ralph/mcp/server/_fallback_http_handler.py",
            "_FallbackHttpHandler.do_GET",
            "raw_sleep_poll",
        ),
        (
            "ralph/mcp/server/_fallback_standalone_server.py",
            "_FallbackStandaloneServer._setup_handover_pause._pause_watcher",
            "raw_sleep_poll",
        ),
        (
            "ralph/mcp/server/_fallback_standalone_server.py",
            "_FallbackStandaloneServer.run",
            "raw_sleep_poll",
        ),
        ("ralph/mcp/server/_handover_protocol.py", "wait_for_candidate_resume", "raw_sleep_poll"),
        ("ralph/mcp/server/_runtime_polling.py", "_poll_registry_build_gate", "raw_sleep_poll"),
        (
            "ralph/mcp/server/_transport_repetition_tracker.py",
            "_acquire_state_lock",
            "raw_sleep_poll",
        ),
        ("ralph/mcp/server/_wire_ledger.py", "append_wire_record", "raw_sleep_poll"),
        ("ralph/pipeline/integration_actions.py", "_SystemClock.sleep", "raw_sleep_poll"),
        ("ralph/pipeline/integration_attempt_ledger.py", "_take_lock", "raw_sleep_poll"),
        ("ralph/process/_pty_process.py", "PtyProcess.wait", "raw_sleep_poll"),
        ("ralph/process/manager/_supervisor.py", "_kill_tree_and_exit", "raw_sleep_poll"),
        ("ralph/process/teardown.py", "DefaultProcessTeardown._await_exit", "raw_sleep_poll"),
        (
            "ralph/process/teardown.py",
            "DefaultProcessTeardown._signal_process_group",
            "raw_sleep_poll",
        ),
        ("ralph/recovery/connectivity.py", "ConnectivityMonitor._probe_loop", "raw_sleep_poll"),
        ("ralph/telemetry/_user_identity.py", "_identity_lock", "raw_sleep_poll"),
        ("ralph/testing/_audit_baseline.py", "_run_git", "raw_subprocess_invocation"),
        ("ralph/testing/audit_comments.py", "_git_output", "raw_subprocess_invocation"),
        ("ralph/testing/fake_agent_executor.py", "FakeAgentExecutor.run", "raw_sleep_poll"),
    }
)


@dataclass(frozen=True)
class FilesystemPollingInvocationViolation:
    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: {self.kind}: {self.message}"


def _collect_python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _is_exempt(rel_path: str, exempt_paths: frozenset[str]) -> bool:
    return rel_path.removeprefix("ralph/") in exempt_paths


def _is_canonical_owner(module_path: Path, package_root: Path) -> bool:
    relative_path = module_path.relative_to(package_root).as_posix()
    normalized_path = relative_path.removeprefix("ralph/")
    return normalized_path in _DEFAULT_EXEMPT_PATHS


_SUBPROCESS_CALLS: frozenset[str] = frozenset(
    {"run", "call", "check_call", "check_output", "Popen"}
)


def _module_aliases(
    nodes: Sequence[ast.AST],
) -> tuple[set[str], set[str], set[str], dict[str, str]]:
    time_names = _imported_names(nodes, module="time", accepted={"time", "sleep"})
    asyncio_names = _imported_names(nodes, module="asyncio", accepted={"asyncio", "sleep"})
    observer_names = _observer_aliases(nodes)
    subprocess_names = _subprocess_aliases(nodes)
    return time_names, asyncio_names, observer_names, subprocess_names


def _observer_aliases(nodes: Sequence[ast.AST]) -> set[str]:
    aliases = _imported_names(
        nodes, module="watchdog.observers", accepted={"watchdog.observers", "Observer"}
    )
    for node in nodes:
        if isinstance(node, ast.Import):
            aliases.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name == "watchdog"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "watchdog":
            aliases.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name == "observers"
            )
    return aliases


def _subprocess_aliases(nodes: Sequence[ast.AST]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name == "subprocess":
                    aliases[imported.asname or imported.name] = "subprocess"
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for imported in node.names:
                if imported.name in _SUBPROCESS_CALLS:
                    aliases[imported.asname or imported.name] = imported.name
    return aliases


def _imported_names(nodes: Sequence[ast.AST], *, module: str, accepted: set[str]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name == module and imported.name in accepted
            )
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name in accepted
            )
    return names


def _attribute_root(node: ast.Attribute) -> str | None:
    value = node.value
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else None


def _dynamic_observer_constructor(node: ast.Call, observer_names: set[str]) -> bool:
    func = node.func
    if not (
        isinstance(func, ast.Call)
        and isinstance(func.func, ast.Name)
        and func.func.id == "getattr"
        and len(func.args) >= _GETATTR_MIN_ARGS
        and isinstance(func.args[0], ast.Name)
        and func.args[0].id in observer_names
        and isinstance(func.args[1], ast.Constant)
        and isinstance(func.args[1].value, str)
    ):
        return False
    return func.args[1].value == "Observer"


def _dynamic_subprocess_launcher(node: ast.Call, subprocess_names: dict[str, str]) -> bool:
    func = node.func
    if not (
        isinstance(func, ast.Call)
        and isinstance(func.func, ast.Name)
        and func.func.id == "getattr"
        and len(func.args) >= _GETATTR_MIN_ARGS
        and isinstance(func.args[0], ast.Name)
        and subprocess_names.get(func.args[0].id) == "subprocess"
        and isinstance(func.args[1], ast.Constant)
        and isinstance(func.args[1].value, str)
    ):
        return False
    return func.args[1].value in _SUBPROCESS_CALLS


_VIOLATION_MESSAGES: dict[str, str] = {
    "raw_observer_construction": "P1/P4 raw watchdog Observer construction; use lifecycle-owned WorkspaceMonitor",
    "raw_subprocess_invocation": "direct product-owned subprocess choice; route through the typed process executor",
    "raw_sleep_poll": "P3 timer-driven polling; use an injected clock and event/index-driven lifecycle owner",
}


def _violation_for_call(
    node: ast.Call,
    *,
    time_names: set[str],
    asyncio_names: set[str],
    observer_names: set[str],
    subprocess_names: dict[str, str],
) -> tuple[str, str] | None:
    func = node.func
    name = (
        func.id
        if isinstance(func, ast.Name)
        else func.attr
        if isinstance(func, ast.Attribute)
        else ""
    )
    root = _attribute_root(func) if isinstance(func, ast.Attribute) else None
    kind = (
        "raw_subprocess_invocation"
        if _dynamic_subprocess_launcher(node, subprocess_names)
        else "raw_observer_construction"
        if _dynamic_observer_constructor(node, observer_names)
        or (name == "Observer" and (name in observer_names or root in observer_names))
        else "raw_sleep_poll"
        if (
            name == "sleep"
            and (
                name in time_names
                or root in time_names
                or name in asyncio_names
                or root in asyncio_names
            )
        )
        else "raw_subprocess_invocation"
        if (
            (isinstance(func, ast.Name) and subprocess_names.get(name) in _SUBPROCESS_CALLS)
            or (
                isinstance(func, ast.Attribute)
                and name in _SUBPROCESS_CALLS
                and root is not None
                and subprocess_names.get(root) == "subprocess"
            )
        )
        else None
    )
    return (kind, _VIOLATION_MESSAGES[kind]) if kind is not None else None


def _scan_module(module_path: Path, rel_path: str) -> list[FilesystemPollingInvocationViolation]:
    try:
        source = module_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [
            FilesystemPollingInvocationViolation(
                "unreadable_module",
                rel_path,
                0,
                f"module could not be read ({exc}); audit fails closed",
            )
        ]
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return [
            FilesystemPollingInvocationViolation(
                "invalid_module",
                rel_path,
                exc.lineno or 1,
                "module could not be parsed; audit fails closed",
            )
        ]
    nodes = list(ast.walk(tree))
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
    time_names, asyncio_names, observer_names, subprocess_names = _module_aliases(nodes)
    violations: list[FilesystemPollingInvocationViolation] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        details = _violation_for_call(
            node,
            time_names=time_names,
            asyncio_names=asyncio_names,
            observer_names=observer_names,
            subprocess_names=subprocess_names,
        )
        if details is None:
            continue
        kind, message = details
        scope = qualified_scope(node, parents)
        if (rel_path, scope, kind) in EXEMPT_CALL_SITES:
            continue
        violations.append(
            FilesystemPollingInvocationViolation(kind, rel_path, node.lineno, message)
        )
    return violations


def audit_filesystem_polling_invocation(
    package_root: Path,
    *,
    module_paths: Sequence[str] | None = None,
    exempt_paths: frozenset[str] | Sequence[str] | None = None,
) -> list[FilesystemPollingInvocationViolation]:
    if not package_root.is_dir():
        return [
            FilesystemPollingInvocationViolation(
                "missing_package_root", str(package_root), 0, "package root could not be walked"
            )
        ]
    exempt = frozenset(exempt_paths) if exempt_paths is not None else _DEFAULT_EXEMPT_PATHS
    violations: list[FilesystemPollingInvocationViolation] = []
    if module_paths is None:
        missing_roots = [
            package_root / root_name
            for root_name in _DEFAULT_PACKAGE_ROOTS
            if not (package_root / root_name).is_dir()
        ]
        if missing_roots:
            return [
                FilesystemPollingInvocationViolation(
                    "missing_production_root",
                    root.relative_to(package_root).as_posix(),
                    0,
                    "expected production root could not be walked; restore it so the "
                    "filesystem polling/invocation audit can fail closed",
                )
                for root in missing_roots
            ]
        candidates = [
            (path, path.relative_to(package_root).as_posix())
            for root_name in _DEFAULT_PACKAGE_ROOTS
            for path in _collect_python_files(package_root / root_name)
        ]
    else:
        candidates = []
        resolved_root = package_root.resolve()
        for rel_path in module_paths:
            candidate = package_root / rel_path
            try:
                candidate.resolve().relative_to(resolved_root)
            except (OSError, ValueError):
                violations.append(
                    FilesystemPollingInvocationViolation(
                        "invalid_module_path",
                        rel_path,
                        0,
                        "explicit module path escapes the package root; pass a relative "
                        "production-module path so the filesystem polling/invocation "
                        "audit can fail closed",
                    )
                )
                continue
            candidates.append((candidate, rel_path))
    for module_path, rel_path in candidates:
        if _is_exempt(rel_path, exempt) and _is_canonical_owner(module_path, package_root):
            continue
        violations.extend(_scan_module(module_path, rel_path))
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    selected = sys.argv[1:] if argv is None else argv
    package_root = Path(selected[0]) if selected else Path(__file__).parent.parent.parent
    violations = audit_filesystem_polling_invocation(package_root)
    if violations:
        print(f"FILESYSTEM POLLING/INVOCATION VIOLATIONS: {len(violations)}")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("filesystem polling/invocation ownership audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
