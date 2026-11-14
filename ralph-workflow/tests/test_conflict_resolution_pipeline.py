from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import phase_style_for_phase
from ralph.git.conflict_stage_entries import ConflictStageEntries
from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.auto_integrate_outcome import record_resolution_exhausted
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution import sight as sight_module
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.driver import (
    run_conflict_resolution_pipeline,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.conflict_resolution.status import (
    NEUTRAL_PHASE_LABEL,
    PHASE_LABEL,
    push_conflict_status_bar,
)
from ralph.pipeline.integration_resolution import (
    EXHAUSTED,
    assert_non_resolution_dispatch_allowed,
    inspect_integration_resolution,
)
from ralph.pipeline.resolution_outcome import ResolvedOutcome
from ralph.policy.loader import load_policy
from ralph.recovery import dead_agent_probe
from ralph.recovery.canary_probe import CANARY_PROMPT_FILENAME, ProbeOutcome, ProbeResult
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["src/alpha.py", "docs/beta.md"]
_POLICY_BUNDLE = load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")
_CONFIG = UnifiedConfig.model_validate({"general": {}})
_RESTORE_STATUS_BAR = driver_module._restore_status_bar

pytestmark = pytest.mark.timeout_seconds(5)


@pytest.fixture(autouse=True)
def _no_real_cooldown_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    monkeypatch.setattr(
        sight_module,
        "conflict_stage_entries",
        lambda _root, _paths: ConflictStageEntries({}),
    )
    _stub_canary_probe_boundary(monkeypatch)


class _FakeStatusBar:
    def __init__(self) -> None:
        self.last_model: object | None = None


class _FakeDisplay:
    def __init__(self) -> None:
        self.status_bar = _FakeStatusBar()
        self.models: list[object] = []
        self.warn_lines: list[str] = []

    def update_status_bar(self, model: object) -> None:
        self.models.append(model)
        self.status_bar.last_model = model

    def emit_warn_line(self, unit: str, channel: str, message: str) -> None:
        self.warn_lines.append(message)


def _policy_bundle() -> PolicyBundle:
    return _POLICY_BUNDLE


def _config() -> UnifiedConfig:
    return _CONFIG


def _stub_canary_probe_boundary(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    probed: list[str] = []

    def fake_run_canary_probe(config: AgentConfig, **_kwargs: object) -> ProbeResult:
        probed.append(config.cmd)
        return ProbeResult(outcome=ProbeOutcome.INCONCLUSIVE, detail="scripted")

    monkeypatch.setattr(dead_agent_probe, "run_canary_probe", fake_run_canary_probe)
    monkeypatch.setattr("ralph.pipeline.attempt_runner.run_canary_probe", fake_run_canary_probe)
    return probed


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str],
    surviving_per_round: Sequence[Sequence[str]],
) -> list[Sequence[str]]:
    scans: list[Sequence[str]] = []
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(unmerged))
    monkeypatch.setattr(
        driver_module,
        "render_conflict_prompt",
        lambda *, root, **_kwargs: root / "PROMPT.md",
    )
    monkeypatch.setattr(driver_module, "_restore_status_bar", lambda *_args: None)
    monkeypatch.setattr(
        sight_module,
        "conflict_stage_entries",
        lambda _root, _paths: ConflictStageEntries({}),
    )

    def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
        index = min(len(scans), len(surviving_per_round) - 1)
        scans.append(paths)
        return list(surviving_per_round[index])

    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)
    return scans


def _probe_reachable_deps(bundle: PolicyBundle) -> PipelineDeps:
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object = None,
        _clock: object = None,
    ) -> object:
        del options, _clock
        if Path(prompt_file).name == CANARY_PROMPT_FILENAME:
            return iter(("scripted canary reply",))
        raise AgentInvocationError(
            config.cmd,
            0,
            "agent idle timeout",
        )

    deps = make_test_pipeline_deps(
        make_display_context(),
        policy_bundle=bundle,
    )
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            dead_agent_probe_runner=dead_agent_probe.production_dead_agent_probe_runner(_config()),
        )
    )
    return replace(
        deps,
        invoke_agent=fake_invoke_agent,
        recovery_sleep=lambda _: None,
        recovery_controller=controller,
    )


