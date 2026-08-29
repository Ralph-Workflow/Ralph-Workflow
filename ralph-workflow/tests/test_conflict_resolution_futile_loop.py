"""B7-B15: a broken tool surface must fail the attempt on evidence, not a clock."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    classify_ralph_origin_fault,
    ralph_origin_counts_as_liveness,
)
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.conflict_resolution.status import ResolutionStatusReporter
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["a.py", "b.py", "c.py"]


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str] = _CONFLICTED,
) -> None:
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(unmerged))
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda root, paths: list(unmerged),
    )
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("primary", "fallback")
    )


def test_transport_loop_detected_is_a_typed_attempt_failure() -> None:
    reason = classify_ralph_origin_fault("HTTP 503: transport_loop_detected")
    assert reason is ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
    assert ralph_origin_counts_as_liveness("transport_loop_detected") is False


def test_supervision_relay_error_is_a_typed_attempt_failure() -> None:
    payload = "SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay sender: timed out"
    reason = classify_ralph_origin_fault(payload)
    assert reason is ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
    assert ralph_origin_counts_as_liveness(payload) is False


def test_dead_tool_surface_fails_fast_and_hands_over(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch)
    session = ResolutionSession()
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        if agent_name == "primary":
            session.terminal_reason = classify_ralph_origin_fault("transport_loop_detected")
            session.charge_conflict_budget = False
            session.dead_tool_surfaces = (*session.dead_tool_surfaces, agent_name)
            return False
        return False

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=UnifiedConfig.model_validate({"general": {}}),
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
    assert called[0] == "primary"
    assert "fallback" in called
    assert called.count("primary") == 1
    # The transport loop is recorded against the candidate that produced
    # it. The round's REASON is the last attempt's own -- carrying an
    # earlier candidate's fault forward reported a healthy agent's
    # genuine failure under somebody else's fault, and made the whole
    # stop unchargeable, which discarded its exhaustion evidence.
    assert session.dead_tool_surfaces == ("primary",)
    assert session.terminal_reason is ResolutionTerminationReason.ATTEMPT_FAILED
    assert session.charge_conflict_budget is True


def test_known_dead_surface_is_not_reentered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch)
    session = ResolutionSession(dead_tool_surfaces=("primary",))
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        return False

    run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=_invoke,
        session=session,
    )
    # The dead surface is never re-entered; the live candidate still gets
    # every round, because a round that invoked nobody was the bug.
    assert "primary" not in called
    assert set(called) == {"fallback"}


def test_status_does_not_report_health_from_ralph_fault_text() -> None:
    reporter = ResolutionStatusReporter(
        display=None,
        target="main",
        round_index=1,
        round_cap=3,
        stop_index=None,
        stop_cap=None,
        clock=lambda: 1.0,
        interval_seconds=0.0,
        started_at=0.0,
        unresolved_paths=tuple(_CONFLICTED),
        agent_name="primary",
    )

    event = type("_Event", (), {"diagnostic": {
        "last_activity_kind": "transport_loop_detected",
        "last_activity_age_seconds": 0.56,
    }})()
    reporter.observe(event)
    assert reporter._last_emitted_at is None


def test_agent_prose_about_tool_surfaces_is_not_a_ralph_fault() -> None:
    """A resolver quoting this repo's own words must not be recorded as dead.

    The payload scanned here carries the agent's own activity text, and a
    match kills that agent for the rest of the rebase. Ralph emits no
    fault containing these phrases, so matching them could only ever
    punish an agent for reading the code it was asked to fix.
    """
    assert classify_ralph_origin_fault("edited driver.py: the MCP tool surface is fine") is None
    assert classify_ralph_origin_fault("the tool service comment explains the seam") is None
    assert classify_ralph_origin_fault("noted repeated identical tool calls in a docstring") is None


def test_real_ralph_fault_tokens_are_still_classified() -> None:
    """Narrowing the prose must not cost the markers Ralph actually writes."""
    assert (
        classify_ralph_origin_fault("HTTP 503: transport_loop_detected")
        is ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
    )
    assert (
        classify_ralph_origin_fault("SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay sender: x")
        is ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
    )


def test_a_resolver_quoting_ralphs_own_fault_tokens_survives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The markers live in Ralph's source, so a resolver reading it echoes them.

    The conflict being resolved is frequently in this repository, where
    ``transport_loop_detected`` and ``SUPERVISION_INFRASTRUCTURE_FAILURE``
    are ordinary source tokens. Scanning the agent's own progress text
    for them killed the only resolver in the shipped one-agent chain for
    quoting a line it had just read.
    """
    from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusEvent
    from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind
    from ralph.pipeline.conflict_resolution.session import _wrap_activity_listener

    session = ResolutionSession()
    listener = _wrap_activity_listener(None, session, agent_name="claude")
    assert listener is not None
    for echoed in (
        'read attempt_fault.py: _TRANSPORT_LOOP = "transport_loop_detected"',
        "grep: transport_loop_detected (3 matches)",
        'edit: SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay sender: {exc}',
    ):
        listener(
            WaitingStatusEvent(
                kind=next(iter(WaitingStatusKind)),
                cumulative_seconds=1.0,
                current_run_seconds=1.0,
                idle_elapsed_seconds=1.0,
                ceiling_seconds=900.0,
                suspect_threshold_seconds=None,
                diagnostic={"last_activity_kind": "mcp_tool", "current_subagent_tool_call": echoed},
                subagent_activity=echoed,
                stall_active=False,
            )
        )
    assert session.dead_tool_surfaces == ()
    assert session.terminal_reason is None


