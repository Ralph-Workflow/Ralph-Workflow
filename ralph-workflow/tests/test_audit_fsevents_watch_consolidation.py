"""Regression tests pinning the fsevents watch-consolidation audit contract.

The fsevents watch-consolidation audit
(``ralph.testing.audit_fsevents_watch_consolidation``) is the
gate that locks the single-recursive-root-watch consolidation in
``ralph/agents/invoke/_workspace.py`` so a future refactor cannot
silently re-introduce drift. It locks four invariants:

  * Exactly one ``observer.schedule(...)`` call exists in the
    workspace-monitor module (INV-1).
  * The single schedule call passes ``recursive=True`` (INV-2).
  * The schedule call sits statically inside
    ``WorkspaceMonitor.start()`` -- no ``for``/``while`` ancestor
    and the nearest enclosing function is named ``start``
    (INV-3).
  * The target module exists at the canonical location
    ``agents/invoke/_workspace.py`` under the package root
    (INV-4).

These tests write forbidden-construct source files under pytest's
``tmp_path`` fixture and run the audit directly against the temp
``package_root``. No real subprocess, no ``time.sleep``, no real
file I/O outside ``tmp_path``.
"""

from __future__ import annotations

import ast as _ast
import functools
from pathlib import Path

import pytest

from ralph.testing import audit_fsevents_watch_consolidation as audit

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = REPO_ROOT / "ralph"


def _write_fake_package(
    tmp_path: Path,
    *,
    workspace_body: str,
) -> Path:
    """Write a fake package containing ``agents/invoke/_workspace.py``.

    Args:
        tmp_path: The pytest ``tmp_path`` fixture; the fake package
            tree is rooted at ``tmp_path / "ralph"``.
        workspace_body: The full source body of the fake
            ``_workspace.py``.  Tests pass mutated ``start()`` or
            ``record_event()`` bodies through this parameter so the
            fixtures differ only in the mutation site.

    Returns:
        The path of the fake ``package_root`` (the audit walks this
        directory).
    """
    package_root: Path = tmp_path / "ralph"
    workspace_path: Path = package_root / "agents" / "invoke" / "_workspace.py"
    workspace_path.parent.mkdir(parents=True)
    workspace_path.write_text(workspace_body, encoding="utf-8")
    return package_root


def test_audit_passes_real_production_tree() -> None:
    """The audit returns zero violations against the committed ralph/ tree.

    Proves the current tree -- exactly one recursive root watch
    scheduled statically inside ``WorkspaceMonitor.start()`` with
    no loop ancestor -- satisfies the invariants.
    """
    violations: list[audit.FseventsWatchViolation] = (
        audit.audit_fsevents_watch_consolidation(PRODUCTION_ROOT)
    )
    assert violations == [], (
        f"audit must be clean on the real ralph-workflow tree; got: {violations}"
    )


def test_audit_flags_second_schedule_call_in_start(tmp_path: Path) -> None:
    """A second ``observer.schedule(...)`` call inside ``start()`` triggers ``multiple_watch_schedule``.

    Two overlapping watchdog watches inflate the fseventsd
    footprint (each one is OS-recursive); the audit forbids the
    duplicate.
    """
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
            "        self._observer.schedule(handler2, workspace_str2, recursive=True)\n"
        ),
    )

    violations: list[audit.FseventsWatchViolation] = (
        audit.audit_fsevents_watch_consolidation(package_root)
    )

    kinds: set[str] = {v.kind for v in violations}
    assert "multiple_watch_schedule" in kinds, (
        f"expected multiple_watch_schedule violation; got kinds: {sorted(kinds)}"
    )


def test_audit_flags_non_recursive_schedule_call(tmp_path: Path) -> None:
    """``recursive=False`` triggers ``watch_not_recursive``.

    The fsevents backend is OS-recursive, so non-recursive
    subscriptions would multiply overlapping streams.  The audit
    requires the literal ``True`` constant.
    """
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=False)\n"
        ),
    )

    violations: list[audit.FseventsWatchViolation] = (
        audit.audit_fsevents_watch_consolidation(package_root)
    )

    kinds: set[str] = {v.kind for v in violations}
    assert "watch_not_recursive" in kinds, (
        f"expected watch_not_recursive violation; got kinds: {sorted(kinds)}"
    )


def test_audit_flags_schedule_call_relocated_into_record_event(tmp_path: Path) -> None:
    """A schedule call moved into ``record_event`` triggers ``dynamic_watch_schedule``.

    The nearest enclosing function is ``record_event``, not
    ``start``, and there is no loop ancestor -- this is the
    function-relocation case.  The audit must flag it because the
    production invariant requires the watch to be scheduled
    statically inside ``start()``, never on a per-event path.
    """
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        pass\n"
            "    def record_event(self, src_path: str) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )

    violations: list[audit.FseventsWatchViolation] = (
        audit.audit_fsevents_watch_consolidation(package_root)
    )

    kinds: set[str] = {v.kind for v in violations}
    assert "dynamic_watch_schedule" in kinds, (
        f"expected dynamic_watch_schedule violation for record_event"
        f" relocation; got kinds: {sorted(kinds)}"
    )


