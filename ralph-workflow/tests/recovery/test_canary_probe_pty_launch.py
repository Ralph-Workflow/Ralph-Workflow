from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state.claude_interactive_execution_strategy import (
    ClaudeInteractiveExecutionStrategy,
)
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.agents.timeout_clock import FakeClock
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.canary_probe import (
    CANARY_MARKER_FILENAME,
    PROBE_INCONCLUSIVE_VERDICT,
    ProbeOutcome,
    ProbeResult,
    run_canary_probe,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.dead_agent_probe import build_dead_agent_probe_runner

pytestmark = pytest.mark.subprocess_e2e

_STUB = Path(__file__).resolve().parent / "fixtures" / "fake_trust_dialog_cli.py"


def _load_stub_dialog_line() -> str:
    spec = importlib.util.spec_from_file_location("fake_trust_dialog_cli", _STUB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dialog = getattr(module, "DIALOG_LINE", None)
    assert isinstance(dialog, str) and dialog
    return dialog


_DIALOG_LINE = _load_stub_dialog_line()


@pytest.fixture(scope="module")
def stub_agent_config() -> Iterator[AgentConfig]:
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
    added = False
    if catalog.get("fake-trust-dialog-cli") is None:
        catalog.add(support)
        added = True
    try:
        yield config
    finally:
        if added:
            catalog.remove("fake-trust-dialog-cli")


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
    assert _DIALOG_LINE in result.error
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


@pytest.mark.timeout_seconds(8)
def test_production_probe_launch_is_non_interactive_and_pre_trusted(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    argv_record = tmp_path / "stub-argv.json"
    result = run_canary_probe(
        stub_agent_config,
        timeout_s=3,
        trusted_parent=trusted_root,
        extra_env=_probe_env(trusted_root, STUB_ARGV_RECORD=str(argv_record)),
    )
    assert result.outcome is ProbeOutcome.PASSED
    assert result.detail == "marker_written"
    argv = json.loads(argv_record.read_text(encoding="utf-8"))
    assert "-p" in argv, (
        "the production probe launch must run the CLI non-interactively; "
        f"the spawned argv was: {argv!r}"
    )


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


_PROBE_PHASE = "development"
_AGENT_NAME = "fake-trust-dialog-cli"


def _dead_attempt_facts() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=("error",),
        artifact_valid=False,
        workspace_changed=False,
        exit_status="exit_1",
        liveness_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=1,
            reported_returncode=1,
            liveness_observed=False,
        ),
    )


@pytest.mark.timeout_seconds(20)
@pytest.mark.parametrize("block", [True, False], ids=["blocks_forever", "exits_rc1"])
def test_dialog_blocked_probe_outcome_leaves_agent_selectable_via_tracker(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
    block: bool,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    untrusted_parent = tmp_path / "untrusted-parent"
    extra = {"STUB_BLOCK": "1"} if block else {}

    def probe(config: AgentConfig) -> ProbeResult:
        return run_canary_probe(
            config,
            timeout_s=3,
            trusted_parent=untrusted_parent,
            extra_env=_probe_env(trusted_root, **extra),
        )

    probe_runner = build_dead_agent_probe_runner(
        lambda name: stub_agent_config if name == _AGENT_NAME else None,
        probe,
        max_probes=1,
    )
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            dead_agent_probe_runner=probe_runner,
            agent_max_consecutive_unproductive=3,
        )
    )

    outcome = controller.finalize_attempt(
        _PROBE_PHASE,
        _AGENT_NAME,
        facts=_dead_attempt_facts(),
        attempt_failed=True,
    )

    assert outcome.kind is AttemptOutcomeKind.UNKNOWN, (
        "a probe that could not run (blocked first-run dialog) must keep the attempt "
        f"verdict unknown, not agent_dead (observed: {outcome.kind})"
    )
    assert outcome.benches_agent is False
    store = controller.unavailability_store
    assert store.is_available(_PROBE_PHASE, _AGENT_NAME) is True, (
        "after a probe that could not run, the agent must be selectable again; "
        f"observed tracker snapshot: {store.snapshot()!r}"
    )
    assert store.earliest_unavailable_wait_ms(_PROBE_PHASE, [_AGENT_NAME]) == 0
    reasons = store.snapshot().get("unavailability_reasons", {})
    assert _AGENT_NAME not in reasons
    eligibility = controller.agent_eligibility(_PROBE_PHASE, _AGENT_NAME)
    assert eligibility.is_eligible is True, (
        "controller-level eligibility must keep the agent selectable after an "
        f"inconclusive probe (observed: {eligibility!r})"
    )
    assert controller.preferred_agent_index(_PROBE_PHASE, [_AGENT_NAME]).agent == _AGENT_NAME, (
        "preferred_agent_index must still select the agent after an inconclusive probe"
    )
    ledger_status = controller.ledger.status(_PROBE_PHASE, _AGENT_NAME)
    assert ledger_status.is_spent is False
    assert ledger_status.is_eligible is True
    ledger_entry = controller.snapshot()["ledger"]["entries"][f"{_PROBE_PHASE}:{_AGENT_NAME}"]
    assert ledger_entry["consecutive_unproductive"] == 1
    assert ledger_entry["total_unproductive"] == 1
    assert ledger_entry["is_spent"] is False
    assert ledger_entry["cooldown_until_ms"] is None
    assert controller.snapshot()["spent_agents"] == {}
    assert controller.false_bench_feedback.count == 0
    assert list(tmp_path.rglob(CANARY_MARKER_FILENAME)) == []
