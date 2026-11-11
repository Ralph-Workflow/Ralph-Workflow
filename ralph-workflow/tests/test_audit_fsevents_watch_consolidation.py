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
    package_root: Path = tmp_path / "ralph"
    workspace_path: Path = package_root / "agents" / "invoke" / "_workspace.py"
    workspace_path.parent.mkdir(parents=True)
    workspace_path.write_text(workspace_body, encoding="utf-8")
    return package_root


def test_audit_skips_schedule_walk_for_modules_without_a_schedule_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )
    (package_root / "unrelated.py").write_text("value = 1\n", encoding="utf-8")

    monkeypatch.setattr(
        audit,
        "_find_schedule_calls",
        lambda _tree: (_ for _ in ()).throw(AssertionError("unexpected schedule walk")),
    )

    violations = audit._unowned_schedule_violations(package_root)

    assert violations == []


def test_audit_regression_canonical_schedule_check_uses_one_ast_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )
    workspace_path = package_root / "agents" / "invoke" / "_workspace.py"
    source = workspace_path.read_text(encoding="utf-8")

    walks = 0
    original_walk = _ast.walk

    def count_walk(tree: _ast.AST) -> object:
        nonlocal walks
        walks += 1
        return original_walk(tree)

    monkeypatch.setattr(audit.ast, "walk", count_walk)

    violations = audit._check_module(
        workspace_path,
        "agents/invoke/_workspace.py",
        source,
    )

    assert violations == []
    assert walks == 1


def test_audit_fails_closed_for_invalid_workspace_source_without_schedule_marker(
    tmp_path: Path,
) -> None:
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body="class WorkspaceMonitor:\n    def start(self) -> None:\n        (\n",
    )

    violations: list[audit.FseventsWatchViolation] = audit.audit_fsevents_watch_consolidation(
        package_root
    )

    assert [violation.kind for violation in violations] == ["invalid_workspace_module"]


def test_audit_flags_second_schedule_call_in_start(tmp_path: Path) -> None:
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
            "        self._observer.schedule(handler2, workspace_str2, recursive=True)\n"
        ),
    )

    violations: list[audit.FseventsWatchViolation] = audit.audit_fsevents_watch_consolidation(
        package_root
    )

    kinds: set[str] = {v.kind for v in violations}
    assert "multiple_watch_schedule" in kinds, (
        f"expected multiple_watch_schedule violation; got kinds: {sorted(kinds)}"
    )


def test_audit_flags_dynamic_schedule_lookup_without_schedule_text(tmp_path: Path) -> None:
    package_root = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )
    (package_root / "extra_watch.py").write_text(
        "def start(observer, handler, root):\n"
        "    method_name = configured_method_name\n"
        "    getattr(observer, method_name)(handler, root, recursive=True)\n",
        encoding="utf-8",
    )

    violations = audit.audit_fsevents_watch_consolidation(package_root)

    assert [violation.kind for violation in violations] == ["unowned_watch_schedule"]
    assert "dynamic getattr" in violations[0].message


def test_audit_allows_non_watch_dynamic_invocation(tmp_path: Path) -> None:
    package_root = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )
    (package_root / "plugin_dispatch.py").write_text(
        "def run(plugin, method_name):\n    getattr(plugin, method_name)()\n",
        encoding="utf-8",
    )

    assert audit.audit_fsevents_watch_consolidation(package_root) == []


def test_audit_flags_aliased_schedule_call(tmp_path: Path) -> None:
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        schedule = self._observer.schedule\n"
            "        schedule(handler, workspace_str, recursive=True)\n"
        ),
    )

    violations: list[audit.FseventsWatchViolation] = audit.audit_fsevents_watch_consolidation(
        package_root
    )

    assert [violation.kind for violation in violations] == ["aliased_watch_schedule"]
    assert "directly" in violations[0].message


def test_audit_flags_non_recursive_schedule_call(tmp_path: Path) -> None:
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=False)\n"
        ),
    )

    violations: list[audit.FseventsWatchViolation] = audit.audit_fsevents_watch_consolidation(
        package_root
    )

    kinds: set[str] = {v.kind for v in violations}
    assert "watch_not_recursive" in kinds, (
        f"expected watch_not_recursive violation; got kinds: {sorted(kinds)}"
    )


def test_audit_flags_schedule_call_relocated_into_record_event(tmp_path: Path) -> None:
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

    violations: list[audit.FseventsWatchViolation] = audit.audit_fsevents_watch_consolidation(
        package_root
    )

    kinds: set[str] = {v.kind for v in violations}
    assert "dynamic_watch_schedule" in kinds, (
        f"expected dynamic_watch_schedule violation for record_event"
        f" relocation; got kinds: {sorted(kinds)}"
    )


def test_audit_flags_schedule_call_inside_for_loop_in_start(tmp_path: Path) -> None:
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        for _ in range(1):\n"
            "            self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )

    violations: list[audit.FseventsWatchViolation] = audit.audit_fsevents_watch_consolidation(
        package_root
    )

    kinds: set[str] = {v.kind for v in violations}
    assert "dynamic_watch_schedule" in kinds, (
        f"expected dynamic_watch_schedule violation for for-loop-inside-start;"
        f" got kinds: {sorted(kinds)}"
    )


