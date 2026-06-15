"""Commit plumbing must use canonical artifact submission.

The commit CLI has a single canonical path for artifact submission
through the MCP tool handle_submit_artifact. No direct writes to
.agent/receipts/ or .agent/completion_seen_*.json are permitted.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


def test_commit_plumbing_never_directly_writes_receipt_or_sentinel() -> None:
    """Commit plumbing never directly writes to .agent/receipts/ or .agent/completion_seen_*.json.

    This test reads the commit_plumbing.py source and scans for patterns that
    would indicate direct writes to protected paths outside the canonical
    submission block markers.
    """
    plumbing_path = (
        Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    )
    source_text = plumbing_path.read_text(encoding="utf-8")

    # Patterns that would indicate direct writes to protected paths
    protected_write_patterns = [
        # Direct Path.write_text / write_bytes to .agent/receipts/
        r"\.agent/receipts/.*\.write_(text|bytes)",
        # Direct Path.open for writing to .agent/receipts/
        r"\.agent/receipts/.*\.open.*\w",
        # Direct writes to .agent/completion_seen_*.json
        r"\.agent/completion_seen_.*\.write_(text|bytes)",
        r"\.agent/completion_seen_.*\.open.*\w",
        # Direct json.dump to protected paths
        r"json\.dump.*\.agent/(receipts|completion_seen_)",
    ]

    for pattern in protected_write_patterns:
        matches = re.findall(pattern, source_text)
        assert (
            not matches
        ), f"Found direct write to protected path matching {pattern}: {matches}"


def test_commit_plumbing_receipt_cleared_before_each_attempt() -> None:
    """Clear run receipts is called at the start of _run_commit_agent_attempt_with_recovery.

    This protects against stale-receipt contamination between attempts.
    """
    # Read the source of _run_commit_agent_attempt_with_recovery
    plumbing_path = (
        Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    )
    source_text = plumbing_path.read_text(encoding="utf-8")

    # Parse the source to find the function definition
    tree = ast.parse(source_text)

    # Find _run_commit_agent_attempt_with_recovery function
    target_func = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_run_commit_agent_attempt_with_recovery"
        ):
            target_func = node
            break

    assert target_func is not None, "Function _run_commit_agent_attempt_with_recovery not found"

    # Check if clear_run_receipts is called in the function body
    calls_clear_run_receipts = False
    for node in ast.walk(target_func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "clear_run_receipts"
        ):
            calls_clear_run_receipts = True
            break

    assert (
        calls_clear_run_receipts
    ), "_run_commit_agent_attempt_with_recovery does not call clear_run_receipts"


def test_commit_plumbing_run_id_binding_is_stable() -> None:
    """Receipt is stamped under _COMMIT_RUN_ID and never any other value.

    This protects against the artifact-handoff drift bug.
    """
    plumbing_path = (
        Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    )
    source_text = plumbing_path.read_text(encoding="utf-8")

    # Check that _COMMIT_RUN_ID is used consistently
    # The constant should be defined and used
    assert "_COMMIT_RUN_ID" in source_text, "_COMMIT_RUN_ID constant not defined"

    # Check that the constant value is used (not a different run_id literal)
    # Look for patterns like run_id="commit-plumbing" or run_id='commit-plumbing'
    # and ensure they don't appear (only the constant should be used)
    non_constant_run_id_patterns = [
        r'run_id\s*=\s*["\']commit-plumbing["\']',
        r'run_id\s*=\s*["\'][^"\']*["\']',  # Any run_id literal
    ]

    # Only check for literals, not the specific value
    for pattern in non_constant_run_id_patterns[1:2]:
        matches = re.findall(pattern, source_text)
        # Filter out the constant definition itself
        filtered_matches = [m for m in matches if "_COMMIT_RUN_ID" not in m]
        assert (
            not filtered_matches
        ), f"Found run_id literal instead of _COMMIT_RUN_ID constant: {filtered_matches}"


def test_commit_plumbing_uses_only_allowlisted_delete() -> None:
    """Only clear_run_receipts is used for deletion in protected paths.

    No ad-hoc unlink of .agent/receipts/* or .agent/completion_seen_* files.
    Other cleanup operations (e.g., .agent/tmp/*) are allowed.
    """
    plumbing_path = (
        Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    )
    source_text = plumbing_path.read_text(encoding="utf-8")

    # Patterns that would indicate direct deletes to protected paths
    protected_delete_patterns = [
        # Direct Path.unlink/rmdir/remove to .agent/receipts/
        r"\.agent/receipts/.*\.(unlink|rmdir|remove)\(",
        # Direct Path.unlink/rmdir/remove to .agent/completion_seen_
        r"\.agent/completion_seen_.*\.(unlink|rmdir|remove)\(",
        # Direct os.remove/os.unlink/os.rmdir to protected paths
        r"os\.(remove|unlink|rmdir).*\.agent/(receipts|completion_seen_)",
    ]

    for pattern in protected_delete_patterns:
        matches = re.findall(pattern, source_text)
        assert (
            not matches
        ), f"Found direct delete to protected path matching {pattern}: {matches}"