def _run(
    tmp_path: Path,
    *,
    invoke: driver_module.ResolutionInvoker,
    display: _FakeDisplay | None = None,
    session: ResolutionSession | None = None,
    pipeline_deps: PipelineDeps | None = None,
) -> bool:
    if pipeline_deps is None:
        pipeline_deps = _probe_reachable_deps(_policy_bundle())
    if session is None:
        session = ResolutionSession()
    if session.recovery_controller is None and pipeline_deps.recovery_controller is not None:
        session.recovery_controller = pipeline_deps.recovery_controller
    return bool(
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=pipeline_deps,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=display,
            display_context=None,
            invoke=invoke,
            session=session,
        )
    )


def test_first_round_success_returns_true_and_invokes_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    calls: list[int] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        calls.append(round_index)
        return True

    assert _run(tmp_path, invoke=_invoke) is True
    assert calls == [1]


def test_surviving_markers_loop_and_feed_the_paths_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(
        monkeypatch,
        unmerged=_CONFLICTED,
        surviving_per_round=[["src/alpha.py"], []],
    )
    feedback_seen: list[tuple[str, ...]] = []

    def _spy_render(
        *,
        root: Path,
        target: str,
        conflicted_paths: Sequence[str],
        round_index: int,
        round_cap: int,
        surviving_marker_paths: Sequence[str],
        replaying_commit_sha: str | None = None,
        replaying_commit_subject: str | None = None,
        stop_index: int | None = None,
        stop_cap: int | None = None,
    ) -> Path | None:
        del (
            target,
            conflicted_paths,
            round_index,
            round_cap,
            replaying_commit_sha,
            replaying_commit_subject,
            stop_index,
            stop_cap,
        )
        feedback_seen.append(tuple(surviving_marker_paths))
        return root / "PROMPT.md"

    monkeypatch.setattr(driver_module, "render_conflict_prompt", _spy_render)

    assert _run(tmp_path, invoke=lambda name, path, index: True) is True
    assert feedback_seen == [(), ("src/alpha.py",)]


def test_bounded_at_max_resolution_rounds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[["src/alpha.py"]])
    calls: list[int] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        calls.append(round_index)
        return True

    assert _run(tmp_path, invoke=_invoke) is False
    assert calls == [1, 2, 3]


def test_failed_resolver_attempt_uses_the_next_fallback_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(
        monkeypatch,
        unmerged=_CONFLICTED,
        surviving_per_round=[list(_CONFLICTED)] * 3 + [[]],
    )
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("primary", "fallback")
    )
    calls: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        calls.append(agent_name)
        if agent_name == "primary":
            raise RuntimeError("agent exploded")
        return True

    session = ResolutionSession(
        recovery_controller=_probe_reachable_deps(_policy_bundle()).recovery_controller
    )
    assert (
        bool(
            run_conflict_resolution_pipeline(
                root=tmp_path,
                target="main",
                config=_config(),
                pipeline_deps=_probe_reachable_deps(_policy_bundle()),
                workspace_scope=None,
                policy_bundle=_policy_bundle(),
                display=None,
                display_context=None,
                invoke=_invoke,
                session=session,
            )
        )
        is True
    )
    assert calls == ["primary", "primary", "primary", "fallback"]
    recovery_state = session.recovery_state
    assert recovery_state is not None
    chain = recovery_state.chain_for_phase(PHASE_RESOLUTION)
    assert chain == AgentChainState(agents=["primary", "fallback"], current_index=1, retries=0)


