from __future__ import annotations

import ast as _ast
import functools
import tempfile
from pathlib import Path

import pytest

from ralph.testing import audit_watchdog_drift as audit

REPO_ROOT = Path(__file__).resolve().parents[1]


@functools.cache
def _parse_audit_module() -> _ast.Module:
    audit_path = REPO_ROOT / "ralph" / "testing" / "audit_watchdog_drift.py"
    source = audit_path.read_text(encoding="utf-8")
    return _ast.parse(source, filename=str(audit_path))


_LEGACY_BASENAME: str = (
    audit._LEGACY_BASENAME_FRAGMENT_A
    + audit._LEGACY_BASENAME_SEPARATOR
    + audit._LEGACY_BASENAME_FRAGMENT_B
    + audit._LEGACY_BASENAME_EXTENSION
)


def _write_fake_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "ralph-workflow"
    package_root = repo_root / "ralph"
    (package_root / "agents" / "idle_watchdog").mkdir(parents=True)
    return package_root, repo_root


def _make_legacy_root_watchdog(repo_root: Path) -> None:
    (repo_root / _LEGACY_BASENAME).write_text(
        "def legacy_main():\n    return 'should not exist'\n",
        encoding="utf-8",
    )


def _make_duplicate_idle_watchdog(package_root: Path) -> Path:
    path = package_root / "agents" / "some_other_module.py"
    path.write_text(
        "class IdleWatchdog:\n    pass\n",
        encoding="utf-8",
    )
    return path


def _make_duplicate_post_exit_watchdog(package_root: Path) -> Path:
    path = package_root / "agents" / "another_module.py"
    path.write_text(
        "class PostExitWatchdog:\n    pass\n",
        encoding="utf-8",
    )
    return path


def _make_fire_reason_outside_owner(package_root: Path) -> Path:
    path = package_root / "agents" / "third_module.py"
    path.write_text(
        "from ralph.agents.idle_watchdog import WatchdogFireReason\n"
        "\n"
        "def bad_construction():\n"
        "    return WatchdogFireReason('no_output_deadline')\n",
        encoding="utf-8",
    )
    return path


def _make_fire_reason_attribute_call_outside_owner(package_root: Path) -> Path:
    path = package_root / "agents" / "fourth_module.py"
    path.write_text(
        "from ralph.agents.idle_watchdog import WatchdogFireReason\n"
        "\n"
        "def bad_attribute_construction():\n"
        "    return WatchdogFireReason.NO_OUTPUT_DEADLINE()\n",
        encoding="utf-8",
    )
    return path


def test_audit_flags_legacy_root_watchdog() -> None:

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        _make_legacy_root_watchdog(repo_root)

        violations = audit.audit_watchdog_drift(package_root, repo_root=repo_root)

        kinds = {v.kind for v in violations}
        assert "legacy_root_watchdog" in kinds, (
            f"expected legacy_root_watchdog violation, got kinds: {sorted(kinds)}"
        )


def test_audit_flags_duplicate_idle_watchdog_class() -> None:

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        bad_path = _make_duplicate_idle_watchdog(package_root)

        violations = audit.audit_watchdog_drift(package_root, repo_root=repo_root)

        matches = [v for v in violations if v.kind == "duplicate_idle_watchdog"]
        assert matches, f"expected at least one duplicate_idle_watchdog violation; got {violations}"
        rel = bad_path.relative_to(package_root).as_posix()
        assert any(v.file_path == rel for v in matches), (
            f"expected violation at {rel}, got: {[(v.kind, v.file_path) for v in matches]}"
        )


def test_audit_flags_duplicate_post_exit_watchdog_class() -> None:

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        bad_path = _make_duplicate_post_exit_watchdog(package_root)

        violations = audit.audit_watchdog_drift(package_root, repo_root=repo_root)

        matches = [v for v in violations if v.kind == "duplicate_post_exit_watchdog"]
        assert matches, (
            f"expected at least one duplicate_post_exit_watchdog violation; got {violations}"
        )
        rel = bad_path.relative_to(package_root).as_posix()
        assert any(v.file_path == rel for v in matches), (
            f"expected violation at {rel}, got: {[(v.kind, v.file_path) for v in matches]}"
        )


def test_audit_flags_watchdog_fire_reason_outside_canonical_owners() -> None:

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        bad_path = _make_fire_reason_outside_owner(package_root)

        violations = audit.audit_watchdog_drift(package_root, repo_root=repo_root)

        matches = [v for v in violations if v.kind == "fire_reason_outside_canonical_owner"]
        assert matches, (
            f"expected at least one fire_reason_outside_canonical_owner violation; got {violations}"
        )
        rel = bad_path.relative_to(package_root).as_posix()
        assert any(v.file_path == rel for v in matches), (
            f"expected violation at {rel}, got: {[(v.kind, v.file_path) for v in matches]}"
        )


