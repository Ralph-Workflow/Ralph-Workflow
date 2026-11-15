from __future__ import annotations

import json
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
from ralph.recovery.dead_agent_verdict import (
    DeadAgentVerdict,
    probe_outcome_verdict,
    verdict_benches_agent,
)

pytestmark = pytest.mark.subprocess_e2e

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_untrusted_cwd_dialog_exit_is_inconclusive_and_benches_nothing": (
        "the untrusted-dialog path now fails open as probe_could_not_run instead of "
        "invocation_error, so the removed check asserting result.detail equals "
        "invocation_error was wrong after the change. The test now asserts result.detail "
        "equals probe_could_not_run, together with the inconclusive outcome, no marker "
        "written, the recorded error, and no marker file anywhere under the temp root"
    ),
}

_STUB = Path(__file__).resolve().parent / "fixtures" / "fake_trust_dialog_cli.py"
_PRODUCTION_PRETRUST_FLAG = "--dangerously-skip-permissions"
_PRINT_MODE_FLAGS = ("-p", "--print")
_ARGV_RECORD_ENV = "STUB_ARGV_RECORD"


@pytest.fixture(scope="module")
def stub_agent_config() -> AgentConfig:
    config = AgentConfig(
        cmd=f"{shlex.quote(sys.executable)} {shlex.quote(str(_STUB))}",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        yolo_flag=_PRODUCTION_PRETRUST_FLAG,
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
    assert result.detail == "probe_could_not_run"
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
def test_trusted_parent_with_production_pretrust_flag_writes_marker_and_passes(
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


@pytest.mark.timeout_seconds(8)
def test_probe_launch_carries_the_print_mode_flag_and_the_permission_flag(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    argv_record = tmp_path / "launched-argv.json"
    result = run_canary_probe(
        stub_agent_config,
        timeout_s=3,
        trusted_parent=trusted_root,
        extra_env=_probe_env(trusted_root, **{_ARGV_RECORD_ENV: str(argv_record)}),
    )
    assert result.outcome is ProbeOutcome.PASSED
    launched_argv = json.loads(argv_record.read_text(encoding="utf-8"))
    assert _PRODUCTION_PRETRUST_FLAG in launched_argv
    assert any(flag in launched_argv for flag in _PRINT_MODE_FLAGS)
    assert launched_argv[-1].endswith("canary_probe_prompt.md")


@pytest.mark.timeout_seconds(8)
def test_probe_without_a_documented_print_flag_is_inconclusive_and_benches_nothing(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    config = stub_agent_config.model_copy(update={"transport": AgentTransport.GENERIC})
    result = run_canary_probe(
        config,
        timeout_s=3,
        trusted_parent=trusted_root,
        extra_env=_probe_env(trusted_root),
    )
    assert result.outcome is ProbeOutcome.INCONCLUSIVE
    assert result.detail == "probe_could_not_run"
    assert result.marker_written is None
    assert list(trusted_root.rglob(CANARY_MARKER_FILENAME)) == []
    verdict = probe_outcome_verdict(result.outcome)
    assert verdict is DeadAgentVerdict.UNKNOWN
    assert verdict_benches_agent(verdict) is False


@pytest.mark.timeout_seconds(8)
def test_canary_probe_regression_missing_pretrust_flag_is_inconclusive(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    config = stub_agent_config.model_copy(update={"yolo_flag": None})
    result = run_canary_probe(
        config,
        timeout_s=3,
        trusted_parent=trusted_root,
        extra_env=_probe_env(trusted_root),
    )
    assert result.outcome is ProbeOutcome.INCONCLUSIVE
    assert result.detail == "probe_could_not_run"
    assert result.marker_written is None
    assert result.error is not None
    assert "Claude requested permissions" in result.error
    assert list(trusted_root.rglob(CANARY_MARKER_FILENAME)) == []
    verdict = probe_outcome_verdict(result.outcome)
    assert verdict is DeadAgentVerdict.UNKNOWN
    assert verdict_benches_agent(verdict) is False


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
