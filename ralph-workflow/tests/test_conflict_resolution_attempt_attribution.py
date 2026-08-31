"""B1-B6 continued: one attempt's verdict must never answer for another's.

Split from ``test_conflict_resolution_phase_parity`` so neither file
exceeds the repo-structure size cap. Where that file pins the CHAIN --
that every candidate gets its turn, in order, with its own retry budget
-- this one pins ATTRIBUTION: a reason belongs to the attempt that
earned it. A stop must not inherit the previous stop's verdict, a
candidate that never launched must not be recorded as declining, an
agent that ran out of context must not be filed as refusing the work,
and a missing agent must be named rather than failing anonymously.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    classify_failed_resolution_attempt,
    invoke_resolution_agent,
)
from tests._conflict_resolution_phase_parity_seams import (
    _CONFLICTED,
    _config,
    _install_seams,
    _policy_bundle,
)

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle


def test_a_stop_never_inherits_the_previous_stops_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An early exit must not report a reason left behind by an earlier conflict."""
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    session = ResolutionSession()
    outcome = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=_config(),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda *_args: False,
        session=session,
    )
    assert outcome.reason is ResolutionTerminationReason.ATTEMPT_FAILED

    # Second stop: nothing is conflicted, so no resolver runs at all.
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: [])
    quiet = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=_config(),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda *_args: False,
        session=session,
    )
    assert quiet.reason is not ResolutionTerminationReason.ATTEMPT_FAILED
    assert session.terminal_reason is None


def test_one_stop_cannot_exceed_its_configured_invocation_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rounds re-spend the chain, so pin the ceiling that bounds the cost."""
    from ralph.pipeline.conflict_resolution.session import conflict_chain_max_retries

    chain = ("one", "two", "three")
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: chain)
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch)
    called: list[tuple[int, str]] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append((round_index, agent_name))
        return False

    config = _config()
    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=config,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
        )
        is False
    )
    # Declines earn no same-agent retry, so the count is exact: every
    # round spends the chain once and nothing spends it twice. WHICH
    # candidate leads a round rotates -- the next round starts after the
    # last one tried -- so the property is per-round coverage, not order.
    rounds = config.conflict_resolution.max_rounds_per_stop
    assert len(called) == rounds * len(chain)
    for round_index in range(1, rounds + 1):
        spent = [agent for index, agent in called if index == round_index]
        assert sorted(spent) == sorted(chain), f"round {round_index} spent {spent}"
    assert len(called) <= (
        rounds * len(chain) * max(1, conflict_chain_max_retries(_policy_bundle()))
    )


def test_a_failed_attempt_is_charged_to_recovery_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two verdicts on one failure spent the same-agent retry budget instantly.

    The default invoker records the failure and the driver classifies
    it; classifying in both places charged RecoveryController twice for
    one attempt, so a transient provider error failed over to the next
    candidate rather than retrying the agent it just lost.
    """
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("provider unavailable")),
    )
    charged: list[str] = []
    original = classify_failed_resolution_attempt

    def _spy(
        session: ResolutionSession | None,
        agent_name: str,
        raw_failure: BaseException | str,
        *,
        candidates: tuple[str, ...] = (),
        failed_index: int = 0,
        policy_bundle: PolicyBundle | None = None,
    ) -> None:
        charged.append(agent_name)
        original(
            session,
            agent_name,
            raw_failure,
            candidates=candidates,
            failed_index=failed_index,
            policy_bundle=policy_bundle,
        )

    monkeypatch.setattr(driver_module, "classify_failed_resolution_attempt", _spy)
    session = ResolutionSession(max_rounds_per_stop=1)

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            session=session,
        )
        is False
    )
    # 'one' is retried on a transient failure before the chain falls over,
    # which only happens while each attempt is charged once.
    assert charged.count("one") > 1
    assert "two" in charged


