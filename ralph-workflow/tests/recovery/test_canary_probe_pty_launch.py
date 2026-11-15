from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state.claude_interactive_execution_strategy import (
    ClaudeInteractiveExecutionStrategy,
)
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.recovery.canary_probe import (
    CANARY_MARKER_FILENAME,
    PROBE_INCONCLUSIVE_VERDICT,
    ProbeOutcome,
    run_canary_probe,
)

pytestmark = pytest.mark.subprocess_e2e

_STUB = Path(__file__).resolve().parent / "fixtures" / "fake_trust_dialog_cli.py"


@pytest.fixture(scope="module")
def stub_agent_config() -> AgentConfig:
    config = AgentConfig(
        cmd=f"{shlex.quote(sys.executable)} {shlex.quote(str(_STUB))}",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    support = AgentSupport(
        name="fake-trust-dialog-cli",
        spec=AgentSpec(
            name="fake-trust-dialog-cli",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            interactive=True,
            requires_pty=True,
        ),
        parser_factory=ClaudeInteractiveParser,
        strategy_factory=ClaudeInteractiveExecutionStrategy,
        config=config,
    )
    catalog = default_catalog()
    if catalog.get("fake-trust-dialog-cli") is None:
        catalog.add(support)
    return config


def _probe_env(trusted_root: Path, **extra: str) -> dict[str, str]:
    return {
        "ANTHROPIC_API_KEY": "stub-key",
        "STUB_TRUSTED_ROOT": str(trusted_root),
        **extra,
    }


@pytest.mark.timeout_seconds(8)
def test_untrusted_cwd_dialog_exit_is_inconclusive_and_benches_nothing(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    untrusted_parent = tmp_path / "untrusted-parent"
    result = run_canary_probe(
        stub_agent_config,
        timeout_s=3,
        trusted_parent=untrusted_parent,
        extra_env=_probe_env(trusted_root),
    )
    assert result.outcome is ProbeOutcome.INCONCLUSIVE
    assert result.detail == "invocation_error"
    assert result.marker_written is None
    assert result.error is not None
    assert PROBE_INCONCLUSIVE_VERDICT == "unknown"
    assert list(tmp_path.rglob(CANARY_MARKER_FILENAME)) == []


@pytest.mark.timeout_seconds(8)
def test_untrusted_cwd_never_exiting_dialog_times_out(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    untrusted_parent = tmp_path / "untrusted-parent"
    result = run_canary_probe(
        stub_agent_config,
        timeout_s=3,
        trusted_parent=untrusted_parent,
        extra_env=_probe_env(trusted_root, STUB_BLOCK="1"),
    )
    assert result.outcome is ProbeOutcome.INCONCLUSIVE
    assert result.detail == "probe_timeout"
    assert result.marker_written is None
    assert list(tmp_path.rglob(CANARY_MARKER_FILENAME)) == []


@pytest.mark.timeout_seconds(8)
def test_trusted_parent_cwd_writes_marker_and_passes(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    result = run_canary_probe(
        stub_agent_config,
        timeout_s=3,
        trusted_parent=trusted_root,
        extra_env=_probe_env(trusted_root),
    )
    assert result.outcome is ProbeOutcome.PASSED
    assert result.detail == "marker_written"
    assert result.marker_written is True
    assert result.error is None
    probe_area = trusted_root / ".agent" / "tmp"
    assert probe_area.exists()
    assert list(probe_area.glob("canary-probe-*")) == []
    assert list(trusted_root.rglob(CANARY_MARKER_FILENAME)) == []


def test_stub_refuses_pipe_route(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_STUB)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=5,
        check=False,
    )
    assert proc.returncode == 2
