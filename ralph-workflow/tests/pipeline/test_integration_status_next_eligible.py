from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import build_integration_status
from ralph.pipeline._integration_status import cooldown_next_eligible
from ralph.pipeline.conflict_resolution._driver_events import (
    emit_attempt_outcome,
    reset_driver_events_state,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.resolution_outcome import NextStrategyOutcome, WaitingOutcome
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.policy.models import PolicyBundle

_RESOLUTION_PHASE = "rebase_conflict_resolution"
_EPOCH_2026_01_01 = 1_767_225_600.0


class _RecordingDisplay(ParallelDisplay):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def emit_warn_line(self, unit_id: str, tag: str, message: str) -> None:
        self.lines.append(message)


def _policy_bundle() -> PolicyBundle:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent")


def _controller_with_cooldown(
    *,
    agents: Sequence[str],
    cooldown_until_ms: dict[str, int],
) -> RecoveryController:
    clock = FakeClock(start=0.0, epoch_start=_EPOCH_2026_01_01)
    entries = {
        agent: UnavailabilityEntry(
            unavailable_until_ms=cooldown_until_ms[agent],
            reason=UnavailabilityReason.OUT_OF_CREDITS,
            attempt=0,
            base_backoff_ms=60_000,
            max_backoff_ms=1_800_000,
        )
        for agent in cooldown_until_ms
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_policy_bundle(),
            event_bus=FailureEventBus(),
            unavailability_entries=entries,
        ),
    )
    controller.register_phase_chain(_RESOLUTION_PHASE, tuple(agents))
    return controller


def _cooling_controller() -> RecoveryController:
    return _controller_with_cooldown(
        agents=("claude", "opencode"),
        cooldown_until_ms={"claude": 5_000, "opencode": 9_000},
    )


def test_the_waiting_status_names_the_next_eligible_agent_and_the_expiry() -> None:
    controller = _cooling_controller()

    agent, until_when = cooldown_next_eligible(
        controller,
        _RESOLUTION_PHASE,
        ("claude", "opencode"),
    )

    assert agent == "claude"
    assert until_when == "2026-01-01T00:00:05+00:00"

    status = build_integration_status(
        cause="WAITING: all resolver agents are on cooldown",
        agents_can_finish=False,
        agent_reasons=("claude: out_of_credits", "opencode: out_of_credits"),
        next_eligible_agent=agent,
        until_when=until_when,
    )

    rendered = status.render_log
    assert "next-eligible: claude" in rendered
    assert "until-when: 2026-01-01T00:00:05+00:00" in rendered


def test_an_available_agent_is_reported_as_eligible_now() -> None:
    controller = _controller_with_cooldown(agents=("claude",), cooldown_until_ms={})

    agent, until_when = cooldown_next_eligible(controller, _RESOLUTION_PHASE, ("claude",))

    assert agent == "claude"
    assert until_when == "now"


def test_an_unchanged_waiting_condition_emits_exactly_one_line_across_two_ticks() -> None:
    reset_driver_events_state()
    display = _RecordingDisplay()
    session = ResolutionSession(recovery_controller=_cooling_controller())
    session.unresolved_paths = ("src/alpha.py",)
    outcome = WaitingOutcome(
        external_condition="all resolver agents are on cooldown",
        resumes_when="the earliest resolver cooldown expires",
    )

    emit_attempt_outcome(display, outcome, session=session)
    emit_attempt_outcome(display, outcome, session=session)

    assert len(display.lines) == 1, display.lines
    assert "all resolver agents are on cooldown" in display.lines[0]
    assert "next-eligible: claude" in display.lines[0]


def test_a_changed_waiting_condition_emits_a_new_line() -> None:
    reset_driver_events_state()
    display = _RecordingDisplay()
    session = ResolutionSession(recovery_controller=_cooling_controller())
    session.unresolved_paths = ("src/alpha.py",)

    emit_attempt_outcome(
        display,
        WaitingOutcome(
            external_condition="all resolver agents are on cooldown",
            resumes_when="the earliest resolver cooldown expires",
        ),
        session=session,
    )
    emit_attempt_outcome(
        display,
        WaitingOutcome(
            external_condition="the target branch moved while the conflict was open",
            resumes_when="the target branch moves again",
        ),
        session=session,
    )

    assert len(display.lines) == 2, display.lines
    assert "the target branch moved while the conflict was open" in display.lines[1]


def test_the_next_strategy_line_carries_the_shared_model_fields() -> None:
    reset_driver_events_state()
    display = _RecordingDisplay()
    session = ResolutionSession(recovery_controller=_cooling_controller())
    session.unresolved_paths = ("src/alpha.py",)

    emit_attempt_outcome(
        display,
        NextStrategyOutcome(strategy="candidate_unavailable"),
        session=session,
    )

    assert len(display.lines) == 1, display.lines
    line = display.lines[0]
    assert "candidate_unavailable" in line
    assert "git add -- src/alpha.py" in line
    assert "next-eligible: claude" in line
    assert "until-when: 2026-01-01T00:00:05+00:00" in line