def test_exhausted_resolver_chain_reports_typed_terminal_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[_CONFLICTED])
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("primary", "fallback")
    )
    session = ResolutionSession(
        max_rounds_per_stop=1,
        skip_same_agent_retries=True,
        recovery_controller=_probe_reachable_deps(_policy_bundle()).recovery_controller,
    )
    calls: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        calls.append(agent_name)
        return False

    outcome = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=_config(),
        pipeline_deps=_probe_reachable_deps(_policy_bundle()),
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=_invoke,
        session=session,
    )

    assert not isinstance(outcome, ResolvedOutcome)
    assert session.terminal_reason is ResolutionTerminationReason.ATTEMPT_FAILED
    assert session.exhaustion_reason is not None
    assert calls == ["primary", "primary", "primary", "fallback", "fallback", "fallback"]
    recovery_state = session.recovery_state
    assert recovery_state is not None
    chain = recovery_state.chain_for_phase(PHASE_RESOLUTION)
    assert chain == AgentChainState(agents=["primary", "fallback"], current_index=0, retries=0)


def test_exhausted_resolver_chain_persists_terminal_state_that_blocks_planning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[_CONFLICTED])
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("primary", "fallback")
    )
    session = ResolutionSession(
        max_rounds_per_stop=1,
        recovery_controller=_probe_reachable_deps(_policy_bundle()).recovery_controller,
    )
    outcome = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=_config(),
        pipeline_deps=_probe_reachable_deps(_policy_bundle()),
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda _agent, _prompt, _round: False,
        session=session,
    )

    assert not isinstance(outcome, ResolvedOutcome)
    assert session.exhaustion_reason is not None
    persisted = record_resolution_exhausted(
        reason=session.exhaustion_reason,
        target="main",
    )
    verdict = inspect_integration_resolution(
        tmp_path,
        persisted,
        porcelain=lambda _root: (True, "UU src/alpha.py\n"),
        rebase_active=lambda _root: False,
        merge_status=lambda _root: MERGE_STATE_NONE,
    )
    assert verdict.status is EXHAUSTED
    with pytest.raises(RuntimeError, match="cannot dispatch 'planning'"):
        assert_non_resolution_dispatch_allowed("planning", verdict)


def test_invoker_exception_is_contained_and_returns_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[["src/alpha.py"]])
    probed = _stub_canary_probe_boundary(monkeypatch)

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        raise RuntimeError("agent exploded")

    assert _run(tmp_path, invoke=_invoke) is False
    assert probed, "the failed resolution attempt must still reach the dead-agent probe boundary"


def test_agent_success_over_surviving_markers_is_not_a_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[["src/alpha.py"]])
    assert _run(tmp_path, invoke=lambda name, path, index: True) is False


def test_failed_invocation_with_clean_markers_is_not_a_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    assert _run(tmp_path, invoke=lambda name, path, index: False) is False


def test_unreadable_unmerged_query_returns_false_without_invoking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(
        monkeypatch,
        unmerged=["<unmerged-path-query-failed>"],
        surviving_per_round=[[]],
    )
    calls: list[int] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        calls.append(round_index)
        return True

    assert _run(tmp_path, invoke=_invoke) is False
    assert calls == []


def test_no_conflicts_returns_false_without_invoking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=[], surviving_per_round=[[]])
    calls: list[int] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        calls.append(round_index)
        return True

    assert _run(tmp_path, invoke=_invoke) is False
    assert calls == []


def test_status_bar_is_pushed_with_the_conflict_resolution_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    display = _FakeDisplay()

    assert _run(tmp_path, invoke=lambda name, path, index: True, display=display) is True

    labels = [getattr(model, "phase_label", None) for model in display.models]
    assert PHASE_LABEL in labels


def test_previous_status_bar_model_is_restored_on_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    monkeypatch.setattr(driver_module, "_restore_status_bar", _RESTORE_STATUS_BAR)
    display = _FakeDisplay()
    sentinel = object()
    display.status_bar.last_model = sentinel

    assert _run(tmp_path, invoke=lambda name, path, index: True, display=display) is True
    assert display.models[-1] is sentinel