def test_a_watchdog_authored_fault_still_bars_the_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Narrowing to Ralph-authored fields must not disarm the detection.

    Corroboration applies to this channel too: a tripped MCP breaker
    answers every tool call, so a real fault clears the threshold at
    once -- while a single tick no longer bars an agent, and no longer
    discards a conflict that agent had already resolved.
    """
    from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusEvent
    from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind
    from ralph.pipeline.conflict_resolution.session import (
        _RALPH_FAULT_ESCALATION_HITS,
        _wrap_activity_listener,
    )

    session = ResolutionSession()
    listener = _wrap_activity_listener(None, session, agent_name="claude")
    assert listener is not None
    faulting = WaitingStatusEvent(
        kind=next(iter(WaitingStatusKind)),
        cumulative_seconds=1.0,
        current_run_seconds=1.0,
        idle_elapsed_seconds=1.0,
        ceiling_seconds=900.0,
        suspect_threshold_seconds=None,
        diagnostic={"last_activity_kind": "transport_loop_detected"},
        subagent_activity="working",
        stall_active=False,
    )
    listener(faulting)
    assert session.stop_dead_surfaces == (), "one tick is not a barred agent"
    assert session.last_attempt_saw_activity is True, "a fault tick is still activity"

    for _ in range(_RALPH_FAULT_ESCALATION_HITS):
        listener(faulting)
    assert session.stop_dead_surfaces == ("claude",)
    assert session.dead_tool_surfaces == (), "a plumbing fault is never run-scoped"
    assert session.terminal_reason is ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED


def test_an_unspendable_chain_reports_tool_surface_dead_not_exhaustion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reason must reach the outcome, not only the console line."""
    _install_seams(monkeypatch)
    session = ResolutionSession(dead_tool_surfaces=("primary", "fallback"))
    outcome = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda *_args: True,
        session=session,
    )
    assert outcome.succeeded is False
    assert outcome.reason is ResolutionTerminationReason.TOOL_SURFACE_DEAD
    assert session.exhaustion_reason is not None
    assert session.exhaustion_reason.startswith(
        ResolutionTerminationReason.TOOL_SURFACE_DEAD.value
    )


