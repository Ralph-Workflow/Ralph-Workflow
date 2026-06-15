"""Static audit: no direct subprocess calls outside ProcessManager."""

from __future__ import annotations

from pathlib import Path

RALPH_ROOT = Path(__file__).parent.parent / "ralph"
TESTS_ROOT = Path(__file__).parent

FORBIDDEN_PATTERNS = [
    "subprocess.run(",
    "subprocess.Popen(",
    "asyncio.create_subprocess_exec(",
    "asyncio.create_subprocess_shell(",
]

POSIX_FORBIDDEN = [
    "os.killpg(",
    "os.setsid(",
]

# Files under RALPH_ROOT that are allowed to use subprocess directly.
ALLOWLIST: list[tuple[str, str]] = [
    (
        "mcp/tools/unsafe_exec.py",
        "intentionally uses subprocess.run with shell=True for unrestricted shell execution",
    ),
    (
        "testing/audit_mcp_timeout.py",
        "references subprocess.run/Popen as detection-pattern strings; does not call subprocess",
    ),
]

# Files under TESTS_ROOT that are allowed to use subprocess directly.
# Each entry should have a comment explaining why it's allowlisted.
TESTS_ALLOWLIST: set[str] = {
    "test_process_audit.py",  # defines pattern strings as literals
    "test_process_cross_platform.py",  # defines forbidden token strings as literals for inspection
    "test_process_manager.py",  # drives ProcessManager; subprocess.run is test infra
    "test_parallel_coordinator.py",  # git repo setup via subprocess.run in test fixtures
    "test_git_rebase.py",  # git repo setup via subprocess.run in test fixtures
    "test_git_rebase_continuation.py",  # git repo setup via subprocess.run in test fixtures
    "test_asyncio_bridge.py",  # patches os.killpg; no real call
    "test_cli.py",  # exercises actual console-script entrypoint via subprocess
    "test_install.py",  # wheel build/install smoke coverage in a throwaway venv
    "test_interrupt_signal_realtime.py",  # live SIGINT black-box coverage needs a subprocess
    "test_claude_interactive_interrupt_realtime.py",  # PTY-backed live SIGINT black-box coverage
    "test_skills_package_sync_script.py",  # node packaging sync coverage uses a subprocess
    "test_audit_test_policy.py",  # contains subprocess.run literals as test-fixture code strings
    "test_audit_mcp_timeout.py",  # subprocess.run/Popen literals as audit-fixture code strings
    "test_audit_parallelization_dormant.py",
    # invokes the audit module as a subprocess in test_audit_executable_invocation_returns_zero
    "test_audit_activity_aware_watchdog.py",
    # invokes the audit module as a subprocess to verify the main() exit code
    "test_verify_budget_real_time.py",  # tests process-level timeout behavior via subprocess
    "test_verify_invariants.py",  # spawns patched subprocesses to verify import-time invariants
    # spawns python -O to verify size-limit import-time invariants
    "test_plan_artifact_size_limits.py",
    "test_mock_agy_binary.py",  # black-box subprocess tests for the deterministic AGY mock
    "test_agy_plumbing_contract.py",  # contract tests for AGY smoke plumbing
    "test_monitor.py",  # live psutil process-tree black-box coverage needs a real subprocess
    "test_teardown.py",  # live process-subtree teardown black-box coverage needs a real subprocess
    "test_e2e_activity_aware.py",  # e2e watchdog coverage needs real subprocesses
}

_MCP_FIXTURE_FILES = {
    "test_fake_http_mcp_fixture.py",
    "test_fake_stdio_mcp_fixture.py",
    "test_mcp_e2e.py",
    "test_validate_custom_mcp_http_e2e.py",
    "test_custom_mcp_roundtrip.py",
}

RALPH_PY_FILES = tuple(sorted(RALPH_ROOT.rglob("*.py")))
TEST_PY_FILES = tuple(
    sorted(
        py_file for py_file in TESTS_ROOT.rglob("*.py") if not py_file.is_relative_to(RALPH_ROOT)
    )
)
RALPH_PY_CONTENTS = tuple(
    (py_file, py_file.read_text(encoding="utf-8")) for py_file in RALPH_PY_FILES
)
TEST_PY_CONTENTS = tuple(
    (py_file, py_file.read_text(encoding="utf-8")) for py_file in TEST_PY_FILES
)


def _allowed(rel_path: str) -> bool:
    return any(rel_path == path for path, _ in ALLOWLIST)


def test_no_direct_subprocess_calls_outside_process_manager() -> None:
    """Assert no production file under ralph/ uses subprocess directly except manager.py."""
    violations: list[str] = []
    for py_file, content in RALPH_PY_CONTENTS:
        rel = py_file.relative_to(RALPH_ROOT).as_posix()
        if rel == "process/manager/__init__.py" or _allowed(rel):
            continue
        violations.extend(
            f"{rel}: contains '{pattern}'" for pattern in FORBIDDEN_PATTERNS if pattern in content
        )

    assert not violations, (
        "Direct subprocess calls found outside ralph/process/manager/__init__.py:\n"
        + "\n".join(violations)
    )


def test_no_direct_subprocess_calls_in_tests() -> None:
    """Assert no test file uses subprocess or POSIX kill APIs directly.

    Allowlisted files are test-infrastructure uses (git setup, pattern literals).
    New test files must not bypass ProcessManager.
    """
    all_patterns = FORBIDDEN_PATTERNS + POSIX_FORBIDDEN
    violations: list[str] = []
    for py_file, content in TEST_PY_CONTENTS:
        if py_file.is_relative_to(RALPH_ROOT):
            continue
        if py_file.name in TESTS_ALLOWLIST:
            continue
        rel = py_file.relative_to(TESTS_ROOT).as_posix()
        violations.extend(
            f"{rel}: contains '{pattern}'" for pattern in all_patterns if pattern in content
        )

    assert not violations, (
        "Direct subprocess/POSIX calls found in tests/ outside the allowlist:\n"
        + "\n".join(violations)
    )


def test_mcp_fixtures_no_longer_allowlisted() -> None:
    """Assert that the migrated MCP fixture files are not in TESTS_ALLOWLIST."""
    regressions = _MCP_FIXTURE_FILES & TESTS_ALLOWLIST
    assert not regressions, "MCP fixture files were re-added to TESTS_ALLOWLIST:\n" + "\n".join(
        sorted(regressions)
    )
