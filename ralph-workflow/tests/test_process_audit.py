from __future__ import annotations

import ast
from pathlib import Path

import pytest

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "TESTS_ALLOWLIST": (
        "the retired sync-script absence assertion moved to the consolidated "
        "test_retired_skills_package_absent module, so its obsolete process-audit "
        "allowlist entry was removed"
    ),
    "test_no_direct_subprocess_calls_in_tests": (
        "test_repo_structure_boundary_rules.py carries os.killpg fixture strings "
        "for the repo-structure boundary-rule audit, so it joins the allowlist "
        "without a subprocess_e2e marker"
    ),
}

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
    (
        "diagnostics/host_pressure.py",
        "runs read-only sysctl -n kern.maxfiles with check=False and a 2s timeout; no shell, no user input",
    ),
    (
        "git/operations.py",
        "git lock liveness probe shells out to lsof and fuser with bounded "
        "subprocess timeouts (see _PROBE_TIMEOUT_S) to verify the lock "
        "holder is dead before unlinking; the calls are bounded, parent "
        "blocks on .run() so no fd or process leaks, and the alternative "
        "(calling ProcessManager) would couple the static AST-based "
        "audit to a heavy infrastructure module that the bounded probe "
        "deliberately avoids",
    ),
    (
        "mcp/explore/_bench_r6_metrics.py",
        "R6 benchmark harness spawns a fresh ``python -c`` subprocess to "
        "measure per-build peak RSS via /proc/self/status VmHWM; the parent "
        "blocks on .run()'s timeout so the child cannot outlive the call and "
        "no fd or process leaks across the harness",
    ),
    (
        "mcp/explore/_bench_r6_subprocess.py",
        "Subprocess helper split out from _bench_r6_metrics for the file-size "
        "ceiling; same single-purpose subprocess.run pattern as the parent "
        "(bounded timeout, parent blocks on .run() so no leaks)",
    ),
    (
        "git/_lock_recovery.py",
        "runs read-only bounded lsof and fuser probes with check=False and 0.5s timeout for lock liveness discovery",
    ),
    (
        "testing/audit_comments.py",
        "uses read-only git rev-parse and git ls-files to enumerate tracked files for comment auditing",
    ),
    (
        "git/_lock_recovery.py",
        "runs read-only lsof and fuser to probe git lock liveness with check=False and bounded timeout; no shell, no user input",
    ),
    (
        "testing/_audit_baseline.py",
        "materialises committed git baseline trees for diff-based audit gates with bounded timeout",
    ),
    (
        "testing/audit_comments.py",
        "enumerates the tracked file list with read-only git queries "
        "(git rev-parse --show-toplevel, git ls-files -z) so the comment audit can "
        "scan the tree; check=False, no shell, no user input, and the same "
        "bounded-git pattern as testing/_audit_baseline.py",
    ),
]