def test_audit_flags_attribute_call_watchdog_fire_reason_outside_owner() -> None:

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        bad_path = _make_fire_reason_attribute_call_outside_owner(package_root)

        violations = audit.audit_watchdog_drift(package_root, repo_root=repo_root)

        matches = [v for v in violations if v.kind == "fire_reason_outside_canonical_owner"]
        assert matches, (
            f"expected at least one fire_reason_outside_canonical_owner violation; got {violations}"
        )
        rel = bad_path.relative_to(package_root).as_posix()
        assert any(v.file_path == rel for v in matches), (
            f"expected violation at {rel}, got: {[(v.kind, v.file_path) for v in matches]}"
        )


def test_audit_does_not_flag_class_idle_watchdog_subclass() -> None:

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        (package_root / "agents" / "subclass_module.py").write_text(
            "class IdleWatchdogSubclass:\n    pass\n",
            encoding="utf-8",
        )

        violations = audit.audit_watchdog_drift(package_root, repo_root=repo_root)

        kinds = {v.kind for v in violations}
        assert "duplicate_idle_watchdog" not in kinds, (
            f"audit must not flag IdleWatchdogSubclass; got kinds: {sorted(kinds)}"
        )


def test_audit_does_not_flag_comparison_reference_to_watchdog_fire_reason() -> None:

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        (package_root / "agents" / "comparison_module.py").write_text(
            "from ralph.agents.idle_watchdog import WatchdogFireReason\n"
            "\n"
            "def compare(reason: WatchdogFireReason) -> bool:\n"
            "    return reason == WatchdogFireReason.NO_OUTPUT_DEADLINE\n",
            encoding="utf-8",
        )

        violations = audit.audit_watchdog_drift(package_root, repo_root=repo_root)

        kinds = {v.kind for v in violations}
        assert "fire_reason_outside_canonical_owner" not in kinds, (
            f"audit must not flag comparison reference; got kinds: {sorted(kinds)}"
        )


def test_audit_flags_subagent_counting_outside_owner() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        (package_root / "agents" / "duplicate_subagent_identity.py").write_text(
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class SubagentIdentity:\n"
            "    pid: int\n"
            "    source: str\n",
            encoding="utf-8",
        )

        violations = audit.audit_watchdog_drift(package_root, repo_root=repo_root)

        kinds = {v.kind for v in violations}
        assert "subagent_counting_outside_owner" in kinds, (
            f"audit must flag duplicate SubagentIdentity; got kinds: {sorted(kinds)}"
        )


def test_audit_does_not_flag_canonical_owner_subagent_counting() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        (package_root / "agents" / "idle_watchdog" / "_subagent_identity.py").write_text(
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class SubagentIdentity:\n"
            "    pid: int\n"
            "\n"
            "class SubagentPidRegistry:\n"
            "    pass\n",
            encoding="utf-8",
        )

        violations = audit.audit_watchdog_drift(package_root, repo_root=repo_root)

        kinds = {v.kind for v in violations}
        assert "subagent_counting_outside_owner" not in kinds, (
            f"audit must not flag canonical owner; got kinds: {sorted(kinds)}"
        )


def test_audit_module_imports_clean() -> None:

    audit_path = REPO_ROOT / "ralph" / "testing" / "audit_watchdog_drift.py"
    source = audit_path.read_text(encoding="utf-8")
    tree = _ast.parse(source, filename=str(audit_path))

    forbidden_calls: dict[str, list[tuple[str, int]]] = {
        "time.sleep": [],
        "asyncio.sleep": [],
        "subprocess.run": [],
        "subprocess.Popen": [],
        "subprocess.call": [],
        "subprocess.check_output": [],
        "urllib.request.urlopen": [],
        "socket.create_connection": [],
    }
    forbidden_attrs: dict[str, list[tuple[str, int]]] = {
        "httpx": [],
        "requests": [],
    }

    def _dotted(node: _ast.AST) -> str | None:
        if isinstance(node, _ast.Name):
            return node.id
        if isinstance(node, _ast.Attribute):
            base = _dotted(node.value)
            if base is None:
                return None
            return f"{base}.{node.attr}"
        return None

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            name = _dotted(node.func)
            if name is None:
                continue
            if name in forbidden_calls:
                forbidden_calls[name].append((audit_path.name, node.lineno))
        elif isinstance(node, _ast.Attribute):
            value_name = _dotted(node.value)
            if value_name in forbidden_attrs:
                forbidden_attrs[value_name].append((audit_path.name, node.lineno))

    all_violations: list[str] = []
    for name, hits in forbidden_calls.items():
        for path, lineno in hits:
            all_violations.append(f"{path}:{lineno}: call to {name}")
    for name, hits in forbidden_attrs.items():
        for path, lineno in hits:
            all_violations.append(f"{path}:{lineno}: attribute access on {name}")
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

    audit_path = REPO_ROOT / "ralph" / "testing" / "audit_watchdog_drift.py"
    tree = _parse_audit_module()

    parts = forbidden_name.split(".")

    def _matches(node: _ast.AST) -> bool:
        if isinstance(node, _ast.Name):
            return parts == [node.id]
        if isinstance(node, _ast.Attribute):
            inner = _matches(node.value)
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