def test_an_agents_activity_event_cannot_kill_its_own_tool_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end: progress text mentioning a tool surface leaves the chain live."""
    from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusEvent
    from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind
    from ralph.pipeline.conflict_resolution.session import _wrap_activity_listener

    session = ResolutionSession()
    listener = _wrap_activity_listener(None, session, agent_name="claude")
    assert listener is not None
    listener(
        WaitingStatusEvent(
            kind=next(iter(WaitingStatusKind)),
            cumulative_seconds=1.0,
            current_run_seconds=1.0,
            idle_elapsed_seconds=1.0,
            ceiling_seconds=900.0,
            suspect_threshold_seconds=None,
            diagnostic={"last_activity_kind": "mcp_tool"},
            subagent_activity="reading tool_contract.py: the live Ralph-owned tool surface",
            stall_active=False,
        )
    )
    assert session.dead_tool_surfaces == ()
    assert session.terminal_reason is None


def test_ralph_infrastructure_faults_are_not_recorded_as_an_exhausted_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dead tool surface is Ralph's fault, not the resolver giving up.

    Recording it as an exhausted chain wrote durable terminal evidence
    that the resolver had failed, and the next seam then refused to try
    at all -- for a conflict no agent had actually been unable to fix.
    """
    from ralph.pipeline import auto_integrate_rebase_merge as rebase_merge

    captured: dict[str, object] = {}

    def _fake_resolve(root: Path, target: str, resolver: object, *, session: object) -> bool:
        captured["session"] = session
        session.exhaustion_reason = "TOOL_SURFACE_DEAD: conflict markers survive in: a.py"
        session.charge_conflict_budget = False
        return False

    monkeypatch.setattr(rebase_merge, "resolve_rebase_in_progress", _fake_resolve)
    resolved, reason = rebase_merge._resolve_rebase_with_config(
        tmp_path, "main", lambda *_a: False, None
    )
    assert resolved is False
    assert reason is None, "an infrastructure fault must not become exhaustion evidence"


def test_a_real_resolver_exhaustion_is_still_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Suppressing infrastructure faults must not suppress genuine exhaustion."""
    from ralph.pipeline import auto_integrate_rebase_merge as rebase_merge

    def _fake_resolve(root: Path, target: str, resolver: object, *, session: object) -> bool:
        session.exhaustion_reason = "ATTEMPT_FAILED: conflict markers survive in: a.py"
        return False

    monkeypatch.setattr(rebase_merge, "resolve_rebase_in_progress", _fake_resolve)
    resolved, reason = rebase_merge._resolve_rebase_with_config(
        tmp_path, "main", lambda *_a: False, None
    )
    assert resolved is False
    assert reason == "ATTEMPT_FAILED: conflict markers survive in: a.py"


def test_a_new_stop_starts_with_a_chargeable_budget(tmp_path: Path) -> None:
    """The flag describes the stop that failed, not one three stops ago."""
    from ralph.pipeline.conflict_resolution.session import begin_resolution_stop

    session = ResolutionSession(charge_conflict_budget=False)
    begin_resolution_stop(session)
    assert session.charge_conflict_budget is True


def test_a_failed_mechanical_attempt_still_reaches_the_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ralph's own deterministic try must not consume the stop's only chance.

    A mode-only or gitlink stop is staged without an agent. When that
    attempt did not prove out, the stop was abandoned -- with a resolver
    configured, willing, and never asked.
    """
    from ralph.pipeline.conflict_resolution import rebase_loop

    stop = rebase_loop.RebaseStop(
        sha="deadbeef",
        subject="replayed commit",
        conflicted_files=("a.py",),
        stop_index=1,
        stop_cap=10,
    )
    monkeypatch.setattr(rebase_loop, "_read_stop", lambda *_a: stop)
    monkeypatch.setattr(rebase_loop, "_worktree_dirty_paths", lambda _root: frozenset())
    monkeypatch.setattr(rebase_loop, "_try_deterministic_resolution", lambda *_a: True)
    monkeypatch.setattr(rebase_loop, "_stage_and_prove", lambda *_a: False)
    monkeypatch.setattr(rebase_loop, "_touched_nothing_unexpected", lambda *_a: True)
    monkeypatch.setattr(rebase_loop, "_remove_ort_residue", lambda *_a: True)
    monkeypatch.setattr(rebase_loop, "_continue_past", lambda *_a: True)
    asked: list[str] = []

    def _resolver(_root: Path, _target: str, _stop: object) -> bool:
        asked.append("resolver")
        return True

    resolved = rebase_loop._resolve_one_stop(
        tmp_path, "main", _resolver, 1, 10, frozenset(), set()
    )
    assert asked == ["resolver"], "the resolver must still be offered the stop"
    assert resolved is False, "and Ralph's own proof still gates the landing"


