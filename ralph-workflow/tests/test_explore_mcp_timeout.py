"""Compliance tests for the MCP timeout contract in ralph/mcp/explore/.

Mirrors the contract enforced by ``ralph/testing/audit_mcp_timeout.py``:

* ``subprocess.run/call/check_call/check_output`` must carry a
  bounded ``timeout=``.
* ``.communicate(...)`` and ``.wait()`` must carry a bounded
  ``timeout=``.
* Network calls must carry a ``timeout=``.
* An inline ``# mcp-timeout-ok: <reason>`` marker is the only
  allowed bypass.

The test scans ``ralph/mcp/explore/`` via the same AST visitor
``McpTimeoutAuditor`` used by the audit command, so a regression
in ``audit_mcp_timeout.py`` would also break this test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing.audit_mcp_timeout import (
    audit_mcp_directory,
)


EXPLORE_ROOT = Path(__file__).resolve().parents[1] / "ralph" / "mcp" / "explore"


def test_explore_module_passes_mcp_timeout_audit() -> None:
    """Every file in ralph/mcp/explore/ must satisfy the MCP timeout contract."""
    if not EXPLORE_ROOT.is_dir():
        pytest.skip(f"explore module not present: {EXPLORE_ROOT}")
    violations, files_checked = audit_mcp_directory(EXPLORE_ROOT)
    formatted = "\n".join(str(v) for v in violations)
    assert not violations, (
        f"Found {len(violations)} MCP timeout contract violations "
        f"in {files_checked} file(s):\n{formatted}"
    )


def test_explore_handlers_use_bounded_timeouts() -> None:
    """Handlers must perform bounded I/O (no unbounded subprocess / network calls)."""
    if not EXPLORE_ROOT.is_dir():
        pytest.skip(f"explore module not present: {EXPLORE_ROOT}")
    handlers_py = EXPLORE_ROOT / "handlers.py"
    if not handlers_py.is_file():
        pytest.skip("handlers.py not present")
    violations, _ = audit_mcp_directory(handlers_py)
    assert not violations, "\n".join(str(v) for v in violations)


def test_explore_pipeline_uses_bounded_timeouts() -> None:
    """Pipeline must use bounded reindex operations."""
    pipeline_py = EXPLORE_ROOT / "pipeline.py"
    if not pipeline_py.is_file():
        pytest.skip("pipeline.py not present")
    violations, _ = audit_mcp_directory(pipeline_py)
    assert not violations, "\n".join(str(v) for v in violations)