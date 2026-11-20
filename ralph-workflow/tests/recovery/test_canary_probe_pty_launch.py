from __future__ import annotations

import json
import shlex
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from loguru import logger

from ralph.agents.agent_activity_kind import AgentActivityKind
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
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.canary_probe import (
    CANARY_MARKER_CONTENT,
    CANARY_MARKER_FILENAME,
    PROBE_COULD_NOT_RUN_DETAIL,
    PROBE_INCONCLUSIVE_VERDICT,
    PROBE_REASON_BUDGET_EXHAUSTED,
    PROBE_REASON_INTERNAL_ERROR,
    PROBE_REASON_INVOCATION_ERROR,
    PROBE_REASON_NO_PRETRUSTED_WORKSPACE,
    PROBE_REASON_PROBE_TIMEOUT,
    PROBE_REASON_TRANSPORT_NOT_PROBE_SAFE,
    ProbeOutcome,
    ProbeResult,
    run_canary_probe,
)
from ralph.recovery.canary_probe_judge import CanaryMarkerState
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.dead_agent_probe import build_dead_agent_probe_runner
from ralph.recovery.unavailability_reason import UnavailabilityReason

pytestmark = pytest.mark.subprocess_e2e

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_untrusted_cwd_dialog_exit_is_inconclusive_and_starts_no_cooldown": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_untrusted_cwd_dialog_exit_is_inconclusive_and_benches_nothing with an unchanged body"
    ),
    "test_dialog_blocked_probe_outcome_leaves_agent_selectable_via_tracker (cooldown vocabulary)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
    "test_dialog_blocked_probe_outcome_leaves_agent_selectable_via_tracker": "operator rule: an agent is either on cooldown or not; with a one-attempt unproductive limit the charged attempt now starts an expiring unproductive_limit cooldown in the shared tracker, so the test asserts that non-dead cooldown and that the agent is selectable again once it expires",
    "test_mismatched_marker_is_never_passed_and_the_judge_fails_it": "the test no longer builds synthetic AttemptFacts and calls judge_probe itself; it asserts the real production probe result, which the PTY transport now records the agent's tool use for, so a marker file whose content is wrong is a failed_reproducing_no_work verdict with a reason naming the mismatch",
}

_STUB = Path(__file__).resolve().parent / "fixtures" / "fake_trust_dialog_cli.py"
_CORPUS_TRUST_DIALOG = (
    Path(__file__).resolve().parent / "dead_agent_corpus" / "claude_probe_trust_dialog_rc1.json"
)

_NEVER_SEEN_PROMPT_TAIL = "╭─ Vault handshake ─╮"


def _corpus_real_prompt_tail() -> str:
    payload = json.loads(_CORPUS_TRUST_DIALOG.read_text(encoding="utf-8"))
    lines = payload["captured_lines"]
    if not isinstance(lines, list) or not lines:
        raise RuntimeError(f"corpus fixture missing captured_lines: {_CORPUS_TRUST_DIALOG}")
    tail = lines[-1]
    if not isinstance(tail, str) or not tail:
        raise RuntimeError(f"corpus trust-dialog tail missing: {_CORPUS_TRUST_DIALOG}")
    return tail


_CORPUS_REAL_PROMPT_TAIL = _corpus_real_prompt_tail()

_RUNTIME_TERMINATION_REASON_PREFIX = "probe_terminated_by_runtime:"

