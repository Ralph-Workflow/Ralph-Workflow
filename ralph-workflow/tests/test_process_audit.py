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
ALLOWLIST: list[tuple[str, str]] = []

# Files under TESTS_ROOT that are allowed to use subprocess directly.
# Each entry should have a comment explaining why it's allowlisted.
TESTS_ALLOWLIST: set[str] = {
    "test_process_audit.py",           # defines pattern strings as literals
    "test_process_cross_platform.py",  # defines forbidden token strings as literals for inspection
    "test_process_manager.py",         # drives ProcessManager; subprocess.run is test infra
    "test_parallel_coordinator.py",    # git repo setup via subprocess.run in test fixtures
    "test_git_rebase.py",              # git repo setup via subprocess.run in test fixtures
    "test_git_rebase_continuation.py", # git repo setup via subprocess.run in test fixtures
    "test_asyncio_bridge.py",          # patches os.killpg; no real call
}

_MCP_FIXTURE_FILES = {
    "test_fake_http_mcp_fixture.py",
    "test_fake_stdio_mcp_fixture.py",
    "test_mcp_e2e.py",
    "test_validate_custom_mcp_http_e2e.py",
    "test_custom_mcp_roundtrip.py",
}


def _allowed(rel_path: str) -> bool:
    return any(rel_path == path for path, _ in ALLOWLIST)


def test_no_direct_subprocess_calls_outside_process_manager() -> None:
    """Assert no production file under ralph/ uses subprocess directly except manager.py."""
    violations = [
        f"{py_file.relative_to(RALPH_ROOT).as_posix()}: contains '{pattern}'"
        for py_file in sorted(RALPH_ROOT.rglob("*.py"))
        for rel in [py_file.relative_to(RALPH_ROOT).as_posix()]
        if rel != "process/manager.py" and not _allowed(rel)
        for pattern in FORBIDDEN_PATTERNS
        if pattern in py_file.read_text(encoding="utf-8")
    ]

    assert not violations, (
        "Direct subprocess calls found outside ralph/process/manager.py:\n"
        + "\n".join(violations)
    )


def test_no_direct_subprocess_calls_in_tests() -> None:
    """Assert no test file uses subprocess or POSIX kill APIs directly.

    Allowlisted files are test-infrastructure uses (git setup, pattern literals).
    New test files must not bypass ProcessManager.
    """
    all_patterns = FORBIDDEN_PATTERNS + POSIX_FORBIDDEN
    violations = [
        f"{py_file.relative_to(TESTS_ROOT).as_posix()}: contains '{pattern}'"
        for py_file in sorted(TESTS_ROOT.rglob("*.py"))
        # Skip files that are under RALPH_ROOT (already scanned above) or allowlisted
        if not any(py_file.is_relative_to(p) for p in [RALPH_ROOT])
        for rel in [py_file.name]
        if rel not in TESTS_ALLOWLIST
        for pattern in all_patterns
        if pattern in py_file.read_text(encoding="utf-8")
    ]

    assert not violations, (
        "Direct subprocess/POSIX calls found in tests/ outside the allowlist:\n"
        + "\n".join(violations)
    )


def test_mcp_fixtures_no_longer_allowlisted() -> None:
    """Assert that the migrated MCP fixture files are not in TESTS_ALLOWLIST."""
    regressions = _MCP_FIXTURE_FILES & TESTS_ALLOWLIST
    assert not regressions, (
        "MCP fixture files were re-added to TESTS_ALLOWLIST:\n"
        + "\n".join(sorted(regressions))
    )
