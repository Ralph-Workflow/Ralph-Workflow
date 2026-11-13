from __future__ import annotations

import ast
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


_EXPECTED_CALLBACK_ARITY: int = 2
_MIN_RECORD_ARGS: int = 2


def _line_in_ranges(line: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= line <= end for start, end in ranges)


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

_WORKSPACE_EVENT_BINDING_ALLOWLIST: frozenset[str] = frozenset(
    {
        "agents/invoke/_pty_line_reader.py",
        "agents/invoke/_process_reader.py",
    }
)


@dataclass(frozen=True)
class ActivityAwareWatchdogViolation:
    category: str
    file_path: str
    line: int
    snippet: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.snippet}"


def _iter_py_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for parent, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if not name.endswith(".py"):
                continue
            result.append(Path(parent) / name)
    result.sort()
    return result


_TEARDOWN_WIRED_NAMES: frozenset[str] = frozenset({"teardown_subtree"})


def _function_wires_teardown(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            name = _func_call_name(node)
            if name is not None and name in _TEARDOWN_WIRED_NAMES:
                return True
        if isinstance(node, ast.Name) and node.id in _TEARDOWN_WIRED_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _TEARDOWN_WIRED_NAMES:
            return True
    return False


_IDLE_WATCHDOG_MARKER: str = "IdleWatchdog("
_DEFAULT_MONITOR_MARKER: str = "DefaultProcessMonitor("
_INVOKE_ONLY_MARKERS: tuple[str, ...] = (
    "set_on_event",
    "self._handle.terminate",
    "AgentInvocationError",
)
_SUBAGENT_COUNTING_SEAM_FILES: frozenset[str] = frozenset(
    {
        "agents/invoke/_process_reader.py",
        "agents/invoke/_pty_line_reader.py",
    }
)
_SUBAGENT_COUNTING_SEAM_FUNCTION: str = "_corroborate"
_SUBAGENT_COUNTING_SEAM_ACCEPTED: tuple[str, ...] = (
    "spawned_subagent_count",
    "live_subagent_count",
)
_SUBAGENT_COUNTING_SEAM_REJECTED: str = "descendant_snapshot"


def _file_needs_parse(rel_path: str, source: str) -> bool:
    if _IDLE_WATCHDOG_MARKER in source or _DEFAULT_MONITOR_MARKER in source:
        return True
    if rel_path in _SUBAGENT_COUNTING_SEAM_FILES:
        return _SUBAGENT_COUNTING_SEAM_FUNCTION in source
    if rel_path.startswith("agents/invoke/"):
        return any(marker in source for marker in _INVOKE_ONLY_MARKERS)
    return False


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


def _func_call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return None


def _first_arg(node: ast.Call) -> ast.expr | None:
    if node.args:
        return node.args[0]
    return None


def _has_kwarg(node: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in node.keywords)


def _format_snippet(source: str, line: int) -> str:
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


class _ModuleVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str, source: str, *, tree: ast.AST | None = None) -> None:
        self.rel_path = rel_path
        self.source = source
        self.violations: list[ActivityAwareWatchdogViolation] = []
        self._has_set_active_sink = False
        self._has_set_subagent_sink = False
        self._function_defs: dict[str, ast.FunctionDef] | None = None
        self._tree = tree

    def _add(self, category: str, line: int) -> None:
        self.violations.append(
            ActivityAwareWatchdogViolation(
                category=category,
                file_path=self.rel_path,
                line=line,
                snippet=_format_snippet(self.source, line),
            )
        )

    def _is_invoke_file(self) -> bool:
        return self.rel_path.startswith("agents/invoke/")

    def visit_Call(self, node: ast.Call) -> None:
        name = _func_call_name(node)

        if name == "IdleWatchdog" and not _has_kwarg(node, "process_monitor"):
            self._add("process_monitor_injection", node.lineno)

        if name == "DefaultProcessMonitor":
            self._check_default_process_monitor(node)

        if self._is_invoke_file():
            if name in {"set_active_sink", "set_subagent_sink"}:
                if name == "set_active_sink":
                    self._has_set_active_sink = True
                else:
                    self._has_set_subagent_sink = True

            if self._is_set_on_event_call(node):
                self._check_set_on_event_binding(node)

        self.generic_visit(node)

    def _check_default_process_monitor(self, node: ast.Call) -> None:
        required = {"role_classifier", "discovery_strategy", "subagent_pid_source"}
        missing = {k for k in required if not _has_kwarg(node, k)}
        if missing:
            self._add("process_monitor_injection_full", node.lineno)
            return

        role_kw = next(kw for kw in node.keywords if kw.arg == "role_classifier")
        if not isinstance(role_kw.value, ast.Call):
            self._add("process_monitor_injection_full", node.lineno)
            return
        role_call_name = _func_call_name(role_kw.value)
        if role_call_name != "role_classifier_for_transport":
            self._add("process_monitor_injection_full", node.lineno)

    def _check_set_on_event_binding(self, node: ast.Call) -> None:
        if self.rel_path in _WORKSPACE_EVENT_BINDING_ALLOWLIST:
            return
        if not node.args:
            return
        bound = node.args[0]
        if self._is_valid_two_arg_forwarder(bound):
            return
        self._add("workspace_event_binding", node.lineno)

    def _is_valid_two_arg_forwarder(self, node: ast.expr) -> bool:
        func_def: ast.FunctionDef | ast.Lambda | None = None
        if isinstance(node, ast.Lambda):
            func_def = node
        elif isinstance(node, ast.Name):
            func_def = self._find_function_def(node.id)
        if func_def is None:
            return False
        args = func_def.args
        if args.vararg or args.kwarg or args.kwonlyargs or args.posonlyargs:
            return False
        if len(args.args) != _EXPECTED_CALLBACK_ARITY:
            return False
        return self._forwards_record_workspace_event(func_def.body)

    def _find_function_def(self, name: str) -> ast.FunctionDef | None:
        if self._function_defs is None:
            tree = self._tree if self._tree is not None else ast.parse(self.source)
            self._function_defs = {
                child.name: child for child in ast.walk(tree) if isinstance(child, ast.FunctionDef)
            }
        return self._function_defs.get(name)

    def _forwards_record_workspace_event(self, body: ast.AST | list[ast.stmt]) -> bool:
        nodes = body if isinstance(body, list) else [body]
        for node in nodes:
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                if not (isinstance(func, ast.Attribute) and func.attr == "record_workspace_event"):
                    continue
                has_kind = any(kw.arg == "kind" for kw in child.keywords)
                has_weight = any(kw.arg == "weight" for kw in child.keywords)
                if has_kind and has_weight:
                    return True
                if len(child.args) >= _MIN_RECORD_ARGS:
                    return True
        return False

    def _is_set_on_event_call(self, node: ast.Call) -> bool:
        func = node.func
        return isinstance(func, ast.Attribute) and func.attr == "set_on_event"

    def _is_self_handle_terminate(self, node: ast.Call) -> bool:
        func = node.func
        if not isinstance(func, ast.Attribute):
            return False
        if func.attr != "terminate":
            return False
        receiver = func.value
        if not isinstance(receiver, ast.Attribute):
            return False
        if receiver.attr != "_handle":
            return False
        inner = receiver.value
        return isinstance(inner, ast.Name) and inner.id == "self"

    def finalize_invoke_file(self) -> None:
        if not self._constructs_idle_watchdog():
            return
        if not self._has_set_active_sink:
            self._add("mcp_tool_sink", 1)
        if not self._has_set_subagent_sink:
            self._add("subagent_sink", 1)
        self._scan_function_bodies_for_teardown()
        self._scan_function_bodies_for_error_path_teardown()

    def _scan_function_bodies_for_teardown(self) -> None:
        tree = self._tree if self._tree is not None else ast.parse(self.source)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            terminate_calls = [
                node
                for node in ast.walk(func)
                if isinstance(node, ast.Call) and self._is_self_handle_terminate(node)
            ]
            if not terminate_calls:
                continue
            if not _function_wires_teardown(func):
                first_line = terminate_calls[0].lineno
                self._add("teardown_subtree", first_line)

    def _scan_function_bodies_for_error_path_teardown(self) -> None:
        tree = self._tree if self._tree is not None else ast.parse(self.source)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            raise_nodes = [
                node
                for node in ast.walk(func)
                if isinstance(node, ast.Raise) and self._is_agent_invocation_error_raise(node)
            ]
            if not raise_nodes:
                continue
            if not _function_wires_teardown(func):
                first_line = raise_nodes[0].lineno
                self._add("error_path_teardown", first_line)

    def _is_agent_invocation_error_raise(self, node: ast.Raise) -> bool:
        exc = node.exc
        if exc is None:
            return False
        if isinstance(exc, ast.Call):
            name = _func_call_name(exc)
            if name is None:
                return False
            return name == "AgentInvocationError" or name.endswith(".AgentInvocationError")
        name = _dotted_name(exc)
        if name is None:
            return False
        return name == "AgentInvocationError" or name.endswith(".AgentInvocationError")

    def _constructs_idle_watchdog(self) -> bool:
        tree = self._tree if self._tree is not None else ast.parse(self.source)
        for child in ast.walk(tree):
            if isinstance(child, ast.Call) and _func_call_name(child) == "IdleWatchdog":
                return True
        return False

    def _collect_forbidden_descendant_snapshot(
        self,
        target: ast.FunctionDef,
    ) -> list[int]:
        forbidden: list[int] = []
        guard_indices = self._find_monitor_guard_arms(target)
        legacy_line_ranges: list[tuple[int, int]] = []
        if guard_indices is not None:
            _if_start, _if_end, else_start, else_end = guard_indices
            if else_start is not None and else_end is not None:
                legacy_line_ranges.append((else_start, else_end))
        for child in ast.walk(target):
            line_raw: object = getattr(child, "lineno", None)
            if not isinstance(line_raw, int):
                continue
            line: int = line_raw
            is_name_match = (
                isinstance(child, ast.Name) and child.id == _SUBAGENT_COUNTING_SEAM_REJECTED
            )
            is_attr_match = (
                isinstance(child, ast.Attribute) and child.attr == _SUBAGENT_COUNTING_SEAM_REJECTED
            )
            if (is_name_match or is_attr_match) and not _line_in_ranges(line, legacy_line_ranges):
                forbidden.append(line)
        return forbidden

    def _find_monitor_guard_arms(
        self,
        target: ast.FunctionDef,
    ) -> tuple[int, int, int | None, int | None] | None:
        for stmt in ast.walk(target):
            if not isinstance(stmt, ast.If):
                continue
            test_src = ast.unparse(stmt.test)
            is_guard = (
                "self._process_monitor is not None" in test_src or "monitor is not None" in test_src
            )
            if not is_guard:
                continue
            body_first = stmt.body[0] if stmt.body else stmt
            body_last = stmt.body[-1] if stmt.body else stmt
            if_start_raw: object = getattr(body_first, "lineno", stmt.lineno)
            if_start: int = if_start_raw if isinstance(if_start_raw, int) else stmt.lineno
            if_end_raw: object = getattr(body_last, "end_lineno", stmt.lineno)
            if_end: int = if_end_raw if isinstance(if_end_raw, int) else stmt.lineno
            if stmt.orelse:
                else_first = stmt.orelse[0]
                else_last = stmt.orelse[-1]
                else_start_raw: object = getattr(else_first, "lineno", None)
                else_end_raw: object = getattr(else_last, "end_lineno", None)
                else_start: int | None = else_start_raw if isinstance(else_start_raw, int) else None
                else_end: int | None = else_end_raw if isinstance(else_end_raw, int) else None
            else:
                else_start = None
                else_end = None
            return (if_start, if_end, else_start, else_end)
        return None

    def check_subagent_counting_seam(self) -> None:
        if self.rel_path not in _SUBAGENT_COUNTING_SEAM_FILES:
            return
        tree = self._tree if self._tree is not None else ast.parse(self.source)
        target: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == _SUBAGENT_COUNTING_SEAM_FUNCTION:
                target = node
                break
        if target is None:
            return
        forbidden_descent_references = self._collect_forbidden_descendant_snapshot(target)
        if forbidden_descent_references:
            line = forbidden_descent_references[0]
            self._add("subagent_counting_seam", line)


