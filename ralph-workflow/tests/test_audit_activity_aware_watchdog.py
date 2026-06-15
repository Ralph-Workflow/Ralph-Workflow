"""Regression tests pinning the activity-aware watchdog contract.

The activity-aware watchdog audit
(``ralph.testing.audit_activity_aware_watchdog``) is the gate that makes
the subagent/tool-visibility contract from PROMPT.md a hard fail. It
locks seven invariants:

  * ``IdleWatchdog`` is constructed with ``process_monitor=``.
  * ``set_active_sink`` is wired after watchdog construction.
  * ``set_subagent_sink`` is wired after watchdog construction.
  * ``WorkspaceMonitor.set_on_event`` is bound to a 2-arg forwarding
    callable (not the legacy 0-arg bound method).
  * ``teardown_subtree`` is called on every fire path after
    ``self._handle.terminate``.
  * ``teardown_subtree`` (or ``_teardown_subtree_if_pid_available``) is
    called on every error/crash path that raises ``AgentInvocationError``.
  * ``DefaultProcessMonitor`` is constructed with injected
    ``role_classifier=``, ``discovery_strategy=``, and
    ``subagent_pid_source=``, and the role classifier comes from
    ``role_classifier_for_transport``.

These tests write forbidden-construct source files under pytest's
``tmp_path`` fixture and run the audit directly against the temp
``package_root`` or individual temp files. No real subprocess,
no ``time.sleep``, no real file I/O outside ``tmp_path``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ralph.testing import audit_activity_aware_watchdog as audit


def _write_fake_package(tmp_path: Path) -> Path:
    """Create a minimal ``agents/invoke/`` package layout."""
    package_root = tmp_path / "fake_ralph"
    (package_root / "agents" / "invoke").mkdir(parents=True)
    return package_root


def _reader_source_with_missing_process_monitor() -> str:
    return (
        "from ralph.agents.idle_watchdog import IdleWatchdog\n"
        "from ralph.agents.timeout_clock import FakeClock\n"
        "\n"
        "def read_lines(self):\n"
        "    watchdog = IdleWatchdog(self._policy, FakeClock())\n"
        "    yield 'x'\n"
    )


def _reader_source_with_missing_active_sink() -> str:
    return (
        "from ralph.agents.idle_watchdog import IdleWatchdog\n"
        "from ralph.agents.timeout_clock import FakeClock\n"
        "\n"
        "def read_lines(self):\n"
        "    watchdog = IdleWatchdog(\n"
        "        self._policy, FakeClock(), process_monitor=None\n"
        "    )\n"
        "    yield 'x'\n"
    )


def _reader_source_with_missing_subagent_sink() -> str:
    return (
        "from ralph.agents.idle_watchdog import IdleWatchdog\n"
        "from ralph.mcp.server._activity_sink import set_active_sink\n"
        "from ralph.agents.timeout_clock import FakeClock\n"
        "\n"
        "def read_lines(self):\n"
        "    watchdog = IdleWatchdog(\n"
        "        self._policy, FakeClock(), process_monitor=None\n"
        "    )\n"
        "    set_active_sink(lambda _: watchdog.record_mcp_tool_call())\n"
        "    yield 'x'\n"
    )


def _reader_source_with_zero_arg_set_on_event() -> str:
    return (
        "from ralph.agents.idle_watchdog import IdleWatchdog\n"
        "from ralph.agents.invoke._workspace import WorkspaceMonitor\n"
        "from ralph.agents.timeout_clock import FakeClock\n"
        "\n"
        "def read_lines(self):\n"
        "    monitor = WorkspaceMonitor('/tmp')\n"
        "    watchdog = IdleWatchdog(\n"
        "        self._policy, FakeClock(), process_monitor=None\n"
        "    )\n"
        "    monitor.set_on_event(watchdog.record_workspace_event)\n"
        "    yield 'x'\n"
    )


def _reader_source_with_missing_teardown_subtree() -> str:
    return (
        "from ralph.agents.idle_watchdog import IdleWatchdog\n"
        "from ralph.agents.timeout_clock import FakeClock\n"
        "\n"
        "class Reader:\n"
        "    def _check_fire(self, watchdog):\n"
        "        watchdog = IdleWatchdog(\n"
        "            self._policy, FakeClock(), process_monitor=None\n"
        "        )\n"
        "        self._handle.terminate(grace_period_s=0.5)\n"
        "        return None\n"
    )


def _reader_source_with_valid_teardown_subtree() -> str:
    return (
        "from ralph.agents.idle_watchdog import IdleWatchdog\n"
        "from ralph.process.teardown import teardown_subtree\n"
        "from ralph.agents.timeout_clock import FakeClock\n"
        "\n"
        "class Reader:\n"
        "    def _check_fire(self, watchdog):\n"
        "        watchdog = IdleWatchdog(\n"
        "            self._policy, FakeClock(), process_monitor=None\n"
        "        )\n"
        "        self._handle.terminate(grace_period_s=0.5)\n"
        "        teardown_subtree(self._handle.pid)\n"
        "        return None\n"
    )


def _reader_source_fully_wired() -> str:
    """A reader that satisfies all six invoke-file invariants."""
    return (
        "from ralph.agents.idle_watchdog import IdleWatchdog\n"
        "from ralph.agents.invoke._workspace import WorkspaceMonitor\n"
        "from ralph.mcp.server._activity_sink import set_active_sink\n"
        "from ralph.mcp.server._activity_sink import set_subagent_sink\n"
        "from ralph.process.teardown import teardown_subtree\n"
        "from ralph.agents.timeout_clock import FakeClock\n"
        "\n"
        "class Reader:\n"
        "    def read_lines(self):\n"
        "        monitor = WorkspaceMonitor('/tmp')\n"
        "        watchdog = IdleWatchdog(\n"
        "            self._policy, FakeClock(), process_monitor=None\n"
        "        )\n"
        "        def _forward_event(kind, weight):\n"
        "            watchdog.record_workspace_event(kind=kind, weight=weight)\n"
        "        monitor.set_on_event(_forward_event)\n"
        "        set_active_sink(lambda: watchdog.record_mcp_tool_call())\n"
        "        set_subagent_sink(lambda: watchdog.record_subagent_work())\n"
        "        yield 'x'\n"
        "\n"
        "    def _check_fire(self):\n"
        "        self._handle.terminate(grace_period_s=0.5)\n"
        "        teardown_subtree(self._handle.pid)\n"
        "        return None\n"
    )


def test_audit_flags_idle_watchdog_without_process_monitor(tmp_path: Path) -> None:
    """A reader that constructs ``IdleWatchdog`` without ``process_monitor=``
    is flagged as a ``process_monitor_injection`` violation."""
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "agents" / "invoke" / "_bad_reader.py"
    bad_module.write_text(_reader_source_with_missing_process_monitor(), encoding="utf-8")

    violations = audit.audit_activity_aware_watchdog(package_root)

    assert violations, "expected a process_monitor_injection violation"
    categories = {v.category for v in violations}
    assert "process_monitor_injection" in categories, f"got {categories}"
    paths = {v.file_path for v in violations}
    assert "agents/invoke/_bad_reader.py" in paths, f"got {paths}"


def test_audit_flags_reader_missing_active_sink(tmp_path: Path) -> None:
    """A reader that constructs ``IdleWatchdog`` with ``process_monitor=`` but
    does not call ``set_active_sink`` is flagged as ``mcp_tool_sink``."""
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "agents" / "invoke" / "_bad_reader.py"
    bad_module.write_text(_reader_source_with_missing_active_sink(), encoding="utf-8")

    violations = audit.audit_activity_aware_watchdog(package_root)

    assert violations, "expected an mcp_tool_sink violation"
    categories = {v.category for v in violations}
    assert "mcp_tool_sink" in categories, f"got {categories}"


def test_audit_flags_reader_missing_subagent_sink(tmp_path: Path) -> None:
    """A reader that calls ``set_active_sink`` but not ``set_subagent_sink``
    is flagged as ``subagent_sink``."""
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "agents" / "invoke" / "_bad_reader.py"
    bad_module.write_text(_reader_source_with_missing_subagent_sink(), encoding="utf-8")

    violations = audit.audit_activity_aware_watchdog(package_root)

    assert violations, "expected a subagent_sink violation"
    categories = {v.category for v in violations}
    assert "subagent_sink" in categories, f"got {categories}"


def test_audit_flags_legacy_zero_arg_set_on_event_binding(tmp_path: Path) -> None:
    """A reader that binds ``monitor.set_on_event(watchdog.record_workspace_event)``
    (0-arg bound method) is flagged as ``workspace_event_binding``."""
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "agents" / "invoke" / "_bad_reader.py"
    bad_module.write_text(_reader_source_with_zero_arg_set_on_event(), encoding="utf-8")

    violations = audit.audit_activity_aware_watchdog(package_root)

    assert violations, "expected a workspace_event_binding violation"
    categories = {v.category for v in violations}
    assert "workspace_event_binding" in categories, f"got {categories}"


def test_audit_flags_reader_missing_teardown_subtree_on_fire_path(tmp_path: Path) -> None:
    """A reader that calls ``self._handle.terminate(...)`` without also calling
    ``teardown_subtree(...)`` in the same function body is flagged as
    ``teardown_subtree``."""
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "agents" / "invoke" / "_bad_reader.py"
    bad_module.write_text(_reader_source_with_missing_teardown_subtree(), encoding="utf-8")

    violations = audit.audit_activity_aware_watchdog(package_root)

    assert violations, "expected a teardown_subtree violation"
    categories = {v.category for v in violations}
    assert "teardown_subtree" in categories, f"got {categories}"


def test_audit_does_not_flag_production_call_sites() -> None:
    """Running the audit against the real ``ralph/`` package produces zero
    violations; the production readers satisfy all six invariants."""
    package_root = Path(__file__).parent.parent / "ralph"
    violations = audit.audit_activity_aware_watchdog(package_root)

    assert violations == [], "expected zero violations against production code, got:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_audit_reader_file_per_file_interface(tmp_path: Path) -> None:
    """``audit_reader_file`` runs detectors 1-5 on a single file."""
    bad_file = tmp_path / "_bad_reader.py"
    bad_file.write_text(_reader_source_with_missing_process_monitor(), encoding="utf-8")

    violations = audit.audit_reader_file(bad_file)

    assert violations, "expected at least one violation from per-file audit"
    assert any(v.category == "process_monitor_injection" for v in violations)


def test_audit_reader_file_valid_reader_is_clean(tmp_path: Path) -> None:
    """``audit_reader_file`` reports zero violations for a correctly wired
    reader file. This guards the rel_path fix that makes invoke-gated
    detectors active in per-file mode."""
    good_file = tmp_path / "_good_reader.py"
    good_file.write_text(_reader_source_fully_wired(), encoding="utf-8")

    violations = audit.audit_reader_file(good_file)

    assert violations == [], (
        "expected zero violations for a fully wired reader, got:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


@pytest.mark.subprocess_e2e
def test_audit_main_exit_code_is_one_on_violation(tmp_path: Path) -> None:
    """``main()`` exits 1 when a violation is present (hard-fail, no dry-run).

    This test spawns the audit module as a subprocess to verify the CLI
    exit-code contract end-to-end; it is marked ``subprocess_e2e`` so the
    test-policy audit excludes it from the fast suite.
    """
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "agents" / "invoke" / "_bad_reader.py"
    bad_module.write_text(_reader_source_with_missing_process_monitor(), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "ralph.testing.audit_activity_aware_watchdog", str(package_root)],
        capture_output=True,
        text=True,
        timeout=2,
        cwd=str(Path(__file__).parent.parent),
        check=False,
    )

    assert result.returncode == 1, (
        f"expected exit code 1, got {result.returncode}; stdout={result.stdout}"
    )
    assert "process_monitor_injection" in result.stdout


def test_audit_default_process_monitor_injection_violation(tmp_path: Path) -> None:
    """A ``DefaultProcessMonitor`` call missing ``role_classifier=`` is flagged
    as ``process_monitor_injection_full``."""
    source = (
        "from ralph.process.monitor import DefaultProcessMonitor\n"
        "\n"
        "def build():\n"
        "    return DefaultProcessMonitor(123)\n"
    )
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "agents" / "invoke" / "_bad_factory.py"
    bad_module.write_text(source, encoding="utf-8")

    violations = audit.audit_activity_aware_watchdog(package_root)

    assert violations, "expected a process_monitor_injection_full violation"
    categories = {v.category for v in violations}
    assert "process_monitor_injection_full" in categories, f"got {categories}"


def test_audit_role_classifier_must_be_role_classifier_for_transport(tmp_path: Path) -> None:
    """A ``DefaultProcessMonitor`` call whose ``role_classifier=`` is not a
    ``role_classifier_for_transport`` call is flagged as
    ``process_monitor_injection_full``."""
    source = (
        "from ralph.process.monitor import DefaultProcessMonitor\n"
        "\n"
        "def my_classifier(pid, cmdline):\n"
        "    pass\n"
        "\n"
        "def build():\n"
        "    return DefaultProcessMonitor(\n"
        "        123,\n"
        "        role_classifier=my_classifier,\n"
        "        discovery_strategy=None,\n"
        "        subagent_pid_source=None,\n"
        "    )\n"
    )
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "agents" / "invoke" / "_bad_factory.py"
    bad_module.write_text(source, encoding="utf-8")

    violations = audit.audit_activity_aware_watchdog(package_root)

    assert violations, "expected a process_monitor_injection_full violation"
    categories = {v.category for v in violations}
    assert "process_monitor_injection_full" in categories, f"got {categories}"


def test_audit_teardown_subtree_allowlisted_when_present(tmp_path: Path) -> None:
    """A reader that calls ``teardown_subtree`` on the terminate path is clean."""
    package_root = _write_fake_package(tmp_path)
    good_module = package_root / "agents" / "invoke" / "_good_reader.py"
    good_module.write_text(_reader_source_with_valid_teardown_subtree(), encoding="utf-8")

    violations = audit.audit_activity_aware_watchdog(package_root)

    teardown_violations = [v for v in violations if v.category == "teardown_subtree"]
    assert teardown_violations == [], (
        f"expected no teardown_subtree violation, got {teardown_violations}"
    )


def _completion_source_with_missing_error_path_teardown() -> str:
    return (
        "from ralph.agents.idle_watchdog import IdleWatchdog\n"
        "from ralph.agents.invoke._errors import AgentInvocationError\n"
        "from ralph.agents.timeout_clock import FakeClock\n"
        "\n"
        "class Reader:\n"
        "    def read_lines(self):\n"
        "        watchdog = IdleWatchdog(\n"
        "            self._policy, FakeClock(), process_monitor=None\n"
        "        )\n"
        "        yield 'x'\n"
        "\n"
        "def _check_process_result(handle, agent_name):\n"
        "    returncode = int(handle.returncode or 0)\n"
        "    if returncode != 0:\n"
        "        raise AgentInvocationError(agent_name, returncode, 'stderr')\n"
    )


def test_audit_flags_completion_error_path_missing_teardown(tmp_path: Path) -> None:
    """A function in an invoke file that raises ``AgentInvocationError``
    without calling ``teardown_subtree`` (or the helper) is flagged as
    ``error_path_teardown``."""
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "agents" / "invoke" / "_completion.py"
    bad_module.write_text(_completion_source_with_missing_error_path_teardown(), encoding="utf-8")

    violations = audit.audit_activity_aware_watchdog(package_root)

    assert violations, "expected an error_path_teardown violation"
    categories = {v.category for v in violations}
    assert "error_path_teardown" in categories, f"got {categories}"
    paths = {v.file_path for v in violations}
    assert "agents/invoke/_completion.py" in paths, f"got {paths}"
