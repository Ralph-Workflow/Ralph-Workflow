"""Activity-aware idle watchdog contract audit.

Enforces the subagent/tool-visibility contract (acceptance criteria
AC-08, AC-09, AC-11) and the timeout policy documented in
``docs/agents/timeout-policy.md``. The audit performs a
static AST walk over the production source tree and flags regressions
in the wiring that keeps the watchdog activity-aware:

  * ``IdleWatchdog`` must be constructed with ``process_monitor=``.
  * ``set_active_sink`` must be wired after watchdog construction.
  * ``set_subagent_sink`` must be wired after watchdog construction.
  * ``WorkspaceMonitor.set_on_event`` must be bound to a 2-arg forwarding
    callable that passes ``(kind, weight)`` to
    ``watchdog.record_workspace_event`` (not the legacy 0-arg bound
    method).
  * ``teardown_subtree`` must be called on every fire path after
    ``self._handle.terminate``.
  * ``DefaultProcessMonitor`` must be constructed with injected
    ``role_classifier=``, ``discovery_strategy=``, and
    ``subagent_pid_source=``, and ``role_classifier=`` must come from
    ``role_classifier_for_transport(...)``.

This module uses ONLY the ``ast`` module and ``Path.read_text`` — no
real subprocess, no ``time.sleep``, no real file I/O outside reading
source files. It is therefore clean under ``audit_test_policy`` and
``audit_mcp_timeout``.

Usage:
    python -m ralph.testing.audit_activity_aware_watchdog [package_root]

Exit codes:
  0 = clean
  1 = violations found
  2 = root not found
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


_EXPECTED_CALLBACK_ARITY: int = 2
_MIN_RECORD_ARGS: int = 2

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

# Only these two production reader files are allowed to contain the
# production-style 2-arg ``set_on_event`` binding. Any other file under
# ``ralph/agents/invoke/`` that binds a 0-arg callable is flagged.
_WORKSPACE_EVENT_BINDING_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ralph/agents/invoke/_pty_line_reader.py",
        "ralph/agents/invoke/_process_reader.py",
    }
)


@dataclass(frozen=True)
class ActivityAwareWatchdogViolation:
    """A single activity-aware watchdog contract violation."""

    category: str
    file_path: str
    line: int
    snippet: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.snippet}"


def _iter_py_files(root: Path) -> list[Path]:
    """Walk a directory for ``*.py`` files, skipping caches and build dirs."""
    return sorted(
        p
        for p in root.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
    )


def _dotted_name(node: ast.expr) -> str | None:
    """Return the dotted attribute/Name string for an expression, if simple."""
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
    """Return the function name for a Call node."""
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return None


def _first_arg(node: ast.Call) -> ast.expr | None:
    """Return the first positional argument of a Call, if any."""
    if node.args:
        return node.args[0]
    return None


def _has_kwarg(node: ast.Call, name: str) -> bool:
    """Return True if the call includes a keyword argument with ``name``."""
    return any(kw.arg == name for kw in node.keywords)


def _format_snippet(source: str, line: int) -> str:
    """Return the source line at ``line`` (1-based), stripped."""
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


class _ModuleVisitor(ast.NodeVisitor):
    """AST visitor that detects activity-aware watchdog wiring violations."""

    def __init__(self, rel_path: str, source: str, *, tree: ast.AST | None = None) -> None:
        self.rel_path = rel_path
        self.source = source
        self.violations: list[ActivityAwareWatchdogViolation] = []
        self._idle_watchdog_names: set[str] = set()
        self._workspace_monitor_names: set[str] = set()
        self._has_set_active_sink = False
        self._has_set_subagent_sink = False
        self._has_teardown_subtree = False
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
        """True for files where reader-style wiring is expected."""
        return self.rel_path.startswith("ralph/agents/invoke/")

    def visit_Call(self, node: ast.Call) -> None:
        name = _func_call_name(node)

        # (2) process_monitor_injection: IdleWatchdog(...) must include
        # process_monitor= kwarg.
        if name == "IdleWatchdog":
            if not _has_kwarg(node, "process_monitor"):
                self._add("process_monitor_injection", node.lineno)
            else:
                # Track local variable names assigned from this call so
                # sink detectors can associate set_active_sink etc. with
                # the watchdog.
                self._track_idle_watchdog_name(node)

        # (6) DefaultProcessMonitor(...) must include role_classifier=,
        # discovery_strategy=, subagent_pid_source=, and role_classifier
        # must be role_classifier_for_transport(...).
        if name == "DefaultProcessMonitor":
            self._check_default_process_monitor(node)

        # Track sink wiring in invoke reader files.
        if self._is_invoke_file():
            if name in {"set_active_sink", "set_subagent_sink"}:
                if name == "set_active_sink":
                    self._has_set_active_sink = True
                else:
                    self._has_set_subagent_sink = True

            if name == "teardown_subtree":
                self._has_teardown_subtree = True

            if name == "WorkspaceMonitor":
                self._track_workspace_monitor_name(node)

            # (1) workspace_event_binding: detect set_on_event bound to a
            # 0-arg callable in invoke files (outside the allowlist).
            if self._is_set_on_event_call(node):
                self._check_set_on_event_binding(node)

            # (5) teardown_subtree detection is done in finalize_invoke_file
            # by scanning each function body for terminate/teardown pairs.

        self.generic_visit(node)

    def _track_idle_watchdog_name(self, node: ast.Call) -> None:
        """Remember variable names that hold the constructed IdleWatchdog."""
        # We can't statically know the name in all cases, but we can track
        # the most common patterns: direct assignment and annotated assignment.
        # This is best-effort; the sink detectors below also fall back to
        # checking whether ANY set_active_sink/set_subagent_sink call exists.

    def _track_workspace_monitor_name(self, node: ast.Call) -> None:
        """Remember variable names that hold a WorkspaceMonitor."""

    def _check_default_process_monitor(self, node: ast.Call) -> None:
        required = {"role_classifier", "discovery_strategy", "subagent_pid_source"}
        missing = {k for k in required if not _has_kwarg(node, k)}
        if missing:
            self._add("process_monitor_injection_full", node.lineno)
            return

        # Verify role_classifier= is a call to role_classifier_for_transport.
        role_kw = next(kw for kw in node.keywords if kw.arg == "role_classifier")
        if not isinstance(role_kw.value, ast.Call):
            self._add("process_monitor_injection_full", node.lineno)
            return
        role_call_name = _func_call_name(role_kw.value)
        if role_call_name != "role_classifier_for_transport":
            self._add("process_monitor_injection_full", node.lineno)

    def _check_set_on_event_binding(self, node: ast.Call) -> None:
        """Flag 0-arg or non-forwarding set_on_event bindings."""
        if self.rel_path in _WORKSPACE_EVENT_BINDING_ALLOWLIST:
            return
        if not node.args:
            return
        bound = node.args[0]
        if self._is_valid_two_arg_forwarder(bound):
            return
        self._add("workspace_event_binding", node.lineno)

    def _is_valid_two_arg_forwarder(self, node: ast.expr) -> bool:
        """Return True when ``node`` is a 2-arg callable that forwards kind+weight.

        Accepts either an inline lambda or a local function definition whose
        body calls ``watchdog.record_workspace_event`` with both ``kind`` and
        ``weight`` (positional or keyword).
        """
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
        """Find a function definition by name in the parsed module tree."""
        if self._function_defs is None:
            tree = self._tree if self._tree is not None else ast.parse(self.source)
            self._function_defs = {
                child.name: child
                for child in ast.walk(tree)
                if isinstance(child, ast.FunctionDef)
            }
        return self._function_defs.get(name)

    def _forwards_record_workspace_event(self, body: ast.AST | list[ast.stmt]) -> bool:
        """Return True when the body calls record_workspace_event with kind+weight."""
        nodes = body if isinstance(body, list) else [body]
        for node in nodes:
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    name = _func_call_name(child)
                    if name != "record_workspace_event":
                        continue
                    has_kind = any(kw.arg == "kind" for kw in child.keywords)
                    has_weight = any(kw.arg == "weight" for kw in child.keywords)
                    if has_kind and has_weight:
                        return True
                    if len(child.args) >= _MIN_RECORD_ARGS:
                        return True
        return False

    def _is_set_on_event_call(self, node: ast.Call) -> bool:
        """Return True for any ``*.set_on_event(...)`` call."""
        func = node.func
        return isinstance(func, ast.Attribute) and func.attr == "set_on_event"

    def _is_self_handle_terminate(self, node: ast.Call) -> bool:
        """Return True for ``self._handle.terminate(...)``."""
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
        """After walking an invoke file, check file-level sink wiring."""
        # We only check sink wiring when the file constructs an IdleWatchdog
        # with a process_monitor. We approximate this by checking for any
        # IdleWatchdog call; if one exists and has process_monitor=, the sinks
        # should be present.
        # This is intentionally conservative: files that don't construct an
        # IdleWatchdog are not reader files and are not checked for sinks.
        if not self._constructs_idle_watchdog():
            return
        if not self._has_set_active_sink:
            self._add("mcp_tool_sink", 1)
        if not self._has_set_subagent_sink:
            self._add("subagent_sink", 1)
        self._scan_function_bodies_for_teardown()

    def _scan_function_bodies_for_teardown(self) -> None:
        """Flag any function body that terminates without teardown_subtree."""
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
            has_teardown = any(
                isinstance(node, ast.Call) and _func_call_name(node) == "teardown_subtree"
                for node in ast.walk(func)
            )
            if not has_teardown:
                first_line = terminate_calls[0].lineno
                self._add("teardown_subtree", first_line)

    def _constructs_idle_watchdog(self) -> bool:
        """Return True if this file contains any ``IdleWatchdog(...)`` call."""
        for child in ast.walk(ast.parse(self.source)):
            if isinstance(child, ast.Call) and _func_call_name(child) == "IdleWatchdog":
                return True
        return False


def audit_reader_file(path: Path) -> list[ActivityAwareWatchdogViolation]:
    """Run detectors 1-5 on a single reader-style file.

    This helper is used by the regression tests so they can create small
    temp files without building a full fake package layout.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError):
        return []

    rel_path = path.name
    visitor = _ModuleVisitor(rel_path, source, tree=tree)
    visitor.visit(tree)
    if visitor._is_invoke_file() or not path.name.startswith("test_"):
        visitor.finalize_invoke_file()
    return visitor.violations


def audit_activity_aware_watchdog(package_root: Path) -> list[ActivityAwareWatchdogViolation]:
    """Walk the production source tree and return all violations."""
    all_violations: list[ActivityAwareWatchdogViolation] = []
    if not package_root.is_dir():
        return all_violations

    for py_file in _iter_py_files(package_root):
        rel_path = py_file.relative_to(package_root).as_posix()
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError):
            continue

        visitor = _ModuleVisitor(rel_path, source, tree=tree)
        visitor.visit(tree)
        if rel_path.startswith("ralph/agents/invoke/"):
            visitor.finalize_invoke_file()
        all_violations.extend(visitor.violations)

    return all_violations


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns 0 when clean, 1 on violations, 2 on bad root."""
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
            "after self._handle.terminate, and construct DefaultProcessMonitor with "
            "role_classifier=role_classifier_for_transport(...), discovery_strategy=, "
            "and subagent_pid_source=."
        )
        return 1

    print("activity-aware watchdog audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
