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
        "_install_conflicts.py",
        "resolves an installed console script through its declared interpreter",
    ),
    (
        "testing/audit_mcp_timeout.py",
        "references subprocess.run/Popen as detection-pattern strings; does not call subprocess",
    ),
    (
        "testing/audit_resource_lifecycle.py",
        "references subprocess/asyncio spawn names as detection-pattern strings; does not call subprocess",
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
    "test_audit_filesystem_polling_invocation.py",  # subprocess.run literal in an audit-fixture source string
    "test_audit_resource_lifecycle.py",  # subprocess/asyncio spawn literals as audit-fixture code strings
    "test_audit_parallelization_dormant.py",
    # invokes the audit module as a subprocess in test_audit_executable_invocation_returns_zero
    "test_smoke_multimodal_end_to_end.py",  # per-harness multimodal smoke proof; drives mock_multimodal_agent as a subprocess
    "mock_multimodal_agent.py",  # the multimodal smoke stub itself is a subprocess-launched agent script
    "test_audit_activity_aware_watchdog.py",
    # invokes the audit module as a subprocess to verify the main() exit code
    "test_verify_budget_real_time.py",  # tests process-level timeout behavior via subprocess
    "test_packaging_operations.py",  # black-box coverage for the formula-check gate
    "test_verify_invariants.py",  # spawns patched subprocesses to verify import-time invariants
    # spawns python -O to verify size-limit import-time invariants
    "test_plan_artifact_size_limits.py",
    "test_mock_agy_binary.py",  # black-box subprocess tests for the deterministic AGY mock
    # black-box subprocess test for generated Pi TypeScript extension SSE behavior
    "test_pi_mcp_extension_sse_behavior.py",
    "test_agy_plumbing_mock.py",  # contract tests for AGY smoke plumbing
    "test_monitor.py",  # live psutil process-tree black-box coverage needs a real subprocess
    "test_teardown.py",  # live process-subtree teardown black-box coverage needs a real subprocess
    "test_e2e_activity_aware.py",  # e2e watchdog coverage needs real subprocesses
    "test_agy_live_regression.py",  # live AGY binary black-box coverage via subprocess
    "test_smoke_agy_end_to_end.py",  # drives ralph smoke-interactive-agy as a bounded subprocess
    "test_audit_artifact_submission_canonical_path.py",
    # spawns python -O to verify import-time invariants survive -O
    "test_single_mode_anti_drift.py",
    "test_explore_reindex_bench.py",
    # black-box subprocess coverage for ``python -m ralph.mcp.explore.reindex_bench``
    # --help / --compare / --end-to-end entry points
    # drives scripts/wt028-drift-check.sh via subprocess.run as the
    # system-under-test (the bash script is the artifact being probed;
    # subprocess is the same invocation path make verify-drift uses)
    "test_audit_terminal_escape_containment.py",
    # contains ``os.setsid()`` and ``Console(`` as audit-invariant
    # string literals (these are pattern-pinching needles, not real
    # subprocess calls -- the audit is exercised through monkeypatched
    # sources). Mirrors the existing allowlist pattern for audit
    # test files that maintain POS|CO process-marker literals.
    "test_idle_watchdog.py",
    # drives an end-to-end psutil children-recursion regression by
    # spawning a real python interpreter that itself forks a long-lived
    # child. The test must own the host process directly so the
    # ProcessManager fake cannot mask the bug it pins.
    "test_git_merge.py",  # git repo setup via subprocess.run in test fixtures (real-git subprocess_e2e suite)
    "test_auto_integrate.py",  # git repo setup via subprocess.run in test fixtures (real-git subprocess_e2e suite)
    "test_auto_integrate_resolution.py",  # git repo setup via subprocess.run in test fixtures (real-git subprocess_e2e suite; conflict-resolution + ff-retry tests)
    "test_auto_integrate_race.py",  # git repo setup via subprocess.run in test fixtures (real-git subprocess_e2e suite)
    "test_auto_integrate_recovery.py",  # git repo setup via subprocess.run in test fixtures (real-git subprocess_e2e suite; recovery + dashed-target security regression tests)
    "test_auto_integrate_worktree_sync.py",  # real-git multi-worktree integration regression
    "test_auto_integrate_end_to_end.py",  # clone-layout real-git integration regression
    "test_auto_integrate_stale_merge_marker.py",  # real-git worktree regression for the stale AUTO_MERGE marker
    "test_auto_integrate_remote_refresh.py",  # real-git clone-layout regression for the bounded origin refresh
    "test_auto_integrate_remote_push.py",  # configured-remote auto-integration push regression
    "test_auto_integrate_fail_closed_e2e.py",  # real-git regression for the fail-closed HEAD-read and merge-state queries
    "test_auto_integrate_refresh_contract.py",  # real-git regression for the pre-landing target refresh
    "test_auto_integrate_real_agent_resolution_e2e.py",  # real-git and real-agent MCP-session regression
    "test_auto_integrate_fleet_conflict_e2e.py",  # real-git proof that a conflicted rebase across two linked worktrees lands
    "test_auto_integrate_local_fleet_target_e2e.py",  # real-git multi-worktree proof for the no-origin fleet refresh
    "test_auto_integrate_conflict_budget.py",  # real-git regression for the bounded conflict-resolution budget
    "test_auto_integrate_catchup_e2e.py",  # git repo setup via subprocess.run in test fixtures (real-git subprocess_e2e suite; background catch-up fast-forward)
    # git worktree setup via subprocess.run in test fixtures (real-git
    # subprocess_e2e suite; prefix-colliding sibling worktree regression)
    "test_auto_integrate_worktree_prefix_e2e.py",
    # real-git fixtures for the four-seam / two-topology proof: linked
    # worktrees and clones of a local bare origin are built with git
    # itself, the same convention every sibling auto-integrate e2e file
    # follows
    "test_auto_integrate_seams_e2e.py",
    # real-git fixtures proving AC-11: hostile user git config (rerere,
    # gpgsign, autostash, autosquash, updateRefs) is neutralized before
    # the rebase argv runs. Uses subprocess.run to drive git config +
    # integration in the per-test repo.
    "test_auto_integrate_env_pinning.py",
    # real-git fixtures proving the markerless-conflict path lands the
    # auto-merge without leaving AUTO_MERGE / MERGE_MSG behind. Uses
    # subprocess.run to build the conflicted repo and run the resolver.
    "test_auto_integrate_markerless_conflicts.py",
    # real-git fixtures proving integration onto a non-``main`` target
    # branch (release candidate / release branch) lands unchanged. Uses
    # subprocess.run to set up the alternate-target repo and the
    # integration argv.
    "test_auto_integrate_non_main_target.py",
    # real-git fixtures proving the rung-4 self-resume path lands after
    # a crash leaves an in-progress rebase. Uses subprocess.run to set
    # up the crashed state and drive the recovery + resume argv.
    "test_auto_integrate_rung4_self_resume.py",
    "test_check_route_page_links.py",  # drives a real git subprocess to validate route-page link contracts
    # Patches ``os.killpg`` via monkeypatch to record what production teardown
    # would have signalled; no real subprocess or signal is delivered. The
    # regression test pins the AC-12 ownership guard (PID 1 and already-reaped
    # children must never reach the signal path).
    "test_teardown_ownership_guard.py",
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
        # Keep the audit focused on files below ``tests/``. The shared
        # content snapshot also includes generated/temporary test artifacts
        # outside that root; resolving those paths is both unnecessary and
        # disproportionately expensive for this static policy check.
        try:
            rel = py_file.relative_to(TESTS_ROOT).as_posix()
        except ValueError:
            continue
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