def test_status_bar_is_not_left_on_the_resolution_label_without_a_prior_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    monkeypatch.setattr(driver_module, "_restore_status_bar", _RESTORE_STATUS_BAR)
    display = _FakeDisplay()
    assert display.status_bar.last_model is None

    assert _run(tmp_path, invoke=lambda name, path, index: True, display=display) is True

    final = display.models[-1]
    assert getattr(final, "phase_label", None) == NEUTRAL_PHASE_LABEL
    assert getattr(final, "phase_label", None) != PHASE_LABEL
    assert getattr(final, "workspace_root", None) == str(tmp_path)


def test_status_bar_clear_tolerates_a_display_that_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])

    class _ExplodingDisplay(_FakeDisplay):
        def update_status_bar(self, model: object) -> None:
            message = "status bar exploded"
            raise RuntimeError(message)

    display = _ExplodingDisplay()

    assert _run(tmp_path, invoke=lambda name, path, index: True, display=display) is True


def test_entry_and_exit_are_announced_to_the_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[["src/alpha.py"]])
    display = _FakeDisplay()

    assert _run(tmp_path, invoke=lambda name, path, index: True, display=display) is False

    joined = "\n".join(display.warn_lines)
    assert "entering rebase conflict resolution" in joined
    assert "abandoning conflict resolution" in joined
    assert "src/alpha.py" in joined


def test_merge_mode_success_reports_committing_the_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    display = _FakeDisplay()

    assert _run(tmp_path, invoke=lambda name, path, index: True, display=display) is True

    joined = "\n".join(display.warn_lines)
    assert "conflicts resolved in round 1; verifying and committing the merge" in joined


def test_rebase_mode_success_reports_continuing_the_rebase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=[], surviving_per_round=[[]])
    display = _FakeDisplay()

    resolved = run_rebase_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        stop=RebaseStop(
            sha="0123456789abcdef",
            subject="feat: add alpha",
            conflicted_files=tuple(_CONFLICTED),
            stop_index=1,
            stop_cap=4,
        ),
        config=_config(),
        pipeline_deps=_probe_reachable_deps(_policy_bundle()),
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=display,
        display_context=None,
        invoke=lambda name, path, index: True,
    )

    assert isinstance(resolved, ResolvedOutcome)
    joined = "\n".join(display.warn_lines)
    assert "conflicts resolved in round 1; verifying and continuing the rebase" in joined
    assert "committing the merge" not in joined


def test_the_resolution_phase_has_its_own_non_muted_display_style() -> None:
    assert phase_style_for_phase(PHASE_RESOLUTION) != "theme.text.muted"
    assert phase_style_for_phase(PHASE_RESOLUTION) == "theme.phase.fix"


def test_the_pushed_footer_carries_that_non_muted_style(tmp_path: Path) -> None:
    display = _FakeDisplay()

    push_conflict_status_bar(
        display,
        tmp_path,
        target="main",
        round_index=1,
        round_cap=3,
    )

    pushed = display.models[-1]
    assert pushed.phase_style == phase_style_for_phase(PHASE_RESOLUTION)
    assert pushed.phase_style != "theme.text.muted"


def test_resolution_termination_keeps_typed_reason_activity_and_unresolved_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[_CONFLICTED])
    display = _FakeDisplay()
    session = ResolutionSession(
        recovery_controller=_probe_reachable_deps(_policy_bundle()).recovery_controller
    )

    def _decline(_name: str, _path: Path, _round: int) -> bool:
        session.terminal_reason = ResolutionTerminationReason.OPERATOR_CAP_REACHED
        session.last_activity_kind = "mcp_tool"
        session.last_activity_at = 12.0
        session.last_duration_seconds = 60.0
        return False

    assert (
        bool(
            run_conflict_resolution_pipeline(
                root=tmp_path,
                target="main",
                config=_config(),
                pipeline_deps=_probe_reachable_deps(_policy_bundle()),
                workspace_scope=None,
                policy_bundle=_policy_bundle(),
                display=display,
                display_context=None,
                invoke=_decline,
                session=session,
            )
        )
        is False
    )

    termination = next(line for line in display.warn_lines if "OPERATOR_CAP_REACHED" in line)
    assert "last_activity_kind=mcp_tool" in termination
    assert "last_activity_at=12.0" in termination
    assert "duration=60.0s" in termination
    assert "src/alpha.py, docs/beta.md" in termination


