"""Audit the MCP-to-agent boundary containment contracts."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


class BoundaryViolation:
    def __init__(self, path: Path, line: int, detail: str) -> None:
        self.path = path
        self.line = line
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.detail}"


def _calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Name) and node.func.id == name)
             or (isinstance(node.func, ast.Attribute) and node.func.attr == name))
    ]


def _call_has_issuer(call: ast.Call) -> bool:
    return any(keyword.arg == "issuer" for keyword in call.keywords) or (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "teardown_subtree"
        and isinstance(call.func.value, ast.Call)
        and isinstance(call.func.value.func, ast.Name)
        and call.func.value.func.id == "DefaultProcessTeardown"
    )


def _has_keyword(call: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in call.keywords)


def _is_e2big_handler(handler: ast.ExceptHandler) -> bool:
    return handler.type is not None and "errno.E2BIG" in ast.unparse(handler)


def _translates_e2big(handler: ast.ExceptHandler) -> bool:
    source = ast.unparse(handler)
    return "errno.E2BIG" in source and "AgentLaunchError" in source


def audit_source(path: Path, source: str) -> list[BoundaryViolation]:
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return [BoundaryViolation(path, 1, "source does not parse")]
    violations: list[BoundaryViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "reset_tool_registry_callback":
            violations.append(BoundaryViolation(path, node.lineno, "unscoped reset wrapper is forbidden"))
        if isinstance(node, ast.ImportFrom) and any(alias.name == "reset_tool_registry_callback" for alias in node.names):
            violations.append(BoundaryViolation(path, node.lineno, "unscoped reset import is forbidden"))
    if path.name == "_spawn_validation.py" and (
        "raise OSError(7" in source or "raise OSError(errno.E2BIG" in source
    ):
        violations.append(BoundaryViolation(path, 1, "bare E2BIG raise is forbidden"))
    if path.name == "_process_manager.py":
        violations.extend(
            BoundaryViolation(path, handler.lineno, "OSError handler needs typed E2BIG translation")
            for handler in ast.walk(tree)
            if isinstance(handler, ast.ExceptHandler)
            and _is_e2big_handler(handler)
            and not _translates_e2big(handler)
        )
    violations.extend(
        BoundaryViolation(path, call.lineno, "watchdog error needs issuer and runtime_event")
        for call in _calls_named(tree, "IdleWatchdogKilledError")
        if not _has_keyword(call, "runtime_event") or not _has_keyword(call, "issuer")
    )
    if "ralph/agents/" in path.as_posix() or "ralph/executor/" in path.as_posix():
        violations.extend(
            BoundaryViolation(path, call.lineno, "teardown needs issuer")
            for call in _calls_named(tree, "teardown_subtree")
            if not _call_has_issuer(call)
        )
    if path.name == "_agent_inactivity_timeout_error.py" and "failure_origin" not in source:
        violations.append(BoundaryViolation(path, 1, "inactivity error needs a failure origin"))
    return violations


def _required_source_violations(root: Path) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    bridge = root / "pipeline" / "session_bridge.py"
    bridge_source = bridge.read_text(encoding="utf-8")
    if (
        "_ACTIVE_RESET_STATE" not in bridge_source
        or 'scope_key == "unscoped"' not in bridge_source
        or "_RESET_SCOPE_LOCK" not in bridge_source
    ):
        violations.append(BoundaryViolation(bridge, 1, "scoped reset containment gate is missing"))
    watchdog = root / "agents" / "idle_watchdog_kill.py"
    watchdog_source = watchdog.read_text(encoding="utf-8")
    if "runtime_event" not in watchdog_source or "failure_origin" not in watchdog_source:
        violations.append(BoundaryViolation(watchdog, 1, "watchdog causal attribution is missing"))
    manager = root / "process" / "manager" / "_process_manager.py"
    manager_source = manager.read_text(encoding="utf-8")
    manager_tree = ast.parse(manager_source, filename=str(manager))
    catches_oserror = any(
        isinstance(node, ast.ExceptHandler)
        and node.type is not None
        and "OSError" in ast.unparse(node.type)
        for node in ast.walk(manager_tree)
    )
    if not catches_oserror or "errno.E2BIG" not in manager_source or "AgentLaunchError" not in manager_source:
        violations.append(BoundaryViolation(manager, 1, "Popen E2BIG must use AgentLaunchError"))
    return violations


def audit(root: Path) -> list[BoundaryViolation]:
    violations = _required_source_violations(root)
    for path in sorted(root.rglob("*.py")):
        violations.extend(audit_source(path, path.read_text(encoding="utf-8")))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root = Path(args[0]) if args else Path(__file__).parent.parent
    if not root.is_dir():
        print(f"Error: audit root not found: {root}", file=sys.stderr)
        return 2
    violations = audit(root)
    if violations:
        print("MCP-AGENT BOUNDARY VIOLATIONS:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("No MCP-agent boundary violations found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