def test_audit_flags_getattr_schedule_call_in_unknown_production_module(tmp_path: Path) -> None:
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )
    extra_module = package_root / "pipeline" / "extra_watch.py"
    extra_module.parent.mkdir(parents=True)
    extra_module.write_text(
        "def start(observer, handler, root):\n"
        "    getattr(observer, 'schedule')(handler, root, recursive=True)\n",
        encoding="utf-8",
    )

    violations = audit.audit_fsevents_watch_consolidation(package_root)

    assert len(violations) == 1
    assert violations[0].kind == "unowned_watch_schedule"
    assert violations[0].file_path == "pipeline/extra_watch.py"
    assert "WorkspaceMonitor.start" in violations[0].message


def test_audit_rejects_unrelated_canonical_schedule_receiver(tmp_path: Path) -> None:
    package_root = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        scheduler.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )

    violations = audit.audit_fsevents_watch_consolidation(package_root)

    assert [violation.kind for violation in violations] == ["invalid_watch_schedule_receiver"]
    assert "self._observer.schedule" in violations[0].message


def test_audit_flags_observer_construction_in_unknown_production_module(tmp_path: Path) -> None:
    package_root = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )
    extra_module = package_root / "pipeline" / "extra_observer.py"
    extra_module.parent.mkdir(parents=True)
    extra_module.write_text(
        "from watchdog.observers import Observer\n\ndef start() -> None:\n    Observer()\n",
        encoding="utf-8",
    )

    violations = audit.audit_fsevents_watch_consolidation(package_root)

    assert [violation.kind for violation in violations] == ["unowned_watch_observer"]
    assert violations[0].file_path == "pipeline/extra_observer.py"
    assert "WorkspaceMonitor" in violations[0].message


def test_audit_flags_schedule_call_in_unknown_production_module(tmp_path: Path) -> None:
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )
    extra_module = package_root / "pipeline" / "extra_watch.py"
    extra_module.parent.mkdir(parents=True)
    extra_module.write_text(
        "def start(observer, handler, root):\n"
        "    observer.schedule(handler, root, recursive=True)\n",
        encoding="utf-8",
    )

    violations: list[audit.FseventsWatchViolation] = audit.audit_fsevents_watch_consolidation(
        package_root
    )

    assert len(violations) == 1
    assert violations[0].kind == "unowned_watch_schedule"
    assert violations[0].file_path == "pipeline/extra_watch.py"
    assert "WorkspaceMonitor.start" in violations[0].message


def test_audit_flags_schedule_call_in_testing_module(tmp_path: Path) -> None:
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )
    extra_module = package_root / "testing" / "extra_watch.py"
    extra_module.parent.mkdir(parents=True)
    extra_module.write_text(
        "def start(observer, handler, root):\n"
        "    observer.schedule(handler, root, recursive=True)\n",
        encoding="utf-8",
    )

    violations: list[audit.FseventsWatchViolation] = audit.audit_fsevents_watch_consolidation(
        package_root
    )

    assert [violation.kind for violation in violations] == ["unowned_watch_schedule"]
    assert violations[0].file_path == "testing/extra_watch.py"


def test_audit_flags_invalid_workspace_module(tmp_path: Path) -> None:
    package_root: Path = _write_fake_package(
        tmp_path,
        workspace_body="class WorkspaceMonitor:\n    def start(self) -> None:\n",
    )

    violations = audit.audit_fsevents_watch_consolidation(package_root)

    assert len(violations) == 1
    assert violations[0].kind == "invalid_workspace_module"
    assert violations[0].file_path == "agents/invoke/_workspace.py"
    assert "fail closed" in violations[0].message


def test_audit_fails_closed_for_missing_package_root(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"

    violations = audit.audit_fsevents_watch_consolidation(missing_root)

    assert [violation.kind for violation in violations] == ["missing_package_root"]
    assert "fail closed" in violations[0].message


def test_audit_fails_closed_for_invalid_non_owner_module_without_schedule_marker(
    tmp_path: Path,
) -> None:
    package_root = _write_fake_package(
        tmp_path,
        workspace_body=(
            "class WorkspaceMonitor:\n"
            "    def start(self) -> None:\n"
            "        self._observer.schedule(handler, workspace_str, recursive=True)\n"
        ),
    )
    invalid_module = package_root / "pipeline" / "invalid.py"
    invalid_module.parent.mkdir(parents=True)
    invalid_module.write_text("def broken(:\n", encoding="utf-8")

    violations = audit.audit_fsevents_watch_consolidation(package_root)

    assert [violation.kind for violation in violations] == ["invalid_production_module"]
    assert violations[0].file_path == "pipeline/invalid.py"
    assert "fail closed" in violations[0].message


def test_audit_flags_missing_workspace_module(tmp_path: Path) -> None:
    package_root: Path = tmp_path / "ralph"
    package_root.mkdir(parents=True)

    violations: list[audit.FseventsWatchViolation] = audit.audit_fsevents_watch_consolidation(
        package_root
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
    audit_path: Path = REPO_ROOT / "ralph" / "testing" / "audit_fsevents_watch_consolidation.py"
    source: str = audit_path.read_text(encoding="utf-8")
    return _ast.parse(source, filename=str(audit_path))


def test_audit_module_imports_clean() -> None:
    audit_path: Path = REPO_ROOT / "ralph" / "testing" / "audit_fsevents_watch_consolidation.py"
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
    audit_path: Path = REPO_ROOT / "ralph" / "testing" / "audit_fsevents_watch_consolidation.py"
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