def test_work_the_agent_finished_is_not_thrown_away_by_a_bad_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    session = ResolutionSession()

    def _worked_then_exited_badly(name: str, path: Path, index: int) -> bool:
        session.last_attempt_saw_activity = True
        return False

    assert _run(tmp_path, invoke=_worked_then_exited_badly, session=session) is True


def test_a_round_that_never_reached_an_agent_still_proves_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    session = ResolutionSession()

    def _never_reached_the_agent(name: str, path: Path, index: int) -> bool:
        session.last_attempt_saw_activity = False
        return False

    assert _run(tmp_path, invoke=_never_reached_the_agent, session=session) is False


def test_an_earlier_candidates_finished_work_survives_a_later_one_not_starting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    session = ResolutionSession()

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        session.last_attempt_saw_activity = agent_name == "one"
        return False

    assert _run(tmp_path, invoke=_invoke, session=session) is True


def test_a_heartbeat_does_not_credit_a_decision_nobody_declared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline.conflict_resolution.sight import ConflictSight

    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: ["gone.py"])
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", lambda _r, _p: [])
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _root, paths: dict.fromkeys(paths, ConflictSight.AGENT_DECISION),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _r, _k: ())
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("claude",))
    session = ResolutionSession()

    def _ticked_but_declared_nothing(name: str, _p: Path, _r: int) -> bool:
        session.last_attempt_saw_activity = True
        return False

    assert _run(tmp_path, invoke=_ticked_but_declared_nothing, session=session) is False


def test_a_declared_decision_is_still_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline.conflict_resolution.sight import ConflictSight

    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: ["gone.py"])
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", lambda _r, _p: [])
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _root, paths: dict.fromkeys(paths, ConflictSight.AGENT_DECISION),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _r, _k: ())
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("claude",))

    assert _run(tmp_path, invoke=lambda *_a: True) is True


def test_a_file_the_resolver_destroyed_is_not_a_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: ["src/alpha.py"])
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("claude",))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _s: None)
    session = ResolutionSession(max_rounds_per_stop=1)
    probed = _stub_canary_probe_boundary(monkeypatch)

    def _destroys_the_file(name: str, _p: Path, _r: int) -> bool:
        (tmp_path / "src" / "alpha.py").unlink()
        session.last_attempt_saw_activity = True
        return False

    assert (
        _run(
            tmp_path,
            invoke=_destroys_the_file,
            session=session,
            pipeline_deps=_probe_reachable_deps(_policy_bundle()),
        )
        is False
    )
    assert session.unresolved_paths == ("src/alpha.py",)
    assert probed, "the failed resolution attempt must still reach the dead-agent probe boundary"


def test_a_deletion_is_still_unresolved_on_the_round_after_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: ["src/alpha.py"])
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("claude",))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _s: None)
    session = ResolutionSession()
    rounds: list[int] = []

    def _deletes_then_heartbeats(name: str, _p: Path, round_index: int) -> bool:
        rounds.append(round_index)
        target = tmp_path / "src" / "alpha.py"
        if target.exists():
            target.unlink()
        session.last_attempt_saw_activity = True
        return False

    assert _run(tmp_path, invoke=_deletes_then_heartbeats, session=session) is False
    assert len(rounds) > 1, "the later rounds are where the guard used to lapse"
    assert session.unresolved_paths == ("src/alpha.py",)


def test_a_truncated_conflict_is_not_a_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: ["src/alpha.py"])
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("claude",))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _s: None)
    session = ResolutionSession(max_rounds_per_stop=1)

    def _truncates(name: str, _p: Path, _r: int) -> bool:
        (tmp_path / "src" / "alpha.py").write_text("")
        session.last_attempt_saw_activity = True
        return False

    assert _run(tmp_path, invoke=_truncates, session=session) is False
    assert session.unresolved_paths == ("src/alpha.py",)