_CANONICAL_PROBE_REASONS = frozenset(
    {
        PROBE_REASON_INVOCATION_ERROR,
        PROBE_REASON_PROBE_TIMEOUT,
        PROBE_REASON_INTERNAL_ERROR,
        PROBE_REASON_BUDGET_EXHAUSTED,
        PROBE_REASON_NO_PRETRUSTED_WORKSPACE,
        PROBE_REASON_TRANSPORT_NOT_PROBE_SAFE,
    }
)


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
def test_untrusted_cwd_dialog_exit_is_inconclusive_and_starts_no_cooldown(
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
    assert result.detail == PROBE_COULD_NOT_RUN_DETAIL
    assert result.reason == PROBE_REASON_INVOCATION_ERROR
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
    assert result.detail == PROBE_COULD_NOT_RUN_DETAIL
    assert result.reason == PROBE_REASON_PROBE_TIMEOUT
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
def test_mismatched_marker_is_never_passed_and_the_judge_fails_it(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    marker_record = tmp_path / "stub-marker.json"
    stdout_record = tmp_path / "stub-stdout.txt"
    result = run_canary_probe(
        stub_agent_config,
        timeout_s=3,
        trusted_parent=trusted_root,
        extra_env=_probe_env(
            trusted_root,
            STUB_MARKER_MODE="mismatch",
            STUB_MARKER_RECORD=str(marker_record),
            STUB_STDOUT_RECORD=str(stdout_record),
        ),
    )

    recorded = json.loads(marker_record.read_text(encoding="utf-8"))
    assert Path(recorded["path"]).name == CANARY_MARKER_FILENAME
    assert recorded["content"] != CANARY_MARKER_CONTENT, (
        "the mismatch mode must write a marker file whose content is not the canary "
        f"content; observed content: {recorded['content']!r}"
    )
    captured_stdout = stdout_record.read_text(encoding="utf-8")
    assert "claude tool:" in captured_stdout, (
        "the probe run must show the agent using a tool to write the marker file; "
        f"observed stdout: {captured_stdout!r}"
    )

    assert result.outcome is ProbeOutcome.FAILED_REPRODUCING_NO_WORK, (
        "a marker file whose content is wrong is a real negative verdict for the canary "
        f"task, whatever exit code the CLI returned; observed outcome: {result.outcome!r}"
    )
    assert result.detail == CanaryMarkerState.MISMATCHED.value
    assert result.marker_written is False
    assert "mismatch" in result.reason.lower(), (
        f"the failed verdict must name the mismatch it observed; observed reason: {result.reason!r}"
    )
    assert list(trusted_root.rglob(CANARY_MARKER_FILENAME)) == [], (
        "the probe must not leave the mismatched marker file behind"
    )


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
def test_mismatched_marker_probe_verdict_starts_an_expiring_dead_agent_cooldown(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    marker_record = tmp_path / "stub-marker.json"

    probe_results: list[ProbeResult] = []

    def probe(config: AgentConfig) -> ProbeResult:
        result = run_canary_probe(
            config,
            timeout_s=3,
            trusted_parent=trusted_root,
            extra_env=_probe_env(
                trusted_root,
                STUB_MARKER_MODE="mismatch",
                STUB_MARKER_RECORD=str(marker_record),
            ),
        )
        probe_results.append(result)
        return result

    probe_runner = build_dead_agent_probe_runner(
        lambda name: stub_agent_config if name == _AGENT_NAME else None,
        probe,
        max_probes=1,
    )
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            dead_agent_probe_runner=probe_runner,
        )
    )

    outcome = controller.finalize_attempt(
        _PROBE_PHASE,
        _AGENT_NAME,
        facts=_dead_attempt_facts(),
        attempt_failed=True,
    )

    assert len(probe_results) == 1
    assert probe_results[0].outcome is ProbeOutcome.FAILED_REPRODUCING_NO_WORK
    assert outcome.kind is AttemptOutcomeKind.AGENT_DEAD, (
        "a confirmed mismatched marker is a real dead-agent verdict; "
        f"observed outcome: {outcome.kind}"
    )
    assert outcome.starts_cooldown is True

    store = controller.unavailability_store
    reasons = store.snapshot().get("unavailability_reasons", {})
    assert reasons.get(_AGENT_NAME) == str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS), (
        "the dead-agent verdict must start the shared cooldown tracker entry; "
        f"observed tracker snapshot: {store.snapshot()!r}"
    )
    wait_ms = store.earliest_unavailable_wait_ms(_PROBE_PHASE, [_AGENT_NAME])
    assert wait_ms > 0, "the dead-agent cooldown must expire instead of skipping the agent"
    agent_status = controller.ledger.status(_PROBE_PHASE, _AGENT_NAME)
    assert isinstance(agent_status, Cooldown), (
        f"the agent must be on an expiring cooldown; observed: {agent_status!r}"
    )
    assert "spent_agents" not in controller.snapshot()

    record = controller.last_verdict_record
    assert record is not None
    assert record["probe_outcome"] == "failed_reproducing_no_work"
    assert record["probe_reason"] == CanaryMarkerState.MISMATCHED.value

    clock.advance(wait_ms / 1000 + 0.001)
    assert store.is_available(_PROBE_PHASE, _AGENT_NAME) is True, (
        "after the cooldown expires the agent must be selectable again; "
        f"observed tracker snapshot: {store.snapshot()!r}"
    )