def test_an_infrastructure_fault_does_not_kill_the_agent_for_the_whole_rebase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The shipped chain is ONE agent; barring it for the run ends resolution.

    A transport loop or an unanswered relay is Ralph's own plumbing --
    the recovery layer classifies that same failure as retryable -- so a
    single hiccup must not leave every later stop with nobody to invoke.
    """
    _install_seams(monkeypatch)
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("solo",))
    session = ResolutionSession()
    calls: list[str] = []

    def _faulting(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        calls.append(agent_name)
        session.terminal_reason = ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
        return False

    driver_module.run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=_faulting,
        session=session,
    )
    assert calls, "the faulting agent still had its attempt"
    assert session.dead_tool_surfaces == (), "a plumbing fault is not a permanent verdict"

    # A later stop must still reach it.
    _install_seams(monkeypatch)
    later: list[str] = []
    driver_module.run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda name, _p, _r: later.append(name) is None,
        session=session,
    )
    assert later, "the next stop must still be able to invoke the only agent"


def test_a_chain_barred_only_by_ralphs_own_faults_is_retried_not_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Doing nothing is the one outcome that cannot resolve the conflict."""
    _install_seams(monkeypatch)
    session = ResolutionSession(stop_dead_surfaces=("primary", "fallback"))
    calls: list[str] = []

    driver_module.run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda name, _p, _r: calls.append(name) is None,
        session=session,
    )
    assert calls, "an infrastructure bar must not leave the round with nobody to invoke"


def test_a_candidate_the_registry_cannot_produce_stays_barred_for_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """That bar IS deterministic: the name will not appear mid-run."""
    _install_seams(monkeypatch)
    session = ResolutionSession()
    calls: list[str] = []

    def _unavailable(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        calls.append(agent_name)
        if agent_name == "primary":
            session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
        return False

    driver_module.run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=_unavailable,
        session=session,
    )
    assert session.dead_tool_surfaces == ("primary",)


