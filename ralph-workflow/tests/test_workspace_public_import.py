"""Fresh-interpreter regression for public workspace exports."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ralph.process.manager import SpawnOptions, get_process_manager


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(5)
def test_workspace_protocol_is_importable_before_mcp_startup() -> None:
    """Public workspace imports must not depend on an earlier MCP import."""
    package_root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    process = get_process_manager().spawn(
        [
            sys.executable,
            "-c",
            (
                "from ralph.workspace.protocol import Workspace; "
                "from ralph.workspace import FsWorkspace; "
                "print(Workspace.__name__, FsWorkspace.__name__)"
            ),
        ],
        SpawnOptions(
            cwd=str(package_root),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            label="test:workspace-public-fresh-import",
            text=True,
        ),
    )
    stdout, stderr = process.communicate_and_cleanup(
        timeout=5,
        cleanup_grace_period_s=0.0,
    )

    assert process.returncode == 0, stderr
    assert isinstance(stdout, str)
    assert stdout.strip() == "Workspace FsWorkspace"