@pytest.mark.timeout_seconds(20)
@pytest.mark.parametrize(
    ("dialog_tail", "block"),
    [
        (_NEVER_SEEN_PROMPT_TAIL, False),
        (_CORPUS_REAL_PROMPT_TAIL, True),
    ],
    ids=[
        "never_seen_exits_rc1",
        "corpus_real_blocks_forever",
    ],
)
def test_dialog_blocked_probe_outcome_leaves_agent_selectable_via_tracker(
    stub_agent_config: AgentConfig,
    tmp_path: Path,
    dialog_tail: str,
    block: bool,
) -> None:
    trusted_root = tmp_path / "trusted-root"
    trusted_root.mkdir()
    untrusted_parent = tmp_path / "untrusted-parent"
    stdout_record = tmp_path / "stub-stdout.txt"
    stdin_record = tmp_path / "stub-stdin.txt"
    extra = {
        "STUB_DIALOG_TEXT": dialog_tail,
        "STUB_STDOUT_RECORD": str(stdout_record),
        "STUB_STDIN_RECORD": str(stdin_record),
    }
    if block:
        extra["STUB_BLOCK"] = "1"

    probe_results: list[ProbeResult] = []

    def probe(config: AgentConfig) -> ProbeResult:
        result = run_canary_probe(
            config,
            timeout_s=3,
            trusted_parent=untrusted_parent,
            extra_env=_probe_env(trusted_root, **extra),
        )
        probe_results.append(result)
        return result

    probe_runner = build_dead_agent_probe_runner(
        lambda name: stub_agent_config if name == _AGENT_NAME else None,
        probe,
        max_probes=1,
    )
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            dead_agent_probe_runner=probe_runner,
            agent_max_consecutive_unproductive=1,
        )
    )

    log_records: list[str] = []
    sink_id = logger.add(log_records.append, level="INFO", format="{message}")
    try:
        outcome = controller.finalize_attempt(
            _PROBE_PHASE,
            _AGENT_NAME,
            facts=_dead_attempt_facts(),
            attempt_failed=True,
        )
    finally:
        logger.remove(sink_id)

    assert outcome.kind is AttemptOutcomeKind.UNKNOWN, (
        "a probe that could not run (blocked first-run dialog) must keep the attempt "
        f"verdict unknown, not agent_dead (observed: {outcome.kind})"
    )
    assert outcome.starts_cooldown is False
    store = controller.unavailability_store
    reasons = store.snapshot().get("unavailability_reasons", {})
    assert reasons.get(_AGENT_NAME) == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT), (
        "a probe that could not run must not bench the agent as dead; the one-attempt "
        "unproductive limit only starts an expiring cooldown; observed tracker snapshot: "
        f"{store.snapshot()!r}"
    )
    assert store.earliest_unavailable_wait_ms(_PROBE_PHASE, [_AGENT_NAME]) == 60_000
    assert "spent_agents" not in controller.snapshot()
    assert controller.false_cooldown_feedback.count == 0
    assert list(tmp_path.rglob(CANARY_MARKER_FILENAME)) == []

    assert len(probe_results) == 1
    probe_result = probe_results[0]
    assert probe_result.outcome is ProbeOutcome.INCONCLUSIVE
    assert probe_result.detail == PROBE_COULD_NOT_RUN_DETAIL, (
        "a probe that could not run must report the canonical probe_could_not_run detail; "
        f"observed: {probe_result.detail!r}"
    )
    assert probe_result.reason != "", (
        "a probe that could not run must report why it could not run; "
        f"observed detail={probe_result.detail!r} reason={probe_result.reason!r}"
    )
    assert probe_result.reason in _CANONICAL_PROBE_REASONS or probe_result.reason.startswith(
        _RUNTIME_TERMINATION_REASON_PREFIX
    ), f"the probe reason must be canonical; observed: {probe_result.reason!r}"
    assert probe_result.marker_written is None

    probe_log_lines = [line for line in log_records if line.startswith("dead-agent probe ")]
    assert len(probe_log_lines) == 1, (
        f"the probe outcome must be observable in the emitted log; observed: {probe_log_lines!r}"
    )
    assert f"detail={PROBE_COULD_NOT_RUN_DETAIL}" in probe_log_lines[0]
    assert f"reason={probe_result.reason}" in probe_log_lines[0]

    _assert_probe_could_not_run_observations(
        controller,
        stdout_record,
        stdin_record,
        dialog_tail=dialog_tail,
    )
    clock.advance(60.001)
    assert store.is_available(_PROBE_PHASE, _AGENT_NAME) is True, (
        "after the cooldown expires the agent must be selectable again; "
        f"observed tracker snapshot: {store.snapshot()!r}"
    )
    assert controller.ledger.status(_PROBE_PHASE, _AGENT_NAME).is_eligible


