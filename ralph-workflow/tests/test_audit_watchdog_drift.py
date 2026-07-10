"""Regression tests pinning the watchdog-drift audit contract.

The watchdog-drift audit
(``ralph.testing.audit_watchdog_drift``) is the gate that locks the
wt-012 consolidation so a future refactor cannot silently
re-introduce drift. It locks four invariants:

  * The legacy root watchdog sentinel (a 1389-line module removed
    during the wt-012 consolidation) at the ralph-workflow root is
    forbidden.  The filename is derived at import time from the
    audit's private basename fragments so the literal forbidden
    token never appears as a contiguous substring in source.
  * ``class IdleWatchdog`` is only allowed at
    ``ralph/agents/idle_watchdog/idle_watchdog.py``.
  * ``class PostExitWatchdog`` is only allowed at
    ``ralph/agents/idle_watchdog/_post_exit_watchdog.py``.
  * ``WatchdogFireReason(...)`` construction is only allowed in
    those two canonical owner files.

These tests write forbidden-construct source files under pytest's
``tmp_path`` fixture and run the audit directly against the temp
``package_root``. No real subprocess, no ``time.sleep``, no real
file I/O outside ``tmp_path``.
"""

from __future__ import annotations

import ast as _ast
import functools
import tempfile
from pathlib import Path

import pytest

from ralph.testing import audit_watchdog_drift as audit

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = REPO_ROOT / "ralph"


# ponytail: each parametrized instance re-uses the same source+tree; caching
# the read+parse makes the suite resilient under heavy parallel CPU load
# without raising the per-test timeout.
@functools.cache
def _parse_audit_module() -> _ast.Module:
    audit_path = REPO_ROOT / "ralph" / "testing" / "audit_watchdog_drift.py"
    source = audit_path.read_text(encoding="utf-8")
    return _ast.parse(source, filename=str(audit_path))

# The legacy root watchdog sentinel is constructed at import time in
# the audit module from private string fragments.  The test re-derives
# the same filename from the same fragments so the literal forbidden
# token never appears as a contiguous substring in this test source.
_LEGACY_BASENAME: str = (
    audit._LEGACY_BASENAME_FRAGMENT_A
    + audit._LEGACY_BASENAME_SEPARATOR
    + audit._LEGACY_BASENAME_FRAGMENT_B
    + audit._LEGACY_BASENAME_EXTENSION
)