def test_audit_flags_schedule_call_inside_for_loop_in_start(tmp_path: Path) -> None:
    """A schedule call wrapped in a ``for`` loop inside ``start()`` triggers ``dynamic_watch_schedule``.

    The nearest enclosing function is STILL ``start`` (the
    FunctionDef line-range containment check would let this pass),
    but the loop ancestor means the watch would be re-scheduled
    on every iteration -- the exact per-iteration inflation case
    the ancestor-walk check exists to catch.  This is a SEPARATE
    fixture from the record_event relocation test to prove the
    loop-ancestor detector fires independently of the
    function-name detector.
    """
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        for _ in range(1):\n"
            "            self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )

    violations: list[audit.FseventsWatchViolation] = (
        audit.audit_fsevents_watch_consolidation(package_root)
    )

    kinds: set[str] = {v.kind for v in violations}
    assert "dynamic_watch_schedule" in kinds, (
        f"expected dynamic_watch_schedule violation for for-loop-inside-start;"
        f" got kinds: {sorted(kinds)}"
    )


def test_audit_flags_missing_workspace_module(tmp_path: Path) -> None:
    """A package root WITHOUT ``agents/invoke/_workspace.py`` triggers ``missing_workspace_module``.

    The file's absence is itself drift -- the audit must NOT
    silently pass when the canonical module is gone.
    """
    package_root: Path = tmp_path / "ralph"
    package_root.mkdir(parents=True)
    # Intentionally do NOT write agents/invoke/_workspace.py.

    violations: list[audit.FseventsWatchViolation] = (
        audit.audit_fsevents_watch_consolidation(package_root)
    )

    kinds: set[str] = {v.kind for v in violations}
    assert "missing_workspace_module" in kinds, (
        f"expected missing_workspace_module violation; got kinds: {sorted(kinds)}"
    )


def _dotted(node: _ast.AST) -> str | None:
    if isinstance(node, _ast.Name):
        return node.id
    if isinstance(node, _ast.Attribute):
        base: str | None = _dotted(node.value)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    return None


@functools.cache
def _parse_audit_module() -> _ast.Module:
    audit_path: Path = (
        REPO_ROOT / "ralph" / "testing" / "audit_fsevents_watch_consolidation.py"
    )
    source: str = audit_path.read_text(encoding="utf-8")
    return _ast.parse(source, filename=str(audit_path))


def test_audit_module_imports_clean() -> None:
    """The audit must NOT use ``time.sleep``, ``asyncio.sleep``,
    ``subprocess.run``, ``httpx.*``, ``requests.*``,
    ``urllib.request.urlopen``, or ``socket.create_connection``
    (the test-policy and mcp-timeout invariants).  The audit is
    purely an AST walk over local files.

    The check uses AST-based detection (not regex) so the literal
    strings in the test source do not produce false positives.
    """
    audit_path: Path = (
        REPO_ROOT / "ralph" / "testing" / "audit_fsevents_watch_consolidation.py"
    )
    source: str = audit_path.read_text(encoding="utf-8")
    tree: _ast.Module = _ast.parse(source, filename=str(audit_path))

    forbidden_calls: dict[str, list[int]] = {
        "time.sleep": [],
        "asyncio.sleep": [],
        "subprocess.run": [],
        "subprocess.Popen": [],
        "subprocess.call": [],
        "subprocess.check_output": [],
        "urllib.request.urlopen": [],
        "socket.create_connection": [],
    }
    forbidden_attrs: dict[str, list[int]] = {
        "httpx": [],
        "requests": [],
    }

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            name: str | None = _dotted(node.func)
            if name is None:
                continue
            if name in forbidden_calls:
                forbidden_calls[name].append(node.lineno)
        elif isinstance(node, _ast.Attribute):
            value_name: str | None = _dotted(node.value)
            if value_name in forbidden_attrs:
                forbidden_attrs[value_name].append(node.lineno)

    all_violations: list[str] = []
    for name, lines in forbidden_calls.items():
        all_violations.extend(f"{audit_path.name}:{lineno}: call to {name}" for lineno in lines)
    for name, lines in forbidden_attrs.items():
        all_violations.extend(
            f"{audit_path.name}:{lineno}: attribute access on {name}" for lineno in lines
        )
    assert not all_violations, f"audit module uses forbidden I/O primitives: {all_violations}"


@pytest.mark.subprocess_e2e
def test_audit_module_main_function_returns_zero_on_clean_tree() -> None:
    """Run the audit's ``main()`` in-process and assert exit 0 on the real tree.

    Black-box proof that the audit works as a wired verify step.
    Calling ``main()`` directly (instead of via ``subprocess``)
    keeps the test well under the per-test timeout while still
    validating the CLI entry-point contract.
    """
    rc: int = audit.main([])
    assert rc == 0, f"audit main() must return 0 on clean tree; got {rc}"


@pytest.mark.parametrize(
    "forbidden_name",
    [
        "time.sleep",
        "asyncio.sleep",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_output",
        "urllib.request.urlopen",
        "socket.create_connection",
        "httpx.get",
        "requests.get",
    ],
)
def test_audit_module_forbids_known_io_primitives(forbidden_name: str) -> None:
    """The audit must not be modified to introduce any of the known
    I/O primitives.  This locks the invariant that the audit is a
    pure static walker and never a runtime probe.

    Uses AST-based detection (not regex) so the literal strings in
    the test source do not produce false positives.
    """
    audit_path: Path = (
        REPO_ROOT / "ralph" / "testing" / "audit_fsevents_watch_consolidation.py"
    )
    tree: _ast.Module = _parse_audit_module()

    parts: list[str] = forbidden_name.split(".")

    def _matches(node: _ast.AST) -> bool:
        if isinstance(node, _ast.Name):
            return parts == [node.id]
        if isinstance(node, _ast.Attribute):
            inner: bool = _matches(node.value)
            if not inner:
                return False
            return parts[-1] == node.attr and len(parts) >= 2
        return False

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call) and _matches(node.func):
            raise AssertionError(
                f"audit module contains forbidden call to {forbidden_name}"
                f" at {audit_path.name}:{node.lineno}"
            )