TESTS_ALLOWLIST: set[str] = {
    "test_audit_secret_scan.py",
    "test_repo_structure_boundary_rules.py",
    "test_process_audit.py",
    "test_process_cross_platform.py",
    "test_process_manager.py",
    "test_config_bootstrap_git_isolation.py",
    "test_parallel_coordinator.py",
    "test_git_rebase.py",
    "test_git_rebase_continuation.py",
    "test_asyncio_bridge.py",
    "test_cli.py",
    "test_install.py",
    "test_install_wheel.py",
    "test_install_make_smoke.py",
    "test_interrupt_signal_realtime.py",
    "test_claude_interactive_interrupt_realtime.py",
    "test_terminal_state_restored_on_exit.py",
    "test_audit_test_policy.py",
    "test_audit_mcp_timeout.py",
    "test_audit_filesystem_polling_invocation.py",
    "test_audit_resource_lifecycle.py",
    "e72_descendant_helper.py",
    "test_audit_parallelization_dormant.py",
    "test_smoke_multimodal_end_to_end.py",
    "mock_multimodal_agent.py",
    "test_audit_activity_aware_watchdog.py",
    "test_verify_budget_real_time.py",
    "test_packaging_operations.py",
    "test_verify_invariants.py",
    "test_plan_artifact_size_limits.py",
    "test_mock_agy_binary.py",
    "test_pi_mcp_extension_sse_behavior.py",
    "test_agy_plumbing_mock.py",
    "test_monitor.py",
    "test_teardown.py",
    "test_e2e_activity_aware.py",
    "test_agy_live_regression.py",
    "test_smoke_agy_end_to_end.py",
    "test_smoke_agy_full_lifecycle_e2e.py",
    "test_audit_artifact_submission_canonical_path.py",
    "test_single_mode_anti_drift.py",
    "test_timing_safety_transports.py",
    "test_explore_reindex_bench.py",
    "test_audit_terminal_escape_containment.py",
    "test_idle_watchdog.py",
    "test_v4_corpus_authentic.py",
    "test_git_merge.py",
    "test_integration_obstruction_real_git.py",
    "test_a_live_capture_is_never_truncated.py",
    "test_auto_integrate.py",
    "test_auto_integrate_resolution.py",
    "test_auto_integrate_race.py",
    "test_auto_integrate_recovery.py",
    "test_auto_integrate_worktree_sync.py",
    "test_auto_integrate_end_to_end.py",
    "test_auto_integrate_stale_merge_marker.py",
    "test_auto_integrate_remote_refresh.py",
    "test_auto_integrate_remote_push.py",
    "test_auto_integrate_fail_closed_e2e.py",
    "test_auto_integrate_refresh_contract.py",
    "test_auto_integrate_real_agent_resolution_e2e.py",
    "test_auto_integrate_fleet_conflict_e2e.py",
    "test_auto_integrate_local_fleet_target_e2e.py",
    "test_auto_integrate_conflict_budget.py",
    "test_auto_integrate_catchup_e2e.py",
    "test_auto_integrate_worktree_prefix_e2e.py",
    "test_auto_integrate_seams_e2e.py",
    "test_auto_integrate_env_pinning.py",
    "test_auto_integrate_markerless_conflicts.py",
    "test_auto_integrate_non_main_target.py",
    "test_auto_integrate_rung4_self_resume.py",
    "test_integration_diagnostic_worktree_isolation.py",
    "test_auto_integrate_recovery_git_state_reflog.py",
    "test_check_route_page_links.py",
    "test_teardown_ownership_guard.py",
    "test_explore_crash_safety.py",
    "test_explore_concurrency.py",
    "test_explore_resource_lifecycle.py",
    "test_commit_review_real_diff_proofs.py",
    "test_repo_structure_boundary_rules.py",
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


def _parse_ast_safe(content: str) -> ast.AST | None:
    try:
        return ast.parse(content, type_comments=True)
    except (SyntaxError, ValueError):
        return None


def _is_pytest_mark_subprocess_e2e(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "subprocess_e2e"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _has_subprocess_e2e_marker(tree: ast.AST | None) -> bool:
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                if _is_pytest_mark_subprocess_e2e(dec):
                    return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    if _is_pytest_mark_subprocess_e2e(node.value):
                        return True
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if _is_pytest_mark_subprocess_e2e(elt):
                                return True
    return False


def _allowed(rel_path: str) -> bool:
    return any(rel_path == path for path, _ in ALLOWLIST)


@pytest.mark.timeout_seconds(5)
def test_no_direct_subprocess_calls_outside_process_manager() -> None:
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


@pytest.mark.timeout_seconds(5)
def test_no_direct_subprocess_calls_in_tests() -> None:
    all_patterns = FORBIDDEN_PATTERNS + POSIX_FORBIDDEN
    violations: list[str] = []
    for py_file, content in TEST_PY_CONTENTS:
        if py_file.name in TESTS_ALLOWLIST:
            continue
        found = [pattern for pattern in all_patterns if pattern in content]
        if not found:
            continue
        if _has_subprocess_e2e_marker(_parse_ast_safe(content)):
            continue
        try:
            rel = py_file.relative_to(TESTS_ROOT).as_posix()
        except ValueError:
            continue
        violations.extend(f"{rel}: contains '{pattern}'" for pattern in found)

    assert not violations, (
        "Direct subprocess/POSIX calls found in tests/ outside the allowlist:\n"
        + "\n".join(violations)
    )


def test_mcp_fixtures_no_longer_allowlisted() -> None:
    regressions = _MCP_FIXTURE_FILES & TESTS_ALLOWLIST
    assert not regressions, "MCP fixture files were re-added to TESTS_ALLOWLIST:\n" + "\n".join(
        sorted(regressions)
    )


def test_subprocess_e2e_marker_positive__real_pytestmark_exempts_subprocess_run(
    tmp_path: Path,
) -> None:
    source = """
import pytest
import subprocess

pytestmark = pytest.mark.subprocess_e2e

def test_real():
    subprocess.run(["git", "status"], check=False)
"""
    fixture_path = tmp_path / "test_positive_fixture.py"
    fixture_path.write_text(source, encoding="utf-8")
    content = fixture_path.read_text(encoding="utf-8")
    tree = _parse_ast_safe(content)
    assert _has_subprocess_e2e_marker(tree) is True, (
        "A file with real pytestmark = pytest.mark.subprocess_e2e must be recognized exempt"
    )


def test_subprocess_e2e_marker_negative__comment_only_does_not_exempt_subprocess_run(
    tmp_path: Path,
) -> None:
    source = """
import subprocess

# uses pytest.mark.subprocess_e2e

def test_fake():
    subprocess.run(["git", "status"], check=False)
"""
    fixture_path = tmp_path / "test_negative_fixture.py"
    fixture_path.write_text(source, encoding="utf-8")
    content = fixture_path.read_text(encoding="utf-8")
    tree = _parse_ast_safe(content)
    assert _has_subprocess_e2e_marker(tree) is False, (
        "A comment-only mention of subprocess_e2e must NOT exempt the file;"
        " test_audit_test_policy.py fixture strings must remain non-exempt"
    )