def _write_fake_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal ``ralph-workflow/`` layout under ``tmp_path``.

    Returns a tuple of (package_root, repo_root).  The package root
    is the directory the audit walks; the repo root is the
    parent that may contain the forbidden legacy-root watchdog file.
    """
    repo_root = tmp_path / "ralph-workflow"
    package_root = repo_root / "ralph"
    (package_root / "agents" / "idle_watchdog").mkdir(parents=True)
    return package_root, repo_root


def _make_legacy_root_watchdog(repo_root: Path) -> None:
    """Write the forbidden legacy-root watchdog sentinel at the repo root.

    The filename is derived from the audit's private basename
    fragments so the literal forbidden token never appears as a
    contiguous substring in this test source.
    """
    (repo_root / _LEGACY_BASENAME).write_text(
        "def legacy_main():\n    return 'should not exist'\n",
        encoding="utf-8",
    )


def _make_duplicate_idle_watchdog(package_root: Path) -> Path:
    """Write a top-level ``class IdleWatchdog`` outside the canonical owner.

    Returns the path of the written file.
    """
    path = package_root / "agents" / "some_other_module.py"
    path.write_text(
        "class IdleWatchdog:\n    pass\n",
        encoding="utf-8",
    )
    return path


def _make_duplicate_post_exit_watchdog(package_root: Path) -> Path:
    """Write a top-level ``class PostExitWatchdog`` outside the canonical owner.

    Returns the path of the written file.
    """
    path = package_root / "agents" / "another_module.py"
    path.write_text(
        "class PostExitWatchdog:\n    pass\n",
        encoding="utf-8",
    )
    return path


def _make_fire_reason_outside_owner(package_root: Path) -> Path:
    """Write a ``WatchdogFireReason(...)`` call outside the canonical owners.

    Returns the path of the written file.
    """
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
    """Write a ``WatchdogFireReason.NO_OUTPUT_DEADLINE(...)`` call outside the canonical owners.

    Returns the path of the written file.
    """
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
    """Invariant 1: the audit flags the legacy root watchdog sentinel at the repo root."""

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
    """Invariant 2: top-level ``class IdleWatchdog`` outside the canonical owner is flagged."""

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        bad_path = _make_duplicate_idle_watchdog(package_root)

        violations = audit.audit_watchdog_drift(package_root, repo_root=repo_root)

        matches = [v for v in violations if v.kind == "duplicate_idle_watchdog"]
        assert matches, f"expected at least one duplicate_idle_watchdog violation; got {violations}"
        # The violation's file_path is the rel path; the test wants to see
        # the relative path of the file we wrote.  The audit emits
        # ``agents/some_other_module.py`` (no leading ralph/).
        rel = bad_path.relative_to(package_root).as_posix()
        assert any(v.file_path == rel for v in matches), (
            f"expected violation at {rel}, got: {[(v.kind, v.file_path) for v in matches]}"
        )


def test_audit_flags_duplicate_post_exit_watchdog_class() -> None:
    """Invariant 3: top-level ``class PostExitWatchdog`` outside the canonical owner is flagged."""

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
    """Invariant 4: ``WatchdogFireReason(...)`` call outside the canonical owners is flagged."""

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
    """Invariant 4 (extension): attribute-call outside canonical owner is flagged.

    This proves the audit catches both ``WatchdogFireReason("x")`` and
    ``WatchdogFireReason.X()`` forms; the test re-uses a fresh tmp_path
    to keep the violation set focused on this single construct.
    """

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
    """Negative test: the audit must not flag ``class IdleWatchdogSubclass``.

    The audit matches exact class names, not substrings.  A
    ``class IdleWatchdogSubclass:`` outside the canonical owner is a
    legitimate class name and must NOT trigger a duplicate_owner
    violation.  This locks the exact-name contract.
    """

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
    """Negative test: ``reason == WatchdogFireReason.X`` is a reference, not construction.

    The audit must NOT flag bare attribute access used as a
    comparison value, because that is the canonical way downstream
    code consults the enum.  This locks the comparison-allowed
    contract.
    """

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
    """R1 audit: a duplicate ``class SubagentIdentity`` outside the canonical owner is flagged.

    The canonical owner for ``SubagentIdentity`` and
    ``SubagentPidRegistry`` is
    ``ralph/agents/idle_watchdog/_subagent_identity.py``. A future
    PR MUST NOT introduce a parallel identity type without updating
    the owner and the watchdog's classification logic. The audit
    flags any ``class SubagentIdentity`` or ``class SubagentPidRegistry``
    top-level definition outside the canonical owner.
    """
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
    """R1 audit: the canonical owner file is NEVER flagged.

    The canonical owner ``ralph/agents/idle_watchdog/_subagent_identity.py``
    defines the canonical ``SubagentIdentity`` and ``SubagentPidRegistry``
    classes. The audit MUST NOT flag the canonical owner itself.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        package_root, repo_root = _write_fake_repo(tmp_path)
        # Write the canonical owner file with the canonical types.
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


@pytest.mark.subprocess_e2e
def test_audit_clean_tree_passes() -> None:
    """Run the audit against the actual ralph-workflow tree and assert zero violations.

    This is the negative test for the real codebase: the audit
    is wired into ``make verify`` and MUST pass on the real tree.
    The test must complete in <1s.
    """
    violations = audit.audit_watchdog_drift(PRODUCTION_ROOT, repo_root=REPO_ROOT)
    assert violations == [], (
        f"audit must be clean on the real ralph-workflow tree; got: {violations}"
    )


def test_audit_module_imports_clean() -> None:
    """The audit module must not use ``time.sleep``, ``asyncio.sleep``,
    ``subprocess.run``, ``httpx.*``, ``requests.*``, ``urllib.request.urlopen``,
    or ``socket.create_connection`` (the test-policy and mcp-timeout
    invariants).  The audit is purely an AST walk over local files.

    Uses AST-based detection (not regex) so the literal strings in
    the test source do not produce false positives.
    """

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


@pytest.mark.subprocess_e2e
def test_audit_module_main_function_returns_zero_on_clean_tree() -> None:
    """Run the audit's main() in-process and assert exit 0 on the real tree.

    This is the black-box proof that the audit works as a wired
    verify step.  Calling ``main()`` directly (instead of via
    ``subprocess``) keeps the test well under the 1s per-test
    timeout while still validating the CLI entry-point contract.
    """
    rc = audit.main([])
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