def test_one_candidates_exit_reason_is_not_pinned_on_the_next_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The executor parks a retry intent on failure and clears it only on success.

    A candidate that failed through an exception path therefore leaves
    its intent behind; the next candidate's plain failure used to be
    reported with that reason and to inherit its skip_same_agent_retries,
    spending a healthy agent's retry budget on somebody else's exit.
    """
    from ralph.pipeline import effect_executor as effect_executor_module
    from ralph.pipeline.agent_retry_intent import AgentRetryIntent
    from ralph.pipeline.events import PipelineEvent

    effect_executor_module._set_last_captured_retry_intent(
        AgentRetryIntent(
            failure_reason="PiProviderFailureExitError",
            skip_same_agent_retries=True,
            failed_agent_name="pi",
        )
    )
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        lambda *args, **kwargs: PipelineEvent.AGENT_FAILURE,
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    session = ResolutionSession()

    assert (
        invoke_resolution_agent(
            agent_name="claude",
            prompt_path=prompt,
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            session=session,
        )
        is False
    )
    assert session.last_attempt_failure == "conflict attempt failed"
    assert session.last_attempt_evidence != "PiProviderFailureExitError"
    assert session.skip_same_agent_retries is False


def test_work_that_ran_and_did_not_finish_is_incomplete_not_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A successful invocation that leaves markers has not answered anything.

    No agent can refuse this work -- the session has no tool for it --
    so a round that came back successful with markers still on disk is
    unfinished work, and the next round hands the same agent's successor
    the paths that still carry markers.
    """
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    lines: list[str] = []
    monkeypatch.setattr(
        driver_module, "emit_conflict_phase_line", lambda _display, line: lines.append(line)
    )
    session = ResolutionSession()

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=lambda *_args: True,
            session=session,
        )
        is False
    )
    assert any(
        line.startswith(ResolutionTerminationReason.RESOLUTION_INCOMPLETE.value) for line in lines
    )
    assert not any(
        line.startswith(ResolutionTerminationReason.ATTEMPT_FAILED.value) for line in lines
    )


def test_no_reason_the_pipeline_can_report_is_an_agent_refusing_the_work() -> None:
    """The enum must not offer a state that means "the resolver said no".

    There is no MCP tool with which a resolution session could refuse a
    conflict: ``declare_complete`` is its only completion signal. A
    reason named for a refusal therefore described something that cannot
    happen, while hiding what did -- an attempt that failed, a candidate
    that never started, or work that did not finish.
    """
    values = {reason.value for reason in ResolutionTerminationReason}
    assert not any("DECLIN" in value or "REFUS" in value for value in values)
    assert {
        "ATTEMPT_FAILED",
        "RESOLUTION_INCOMPLETE",
        "CANDIDATE_EXITED",
        "CANDIDATE_UNAVAILABLE",
    } <= values


def test_an_unavailable_candidate_does_not_answer_for_the_one_after_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A misconfigured candidate must not own the stop's verdict or its budget.

    A chain naming an agent this workspace does not have is ordinary. When
    the healthy candidate after it really ran and really failed, the round
    reported the GHOST's fault and left the stop unchargeable -- and an
    unchargeable stop records no exhaustion evidence at all, so the run
    could never escalate honestly.
    """
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("ghost", "healthy")
    )
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    session = ResolutionSession(max_rounds_per_stop=1)
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        if agent_name == "ghost":
            session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
        else:
            session.last_attempt_saw_activity = True
        return False

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
            session=session,
        )
        is False
    )
    assert called == ["ghost", "healthy"]
    assert "ghost" in session.dead_tool_surfaces
    assert "healthy" not in session.dead_tool_surfaces
    assert session.terminal_reason is ResolutionTerminationReason.ATTEMPT_FAILED
    assert session.charge_conflict_budget is True
    assert session.exhaustion_reason is not None
    assert session.exhaustion_reason.startswith(
        ResolutionTerminationReason.ATTEMPT_FAILED.value
    )


def test_the_executor_names_a_missing_agent_instead_of_failing_anonymously(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The registry lookup lives in the executor; only it should do it.

    Duplicating the check in conflict resolution meant building an
    AgentRegistry per candidate, and building one spawns subprocesses --
    a process spawn in front of every resolution attempt. The executor
    already knows, so it says so on the channel callers already read.
    """
    from ralph.pipeline import effect_executor as effect_executor_module

    assert effect_executor_module.AGENT_NOT_FOUND_REASON == "AgentNotFound"
    source = Path(effect_executor_module.__file__).read_text(encoding="utf-8")
    marker = "if agent_config is None:"
    assert marker in source
    branch = source[source.index(marker) : source.index(marker) + 900]
    assert "AGENT_NOT_FOUND_REASON" in branch, (
        "the agent-not-found branch must name the cause on the retry intent"
    )
    assert "skip_same_agent_retries=True" in branch, (
        "retrying a name the registry cannot produce is futile"
    )