def audit_reader_file(path: Path) -> list[ActivityAwareWatchdogViolation]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError):
        return []

    rel_path = f"agents/invoke/{path.name}"
    visitor = _ModuleVisitor(rel_path, source, tree=tree)
    visitor.visit(tree)
    visitor.finalize_invoke_file()
    return visitor.violations


_AUDIT_FINGERPRINT_CACHE: dict[tuple[str, str], list[ActivityAwareWatchdogViolation]] = {}


def _compute_fingerprint(package_root: Path) -> str | None:
    if not package_root.is_dir():
        return None
    hasher = hashlib.sha1()
    for parent, dirs, files in os.walk(package_root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = Path(parent) / name
            try:
                st = path.stat()
                mtime = st.st_mtime_ns
                size = st.st_size
                content = path.read_bytes()
            except OSError:
                continue
            posix = path.as_posix()
            hasher.update(posix.encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(str(size).encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(str(mtime).encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(hashlib.sha1(content).digest())
            hasher.update(b"\n")
    return hasher.hexdigest()


def audit_activity_aware_watchdog(package_root: Path) -> list[ActivityAwareWatchdogViolation]:
    if not package_root.is_dir():
        return []
    resolved_root = str(package_root.resolve())
    fingerprint = _compute_fingerprint(package_root)
    if fingerprint is None:
        return []
    cache_key = (resolved_root, fingerprint)
    cached = _AUDIT_FINGERPRINT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    all_violations: list[ActivityAwareWatchdogViolation] = []
    for py_file in _iter_py_files(package_root):
        rel_path = py_file.relative_to(package_root).as_posix()
        try:
            source = py_file.read_text(encoding="utf-8")
            if not _file_needs_parse(rel_path, source):
                continue
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError):
            continue

        visitor = _ModuleVisitor(rel_path, source, tree=tree)
        visitor.visit(tree)
        if rel_path.startswith("agents/invoke/"):
            visitor.finalize_invoke_file()
        visitor.check_subagent_counting_seam()
        all_violations.extend(visitor.violations)

    _AUDIT_FINGERPRINT_CACHE[cache_key] = all_violations
    return all_violations


def _prewarm_cache_for_production_tree() -> None:
    candidates: list[Path] = []
    module_dir = Path(__file__).resolve().parent
    for parent in (module_dir.parent, module_dir.parent.parent):
        candidate = parent / "ralph"
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        if str(candidate.resolve()) in {key[0] for key in _AUDIT_FINGERPRINT_CACHE}:
            continue
        audit_activity_aware_watchdog(candidate)


_prewarm_cache_for_production_tree()


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    package_root = Path(argv[0]) if argv else Path(__file__).parent.parent

    if not package_root.is_dir():
        print(f"Package root not found: {package_root}", file=sys.stderr)
        return 2

    violations = audit_activity_aware_watchdog(package_root)

    if violations:
        print(f"ACTIVITY-AWARE WATCHDOG CONTRACT VIOLATIONS: {len(violations)}")
        print("=" * 72)
        for v in violations:
            print(f"  {v}")
        print()
        print(
            "Fix the wiring: pass process_monitor= to IdleWatchdog, wire "
            "set_active_sink/set_subagent_sink, bind WorkspaceMonitor.set_on_event "
            "with a 2-arg (kind, weight) forwarding lambda, call teardown_subtree "
            "after self._handle.terminate, call teardown_subtree "
            "before raise AgentInvocationError "
            "on error/crash paths, and construct DefaultProcessMonitor with "
            "role_classifier=role_classifier_for_transport(...), discovery_strategy=, "
            "and subagent_pid_source=. Reader corroborators (_corroborate in "
            "_process_reader.py / _pty_line_reader.py) MUST read "
            "process_monitor.spawned_subagent_count() (or the legacy alias "
            "live_subagent_count()) for scoped_child_active -- the broader "
            "handle.descendant_snapshot() count must NEVER be used for "
            "deferral decisions (R1)."
        )
        return 1

    print("activity-aware watchdog audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