def test_the_waiting_line_shows_the_cause_and_the_fix_command_once() -> None:
    reset_driver_events_state()
    display = _RecordingDisplay()
    session = ResolutionSession(recovery_controller=_cooling_controller())
    session.unresolved_paths = ("src/alpha.py", "docs/beta.md")

    emit_attempt_outcome(
        display,
        WaitingOutcome(
            external_condition="all resolver agents are on cooldown",
            resumes_when="the earliest resolver cooldown expires",
        ),
        session=session,
    )

    assert len(display.lines) == 1, display.lines
    line = display.lines[0]
    cause = (
        "WAITING: all resolver agents are on cooldown; "
        "resumes_when=the earliest resolver cooldown expires"
    )
    assert line.count(cause) == 1, line
    assert line.count("git add -- src/alpha.py docs/beta.md") == 1, line


def test_a_status_with_an_available_agent_does_not_claim_agents_cannot_finish() -> None:
    status = build_integration_status(
        cause="WAITING: the target branch moved while the conflict was open",
        paths_with_reasons=(("src/alpha.py", "unresolved conflict"),),
        agents_can_finish=False,
        next_eligible_agent="claude",
        until_when="now",
    )

    assert "agents cannot finish this" not in status.summary
    assert "next-eligible: claude" in status.summary
    assert "until-when: now" in status.summary


def test_the_status_change_key_covers_the_waiting_identity() -> None:
    first = build_integration_status(
        cause="WAITING: all resolver agents are on cooldown",
        next_eligible_agent="claude",
        until_when="now",
    )
    same = build_integration_status(
        cause="WAITING: all resolver agents are on cooldown",
        next_eligible_agent="claude",
        until_when="now",
    )
    changed = build_integration_status(
        cause="WAITING: all resolver agents are on cooldown",
        next_eligible_agent="opencode",
        until_when="now",
    )

    assert first.change_key == same.change_key
    assert first.change_key != changed.change_key


def test_the_status_carries_the_preserved_artifacts() -> None:
    status = build_integration_status(
        cause="WAITING: integration has not landed; recovery will retry",
        preserved=("refs/ralph/preserved-copy",),
    )

    assert "preserved: refs/ralph/preserved-copy" in status.summary
    facts = {str(label): str(value) for label, value in status.diagnose_rows()}
    assert facts["Preserved artifacts"] == "refs/ralph/preserved-copy"


def _emitted_levels(
    *,
    label: str,
    returncode: int,
    command: tuple[str, ...] = ("git", "symbolic-ref", "--quiet", "HEAD"),
) -> list[str]:
    from ralph.process.manager import (
        ProcessEvent,
        ProcessRecord,
        ProcessStatus,
        loguru_event_listener,
    )

    levels: list[str] = []
    sink_id = logger.add(
        lambda message: levels.append(str(message).strip()),
        level="DEBUG",
        format="{level}",
    )
    try:
        record = ProcessRecord(
            pid=4242,
            pgid=4242,
            command=command,
            cwd=None,
            started_at=datetime.now(tz=UTC),
            status=ProcessStatus.EXITED,
            returncode=returncode,
            label=label,
        )
        loguru_event_listener(
            ProcessEvent(
                record=record,
                previous_status=ProcessStatus.RUNNING,
                new_status=ProcessStatus.EXITED,
                timestamp=datetime.now(tz=UTC),
            )
        )
    finally:
        logger.remove(sink_id)
    return levels


def test_a_benign_probe_exit_code_is_debug_and_an_unexpected_exit_code_warns() -> None:
    assert _emitted_levels(label="git-symbolic-ref", returncode=1) == ["DEBUG"]
    assert _emitted_levels(label="git-symbolic-ref", returncode=2) == ["WARNING"]


def test_the_symbolic_ref_probe_class_stays_quiet_under_a_shared_probe_label() -> None:
    levels = _emitted_levels(
        label="pending-merge:identity",
        returncode=1,
        command=("git", "symbolic-ref", "--quiet", "--short", "HEAD"),
    )

    assert levels == ["DEBUG"], levels


def test_the_merge_base_ancestry_probe_class_stays_quiet_under_a_shared_probe_label() -> None:
    levels = _emitted_levels(
        label="pending-merge:identity",
        returncode=1,
        command=("git", "merge-base", "--is-ancestor", "main", "HEAD"),
    )

    assert levels == ["DEBUG"], levels


def test_the_absent_merge_head_probe_class_stays_quiet_under_a_shared_probe_label() -> None:
    levels = _emitted_levels(
        label="pending-merge:identity",
        returncode=1,
        command=("git", "rev-parse", "--verify", "--quiet", "MERGE_HEAD"),
    )

    assert levels == ["DEBUG"], levels


def test_exit_two_stays_a_warning_for_every_benign_probe_class() -> None:
    for command in (
        ("git", "symbolic-ref", "--quiet", "HEAD"),
        ("git", "merge-base", "--is-ancestor", "main", "HEAD"),
        ("git", "rev-parse", "--verify", "--quiet", "MERGE_HEAD"),
    ):
        levels = _emitted_levels(label="pending-merge:identity", returncode=2, command=command)
        assert levels == ["WARNING"], (command, levels)


def test_an_unregistered_label_keeps_the_loud_default_for_a_benign_probe_command() -> None:
    levels = _emitted_levels(label="executor:run-process", returncode=1)

    assert levels == ["WARNING"], levels