def _assert_probe_could_not_run_observations(
    controller: RecoveryController,
    stdout_record: Path,
    stdin_record: Path,
    *,
    dialog_tail: str,
) -> None:
    captured_stdout = stdout_record.read_text(encoding="utf-8")
    assert captured_stdout == f"{dialog_tail}\n", (
        "the blocked CLI must emit exactly the requested prompt tail and nothing else; "
        f"observed: {captured_stdout!r}"
    )
    captured_stdin = stdin_record.read_text(encoding="utf-8") if stdin_record.exists() else ""
    if dialog_tail == _NEVER_SEEN_PROMPT_TAIL:
        assert captured_stdin == "", (
            "an arbitrary never-seen prompt wording must match no prompt-phrase list in the "
            f"tree, so nothing may answer it; observed input: {captured_stdin!r}"
        )
    else:
        assert captured_stdin != "", (
            "the corpus-real trust-dialog wording must reach the production permission "
            f"auto-approval surface and receive Enter; observed input: {captured_stdin!r}"
        )
        assert all(character in "\r\n" for character in captured_stdin), (
            "the production auto-approval response must contain only Enter-key bytes; "
            f"observed input: {captured_stdin!r}"
        )

    parser = ClaudeInteractiveParser()
    parsed_lines = list(parser.parse(iter([captured_stdout])))
    assert parser.session_id is None, (
        f"the blocked CLI emitted a session event; observed: {parser.session_id!r}"
    )
    if dialog_tail == _NEVER_SEEN_PROMPT_TAIL:
        assert parsed_lines == [], (
            "the blocked CLI emitted model output; observed: "
            f"{[(line.type, line.content) for line in parsed_lines]!r}"
        )
    else:
        assert [(line.type, line.content) for line in parsed_lines] == [("text", dialog_tail)], (
            "the corpus-real trust-dialog wording must remain visible as the captured "
            "provider text; observed: "
            f"{[(line.type, line.content) for line in parsed_lines]!r}"
        )
    activity = ClaudeInteractiveExecutionStrategy().classify_activity_line(captured_stdout)
    assert activity is not None
    assert activity.kind is not AgentActivityKind.STREAM_DELTA, (
        f"the blocked CLI emitted a model token stream; observed: {activity.kind!r}"
    )
    assert activity.kind is not AgentActivityKind.LIFECYCLE, (
        f"the blocked CLI emitted a session event; observed: {activity.kind!r}"
    )

    agent_status = controller.ledger.status(_PROBE_PHASE, _AGENT_NAME)
    assert controller.ledger.registered_agents(_PROBE_PHASE) == (_AGENT_NAME,), (
        "a probe that could not run must be charged by the ledger like any other failed "
        "attempt, so the ledger must hold exactly one entry, for that agent; observed: "
        f"{controller.ledger.registered_agents(_PROBE_PHASE)!r}"
    )
    assert isinstance(agent_status, Cooldown), (
        f"the charged agent must be on an expiring cooldown; observed: {agent_status!r}"
    )
    assert agent_status.reason == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)
