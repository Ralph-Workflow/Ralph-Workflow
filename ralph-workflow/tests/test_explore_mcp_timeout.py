from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing.audit_mcp_timeout import (
    audit_mcp_directory,
    audit_mcp_file,
)

EXPLORE_ROOT = Path(__file__).resolve().parents[1] / "ralph" / "mcp" / "explore"


@pytest.mark.timeout_seconds(5)
def test_explore_module_passes_mcp_timeout_audit() -> None:
    violations, files_checked = audit_mcp_directory(EXPLORE_ROOT)
    formatted = "\n".join(str(v) for v in violations)
    assert not violations, (
        f"Found {len(violations)} MCP timeout contract violations "
        f"in {files_checked} file(s):\n{formatted}"
    )


def test_explore_handlers_use_bounded_timeouts() -> None:
    handlers_py = EXPLORE_ROOT / "handlers.py"
    violations = audit_mcp_file(handlers_py)
    assert not violations, "\n".join(str(v) for v in violations)


def test_explore_pipeline_uses_bounded_timeouts() -> None:
    pipeline_py = EXPLORE_ROOT / "pipeline.py"
    violations = audit_mcp_file(pipeline_py)
    assert not violations, "\n".join(str(v) for v in violations)