def test_every_reason_less_exit_now_names_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An exit with no reason is reported downstream as a failed resolution.

    Two exits returned before the exhaustion block, leaving
    `ResolutionOutcome.reason` empty and `exhaustion_reason` unset -- so
    the operator was told "conflict resolution failed" for a resolution
    that was never configured, or whose prompt could not be written.
    """
    _install_seams(monkeypatch)

    # No agent bound to the drain.
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ())
    session = ResolutionSession()
    outcome = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda *_a: True,
        session=session,
    )
    assert outcome.reason is ResolutionTerminationReason.NO_RESOLVER_CONFIGURED
    assert session.exhaustion_reason is not None

    # The prompt could not be materialized.
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    monkeypatch.setattr(driver_module, "render_conflict_prompt", lambda **_k: None)
    session = ResolutionSession()
    outcome = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda *_a: True,
        session=session,
    )
    assert outcome.reason is ResolutionTerminationReason.PROMPT_UNAVAILABLE
    assert session.exhaustion_reason is not None


def test_the_operator_headline_names_what_the_resolver_reported() -> None:
    """"Conflict resolution failed" was recorded for never-attempted work."""
    from ralph.git.merge import MergeResult
    from ralph.git.rebase.rebase import RebaseConflicts
    from ralph.pipeline.auto_integrate_outcome import classify_rebase_outcome
    from ralph.pipeline.auto_integrate_resolve import RESOLUTION_FAILED

    _action, reason = classify_rebase_outcome(
        rebase_outcome=RebaseConflicts(files=["a.py"]),
        merge_attempted=True,
        merge_outcome=MergeResult(outcome=RESOLUTION_FAILED, reason="OUT_OF_REACH"),
    )
    assert reason is not None
    assert "OUT_OF_REACH" in reason


def test_a_binary_conflict_does_not_starve_the_text_conflicts_beside_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One PNG must not mean three text conflicts are never offered to anyone.

    Escalating the whole set on sight left nothing about the repository
    changed, so the next run saw exactly what this one saw: zero
    invocations, every run, forever.
    """
    from ralph.pipeline.conflict_resolution.sight import ConflictSight

    paths = ["asset.png", "a.py", "b.py"]
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: list(paths))
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", lambda _r, _p: [])
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _root, given: {
            path: (
                ConflictSight.AGENT_DECISION if path.endswith(".png") else ConflictSight.AGENT
            )
            for path in given
        },
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _r, _k: ())
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("claude",))
    calls: list[str] = []

    driver_module.run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda name, _p, _r: calls.append(name) is None,
        session=ResolutionSession(),
    )
    assert calls, "a binary conflict must not starve the text conflicts beside it"


def test_a_submodule_pointer_beside_a_text_conflict_still_spends_the_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The unreachable path is escalated by name, AFTER the rest are resolved."""
    from ralph.pipeline.conflict_resolution.sight import ConflictSight

    paths = ["vendor/sub", "a.py"]
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: list(paths))
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", lambda _r, _p: [])
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _root, given: {
            path: (
                ConflictSight.OUT_OF_REACH if path == "vendor/sub" else ConflictSight.AGENT
            )
            for path in given
        },
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _r, _k: ())
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("claude",))
    session = ResolutionSession()
    calls: list[str] = []

    outcome = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda name, _p, _r: calls.append(name) is None or True,
        session=session,
    )
    assert calls, "the resolvable path must still reach a resolver"
    assert outcome.succeeded is False, "the stop cannot land while a path is unreachable"
    assert outcome.reason is ResolutionTerminationReason.OUT_OF_REACH
    assert outcome.unresolved_paths == ("vendor/sub",), "and it is named"


def test_the_durable_evidence_does_not_assert_a_scan_that_never_ran(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """"Conflict markers survive in X" was asserted for every reason.

    Including exits that never opened a file, and paths that cannot
    carry markers at all -- a binary, a modify/delete. The sentence has
    to be true of every reason that uses it.
    """
    from ralph.pipeline.conflict_resolution.sight import ConflictSight

    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: ["vendor/sub"])
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", lambda _r, _p: [])
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _root, paths: dict.fromkeys(paths, ConflictSight.OUT_OF_REACH),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _r, _k: ())
    session = ResolutionSession()

    outcome = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda *_a: True,
        session=session,
    )
    assert session.exhaustion_reason is not None
    assert "markers survive" not in session.exhaustion_reason
    assert "vendor/sub" in session.exhaustion_reason
    # And the typed outcome names what is unresolved instead of ().
    assert outcome.unresolved_paths == ("vendor/sub",)


def test_a_clean_stop_is_not_reported_as_a_failure_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing unmerged is nothing to resolve, not a failure of anything."""
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: [])
    session = ResolutionSession()

    outcome = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda *_a: True,
        session=session,
    )
    assert outcome.reason is None
    assert session.exhaustion_reason is None
