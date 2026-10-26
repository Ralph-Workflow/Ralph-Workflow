"""Contract tests for MCP E2E test suite registration (U0b).

Enforces repository policy that every subprocess_e2e-marked test file under
tests/mcp/ is registered in REQUIRED_AUTO_INTEGRATE_E2E_FILES (so it runs
under the default `make test` verification gate) or explicitly documented
in an exemption allowlist with justification.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ralph.test_suites import (
    REQUIRED_AUTO_INTEGRATE_E2E_FILES,
    SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES,
    validate_required_auto_integrate_selection,
)

# Explicitly exempt files (none currently, but allowlist exists with strict typing)
_EXEMPT_MCP_SUBPROCESS_E2E_FILES: frozenset[str] = frozenset()


def _is_file_marked_subprocess_e2e(file_path: Path) -> bool:
    """Return True if file_path defines pytestmark containing subprocess_e2e."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    if "subprocess_e2e" not in text:
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    src = ast.unparse(node.value)
                    if "subprocess_e2e" in src:
                        return True
    return False


def test_every_subprocess_e2e_mcp_file_is_registered_or_exempt() -> None:
    """Every test file under tests/mcp/ marked subprocess_e2e must be registered in REQUIRED_AUTO_INTEGRATE_E2E_FILES."""
    repo_root = Path(__file__).resolve().parent.parent
    mcp_tests_dir = repo_root / "tests" / "mcp"
    assert mcp_tests_dir.is_dir()

    registered_set = set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    unregistered_unexempt: list[str] = []

    for test_file in sorted(mcp_tests_dir.glob("test_*.py")):
        rel_path = f"tests/mcp/{test_file.name}"
        if (
            _is_file_marked_subprocess_e2e(test_file)
            and rel_path not in registered_set
            and rel_path not in _EXEMPT_MCP_SUBPROCESS_E2E_FILES
        ):
            unregistered_unexempt.append(rel_path)

    assert not unregistered_unexempt, (
        f"Found subprocess_e2e files under tests/mcp/ that are neither in "
        f"REQUIRED_AUTO_INTEGRATE_E2E_FILES nor in _EXEMPT_MCP_SUBPROCESS_E2E_FILES: {unregistered_unexempt}. "
        f"All E2E tests must be wired into verification per AGENTS.md §'Non-negotiables'."
    )


def test_required_auto_integrate_all_files_exist_on_disk() -> None:
    """Every file in REQUIRED_AUTO_INTEGRATE_E2E_FILES must exist on disk."""
    repo_root = Path(__file__).resolve().parent.parent
    missing_files: list[str] = []

    for rel_path in REQUIRED_AUTO_INTEGRATE_E2E_FILES:
        full_path = repo_root / rel_path
        if not full_path.is_file():
            missing_files.append(rel_path)

    assert not missing_files, (
        f"Files listed in REQUIRED_AUTO_INTEGRATE_E2E_FILES do not exist: {missing_files}"
    )


def test_sanctioned_default_lane_real_git_files_exist_on_disk() -> None:
    """Every file in SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES must exist on disk."""
    repo_root = Path(__file__).resolve().parent.parent
    missing_files: list[str] = []

    for rel_path in SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES:
        full_path = repo_root / rel_path
        if not full_path.is_file():
            missing_files.append(rel_path)

    assert not missing_files, (
        f"Files listed in SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES do not exist: {missing_files}"
    )


def test_required_auto_integrate_selection_accepts_registered_files() -> None:
    """validate_required_auto_integrate_selection accepts the registered list."""
    validate_required_auto_integrate_selection(REQUIRED_AUTO_INTEGRATE_E2E_FILES)


def test_required_auto_integrate_selection_rejects_omissions() -> None:
    """validate_required_auto_integrate_selection fails when a registered file is omitted."""
    omitted = REQUIRED_AUTO_INTEGRATE_E2E_FILES[1:]
    with pytest.raises(RuntimeError, match="combined pytest selection omitted required"):
        validate_required_auto_integrate_selection(omitted)


def test_all_wt16_edge_case_and_root_cause_files_registered() -> None:
    """Verify that all wt-16 MCP make-before-break edge-case test files are registered."""
    expected_wt16_files = {
        "tests/mcp/test_e17_state_continuity.py",
        "tests/mcp/test_e18_old_session_id_accepted.py",
        "tests/mcp/test_e24_fresh_stage_id.py",
        "tests/mcp/test_e25_candidate_survives_agent_tmp_deletion.py",
        "tests/mcp/test_e26_broken_workspace_package.py",
        "tests/mcp/test_e33_listening_fd_real_child.py",
        "tests/mcp/test_e3x_transport_gap_typed_retry.py",
        "tests/mcp/test_e42_two_supervisors_one_replacement.py",
        "tests/mcp/test_e46_parent_sigkill_no_orphans.py",
        "tests/mcp/test_handover_inflight_real_child.py",
        "tests/mcp/test_replacement_shutdown_signals_real_child.py",
        "tests/mcp/test_root_cause_real_child_bridge.py",
        "tests/mcp/test_single_acceptor_instrumented.py",
    }
    registered_set = set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    missing = expected_wt16_files - registered_set
    assert not missing, (
        f"Missing wt-16 test files from REQUIRED_AUTO_INTEGRATE_E2E_FILES: {missing}"
    )
