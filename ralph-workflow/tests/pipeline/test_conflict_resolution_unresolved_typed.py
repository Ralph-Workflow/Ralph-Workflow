from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger as loguru_logger

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution.driver import (
    run_conflict_resolution_outcome,
    run_rebase_conflict_resolution_outcome,
)
from ralph.pipeline.conflict_resolution.git_worktree_observer import GitWorktreeConflictObserver
from ralph.pipeline.conflict_resolution.prompt import PromptMaterializer
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
)
from ralph.pipeline.conflict_resolution.sight import ConflictSight
from ralph.pipeline.conflict_resolution.worktree_observer import WorktreeConflictObserver
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
)
from ralph.policy.loader import default_dir, load_policy
from ralph.policy.models import AgentChainConfig
from tests._resolver_controller import make_resolver_controller

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

_CONFLICTED: tuple[str, ...] = (
    "src/alpha.py",
    "src/beta.py",
    "docs/gamma.md",
    "docs/delta.md",
    "pkg/epsilon.py",
    "pkg/zeta.py",
)

_POLICY_BUNDLE: PolicyBundle = load_policy(default_dir())
_CONFIG = UnifiedConfig.model_validate({"general": {}})
_RESOLUTION_PHASE = "rebase_conflict_resolution"
_RESOLUTION_CHAIN = "rebase_conflict_resolution"

pytestmark = pytest.mark.timeout_seconds(5)


class _FrozenSleepClock:
    def __init__(self) -> None:
        self._now = 1_000.0

    def now(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._now

    def epoch(self) -> float:
        return self._now

    def now_utc(self) -> datetime:
        return datetime.fromtimestamp(self._now, tz=UTC)

    def sleep(self, seconds: float) -> None:
        del seconds


@pytest.fixture
def frozen_clock() -> Iterator[_FrozenSleepClock]:
    clock = _FrozenSleepClock()
    token = install_executors(clock=clock)
    try:
        yield clock
    finally:
        reset_executors(token)


class _ScriptedObserver(WorktreeConflictObserver):
    def __init__(self, unmerged: Sequence[str]) -> None:
        self._unmerged = tuple(unmerged)
        self.surviving: tuple[str, ...] = self._unmerged
        self._content_checks = GitWorktreeConflictObserver()
        self.marker_scans: list[tuple[str, ...]] = []

    def unmerged_paths(self, root: Path) -> Sequence[str]:
        del root
        return self._unmerged

    def retained_merge_paths(self, root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
        del root
        return tuple(paths)

    def classify_unmerged_conflicts(
        self, root: Path, paths: tuple[str, ...]
    ) -> dict[str, ConflictSight]:
        del root
        return dict.fromkeys(tuple(paths), ConflictSight.AGENT)

    def declared_decision_paths(self, kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
        del kinds
        return ()

    def stage_mechanical_conflicts(
        self, root: Path, kinds: dict[str, ConflictSight]
    ) -> tuple[str, ...]:
        del root, kinds
        return ()

    def paths_with_conflict_markers(self, root: Path, paths: tuple[str, ...]) -> Sequence[str]:
        del root
        self.marker_scans.append(tuple(paths))
        return self.surviving

    def path_exists(self, path: Path) -> bool:
        return self._content_checks.path_exists(path)

    def is_effectively_empty(self, path: Path) -> bool:
        return self._content_checks.is_effectively_empty(path)


class _FakeMaterializer(PromptMaterializer):
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.rounds: list[int] = []

    def __call__(
        self,
        *,
        root: Path,
        target: str,
        conflicted_paths: Sequence[str],
        round_index: int,
        round_cap: int,
        session: ResolutionSession,
        stop: RebaseStop | None,
        strategy_history: tuple[str, ...],
    ) -> Path | None:
        del root, target, conflicted_paths, round_cap, session, stop, strategy_history
        self.rounds.append(round_index)
        prompt_path = self._workspace / ".agent" / "tmp" / f"round_{round_index}_prompt.md"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("resolve the conflicts", encoding="utf-8")
        return prompt_path


@dataclass(frozen=True)
class _AttemptStep:
    surviving: tuple[str, ...] | None = None
    raw_error: str | None = None
    report_success: bool = True
    active_failure: bool = False


class _ScriptedRunner:
    def __init__(
        self,
        observer: _ScriptedObserver,
        scripts: dict[str, Sequence[_AttemptStep]],
        invocations: list[str],
    ) -> None:
        self._observer = observer
        self._scripts = {name: tuple(steps) for name, steps in scripts.items()}
        self._invocations = invocations
        self._attempts: dict[str, int] = {}

    def __call__(
        self, agent_name: str, prompt_path: Path, round_index: int
    ) -> StopResolutionOutcome | bool:
        del prompt_path, round_index
        self._invocations.append(agent_name)
        attempt_number = self._attempts.get(agent_name, 0)
        self._attempts[agent_name] = attempt_number + 1
        steps = self._scripts.get(agent_name, (_AttemptStep(surviving=()),))
        step = steps[min(attempt_number, len(steps) - 1)]
        if step.surviving is not None:
            self._observer.surviving = step.surviving
        if step.raw_error is not None:
            raise RuntimeError(step.raw_error)
        if step.active_failure:
            return RetryLaterOutcome(on_change="unresolved conflict remains")
        return step.report_success


@contextmanager
def _captured_logs() -> Iterator[list[str]]:
    captured: list[str] = []
    sink_id = loguru_logger.add(lambda message: captured.append(str(message)), level="INFO")
    try:
        yield captured
    finally:
        loguru_logger.remove(sink_id)


def _typed_reason(unresolved_count: int) -> str:
    return f"agent reported success but {unresolved_count} conflicted path(s) remain"


def _assert_not_ambiguous(logs: Sequence[str]) -> None:
    assert not any("flagged_for_review" in line for line in logs), (
        "an owner-known failure fact must never reach the ambiguous text fall-through"
    )
    assert not any("Ambiguous failure" in line for line in logs), (
        "an owner-known failure fact must never be logged as an ambiguous failure"
    )


def _assert_agent_failure_charged(logs: Sequence[str], unresolved_count: int) -> None:
    assert not any("retry without budget debit" in line for line in logs), (
        "an owner-known failure fact must never be routed as a free retry"
    )
    assert not any("not counted against budget" in line for line in logs), (
        "an owner-known failure fact must never be routed as an environmental free retry"
    )
    assert any(
        "typed attempt outcome" in line and _typed_reason(unresolved_count) in line for line in logs
    ), (
        "the owner must observe the typed fact with the §11.2 reason; "
        "only non-RAW typed outcomes emit this log and they map to AGENT"
    )


def _assert_demoted(logs: Sequence[str], agent: str, *, consecutive: int, total: int) -> None:
    needle = (
        f"Agent '{agent}' demoted in phase '{_RESOLUTION_PHASE}' "
        f"(consecutive={consecutive}, total={total},"
    )
    assert any(needle in line for line in logs), (
        f"expected demotion log {needle!r} in:\n" + "\n".join(logs)
    )


def _resolution_policy_bundle(candidates: Sequence[str]) -> PolicyBundle:
    bundle = _POLICY_BUNDLE
    chains = dict(bundle.agents.agent_chains)
    chains[_RESOLUTION_CHAIN] = AgentChainConfig(
        agents=list(candidates),
        max_retries=2,
        retry_delay_ms=0,
    )
    return bundle.model_copy(
        update={"agents": bundle.agents.model_copy(update={"agent_chains": chains})}
    )


def _session_with_controller() -> ResolutionSession:
    session = ResolutionSession()
    session.recovery_controller = make_resolver_controller(FakeClock(start=0.0))
    return session


def _drive(
    workspace: Path,
    *,
    candidates: Sequence[str],
    scripts: dict[str, Sequence[_AttemptStep]],
    observer: _ScriptedObserver | None = None,
    stop: RebaseStop | None = None,
    clock: _FrozenSleepClock | None = None,
) -> tuple[StopResolutionOutcome, list[str], ResolutionSession]:
    active_observer = observer or _ScriptedObserver(_CONFLICTED)
    materializer = _FakeMaterializer(workspace)
    invocations: list[str] = []
    runner = _ScriptedRunner(active_observer, scripts, invocations)
    session = _session_with_controller()
    effective_clock = clock or _FrozenSleepClock()
    if stop is None:
        outcome = run_conflict_resolution_outcome(
            root=workspace,
            target="main",
            config=_CONFIG,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_resolution_policy_bundle(candidates),
            display=None,
            display_context=None,
            invoke=runner,
            clock=effective_clock.monotonic,
            session=session,
            observer=active_observer,
            prompt_materializer=materializer,
        )
    else:
        outcome = run_rebase_conflict_resolution_outcome(
            root=workspace,
            target="main",
            stop=stop,
            config=_CONFIG,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_resolution_policy_bundle(candidates),
            display=None,
            display_context=None,
            invoke=runner,
            clock=effective_clock.monotonic,
            session=session,
            observer=active_observer,
            prompt_materializer=materializer,
        )
    return outcome, invocations, session


def _drive_captured(
    workspace: Path,
    *,
    candidates: Sequence[str],
    scripts: dict[str, Sequence[_AttemptStep]],
    stop: RebaseStop | None = None,
    observer: _ScriptedObserver | None = None,
    clock: _FrozenSleepClock | None = None,
) -> tuple[StopResolutionOutcome, list[str], list[str], ResolutionSession]:
    with _captured_logs() as logs:
        outcome, invocations, session = _drive(
            workspace,
            candidates=candidates,
            scripts=scripts,
            observer=observer,
            stop=stop,
            clock=clock,
        )
    return outcome, invocations, logs, session


def _no_progress(count: int = 6) -> tuple[_AttemptStep, ...]:
    return tuple(_AttemptStep() for _ in range(count))


def test_A_success_with_unchanged_conflicted_set_is_typed_agent_failure(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts={"agentA": _no_progress(3), "agentB": (_AttemptStep(surviving=()),)},
        clock=frozen_clock,
    )

    assert isinstance(outcome, ResolvedOutcome)
    assert invocations == ["agentA", "agentA", "agentA", "agentB"]
    assert any(_typed_reason(6) in line for line in logs), (
        "the typed failure reason must be logged, not rediscovered from prose"
    )
    _assert_demoted(logs, "agentA", consecutive=3, total=3)
    _assert_not_ambiguous(logs)
    _assert_agent_failure_charged(logs, 6)


def test_B1_count_drop_restarts_the_consecutive_streak(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts={
            "agentA": (
                _AttemptStep(surviving=_CONFLICTED[:4]),
                _AttemptStep(surviving=_CONFLICTED[:2]),
                _AttemptStep(surviving=()),
            )
        },
        clock=frozen_clock,
    )

    assert isinstance(outcome, ResolvedOutcome)
    assert invocations == ["agentA", "agentA", "agentA"]
    assert not any("demoted" in line for line in logs), (
        "a resolver whose attempts drop the count must not be demoted"
    )
    assert any(_typed_reason(4) in line for line in logs)
    assert any(_typed_reason(2) in line for line in logs)
    _assert_not_ambiguous(logs)


def test_B2_demotion_requires_three_no_progress_attempts_after_a_drop(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts={
            "agentA": (
                _AttemptStep(surviving=_CONFLICTED[:4]),
                _AttemptStep(),
                _AttemptStep(),
                _AttemptStep(),
            ),
            "agentB": (_AttemptStep(surviving=()),),
        },
        clock=frozen_clock,
    )

    assert isinstance(outcome, ResolvedOutcome)
    assert invocations == ["agentA", "agentA", "agentA", "agentA", "agentB"], (
        "the drop must restart the streak: demotion only after 3 further "
        f"no-progress attempts; got {invocations}"
    )
    _assert_demoted(logs, "agentA", consecutive=3, total=4)
    _assert_not_ambiguous(logs)


_CANDIDATES_C = (
    "lead",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
)


def _rebase_stop() -> RebaseStop:
    return RebaseStop(
        sha="c0ffee1234",
        subject="replay me",
        conflicted_files=_CONFLICTED,
        stop_index=1,
        stop_cap=10,
    )


def test_C_demoted_resolver_is_skipped_and_the_next_eligible_lands(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    outcome, invocations, logs, session = _drive_captured(
        tmp_path,
        candidates=_CANDIDATES_C,
        scripts={
            "lead": _no_progress(3),
            "second": (_AttemptStep(surviving=()),),
        },
        stop=_rebase_stop(),
        clock=frozen_clock,
    )

    assert isinstance(outcome, ResolvedOutcome)
    assert invocations == ["lead", "lead", "lead", "second"]
    selection_lines = [line for line in logs if "Selected agent second" in line]
    assert selection_lines, "the next eligible candidate must be selected after demotion"
    list_order_reason = (
        "lower_priority (listed after selected agent second in configured chain order)"
    )
    assert all(line.count(list_order_reason) == 5 for line in selection_lines), (
        f"expected exactly five list-order skip reasons naming the selected "
        f"predecessor in: {selection_lines}"
    )
    assert all("lead" in line for line in selection_lines), (
        "the demoted resolver must be named in the skip evidence"
    )
    _assert_demoted(logs, "lead", consecutive=3, total=3)
    assert any(_typed_reason(6) in line for line in logs)
    _assert_not_ambiguous(logs)

    controller = session.recovery_controller
    assert controller is not None
    selection = controller.preferred_agent_index("development", ("lead", "second"))
    assert selection.agent == "lead"
    assert all(skipped_agent != "lead" for skipped_agent, _ in selection.skipped_reasons)


def test_C_all_resolvers_demoted_reports_pending_not_failed(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts={"agentA": _no_progress(3), "agentB": _no_progress(3)},
        stop=_rebase_stop(),
        clock=frozen_clock,
    )

    assert isinstance(outcome, RetryLaterOutcome)
    assert outcome.on_change is not None
    assert "operator" not in (outcome.on_change or "")
    assert invocations == ["agentA", "agentA", "agentA", "agentB", "agentB", "agentB"], (
        f"each resolver must be spent to its demotion; got {invocations}"
    )
    _assert_demoted(logs, "agentA", consecutive=3, total=3)
    _assert_demoted(logs, "agentB", consecutive=3, total=3)
    assert any(_typed_reason(6) in line for line in logs)
    _assert_not_ambiguous(logs)
    assert not any("no resolver can repair" in line for line in logs), (
        "a demotion-driven stop is pending work, not an operator escalation"
    )


_RAW_EXTERNAL_TEXTS = (
    "provider transient fault 0xA4: azimuth thruster miscalibration",
    "upstream relay hung up mid-frame: reason code QZZ-77 (undocumented)",
    "quota gateway answered with a haiku and closed the connection",
)


def test_D1_unrecognised_external_error_stays_ambiguous_and_hits_consecutive_limit(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts={
            "agentA": tuple(_AttemptStep(raw_error=text) for text in _RAW_EXTERNAL_TEXTS),
            "agentB": (_AttemptStep(surviving=()),),
        },
        clock=frozen_clock,
    )

    assert isinstance(outcome, ResolvedOutcome)
    assert invocations == ["agentA", "agentA", "agentA", "agentB"], (
        f"three consecutive RAW failures demote, then the next agent runs; got {invocations}"
    )
    assert sum("Ambiguous failure classification" in line for line in logs) >= 3, (
        "each genuinely external error must classify AMBIGUOUS"
    )
    assert any("flagged_for_review=true" in line for line in logs)
    _assert_demoted(logs, "agentA", consecutive=3, total=3)


def test_D2_raw_failures_interleaved_with_progress_hit_the_total_limit(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    drops = (
        _AttemptStep(surviving=_CONFLICTED[:5]),
        _AttemptStep(surviving=_CONFLICTED[:4]),
        _AttemptStep(surviving=_CONFLICTED[:3]),
        _AttemptStep(surviving=_CONFLICTED[:2]),
    )
    scripts: dict[str, Sequence[_AttemptStep]] = {
        "agentA": (
            _AttemptStep(raw_error=_RAW_EXTERNAL_TEXTS[0]),
            drops[0],
            _AttemptStep(raw_error=_RAW_EXTERNAL_TEXTS[1]),
            drops[1],
            _AttemptStep(raw_error=_RAW_EXTERNAL_TEXTS[2]),
            drops[2],
            _AttemptStep(raw_error=_RAW_EXTERNAL_TEXTS[0] + " [retry 4]"),
            drops[3],
            _AttemptStep(raw_error=_RAW_EXTERNAL_TEXTS[1] + " [retry 5]"),
        ),
        "agentB": (_AttemptStep(surviving=()),),
    }
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts=scripts,
        clock=frozen_clock,
    )

    assert isinstance(outcome, ResolvedOutcome)
    assert invocations == ["agentA"] * 9 + ["agentB"], (
        "each count drop must restart the consecutive streak: the resolver "
        f"survives to the 9th total unproductive attempt; got {invocations}"
    )
    _assert_demoted(logs, "agentA", consecutive=1, total=9)
    assert any("total_unproductive_limit_reached" in line for line in logs), (
        "demotion must come from the TOTAL limit, never the consecutive one"
    )
    assert not any(
        "demoted" in line and "consecutive=3" in line and "total=3" in line for line in logs
    ), "a streak restart must prevent the 3-consecutive demotion"
    assert sum("Ambiguous failure classification" in line for line in logs) >= 5, (
        "every genuinely external RAW failure must still classify AMBIGUOUS"
    )


def test_D3_exception_that_drops_the_unresolved_set_restarts_the_streak(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    observer = _ScriptedObserver(_CONFLICTED)
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts={
            "agentA": (
                _AttemptStep(surviving=_CONFLICTED[:4], raw_error=_RAW_EXTERNAL_TEXTS[0]),
                _AttemptStep(raw_error=_RAW_EXTERNAL_TEXTS[1]),
                _AttemptStep(raw_error=_RAW_EXTERNAL_TEXTS[2]),
                _AttemptStep(raw_error=_RAW_EXTERNAL_TEXTS[0] + " [retry 3]"),
            ),
            "agentB": (_AttemptStep(surviving=()),),
        },
        observer=observer,
        clock=frozen_clock,
    )

    assert isinstance(outcome, ResolvedOutcome)
    assert invocations == ["agentA", "agentA", "agentA", "agentA", "agentB"], (
        "the exception-carried drop must restart the streak: demotion only "
        f"after 3 further no-progress attempts; got {invocations}"
    )
    _assert_demoted(logs, "agentA", consecutive=3, total=4)
    assert sum("Ambiguous failure classification" in line for line in logs) >= 4, (
        "every genuinely external RAW failure must still classify AMBIGUOUS"
    )
    assert len(observer.marker_scans) == len(invocations), (
        "exactly ONE unresolved-set observation must precede each attempt's "
        f"classification/ledger charge; got {len(observer.marker_scans)} scans "
        f"for {len(invocations)} invocations"
    )


def test_D4_false_return_with_count_drop_restarts_the_streak(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    observer = _ScriptedObserver(_CONFLICTED)
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts={
            "agentA": (
                _AttemptStep(surviving=_CONFLICTED[:4], report_success=False),
                _AttemptStep(report_success=False),
                _AttemptStep(report_success=False),
                _AttemptStep(report_success=False),
            ),
            "agentB": (_AttemptStep(surviving=()),),
        },
        observer=observer,
        clock=frozen_clock,
    )

    assert isinstance(outcome, ResolvedOutcome)
    assert invocations == ["agentA", "agentA", "agentA", "agentA", "agentB"], (
        "the false-return drop must restart the streak: demotion only after "
        f"3 further no-progress attempts; got {invocations}"
    )
    _assert_demoted(logs, "agentA", consecutive=3, total=4)
    _assert_not_ambiguous(logs)
    assert len(observer.marker_scans) == len(invocations), (
        "exactly ONE unresolved-set observation must precede each attempt's "
        f"classification/ledger charge; got {len(observer.marker_scans)} scans "
        f"for {len(invocations)} invocations"
    )


def test_D5_active_failed_outcome_reuses_the_single_observation_for_repair(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    observer = _ScriptedObserver(_CONFLICTED)
    outcome, invocations, _, _session = _drive_captured(
        tmp_path,
        candidates=("agentA",),
        scripts={"agentA": (_AttemptStep(surviving=(), active_failure=True),)},
        observer=observer,
        clock=frozen_clock,
    )

    assert isinstance(outcome, ResolvedOutcome)
    assert invocations == ["agentA"], (
        f"the repaired worktree must end the stop in one attempt; got {invocations}"
    )
    assert len(observer.marker_scans) == len(invocations), (
        "exactly ONE unresolved-set observation must serve both the "
        "classification/ledger charge and the repair decision; got "
        f"{len(observer.marker_scans)} scans for {len(invocations)} invocations"
    )


def test_E_sole_resolver_demoted_reports_pending_without_operator_handoff(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("solo",),
        scripts={"solo": _no_progress(3)},
        clock=frozen_clock,
    )

    assert isinstance(outcome, RetryLaterOutcome)
    assert outcome.on_change is not None
    assert invocations == ["solo", "solo", "solo"]
    assert any(
        f"Sole agent 'solo' demoted in phase '{_RESOLUTION_PHASE}'" in line for line in logs
    ), "the sole-resolver demotion must carry its clear message"
    _assert_demoted(logs, "solo", consecutive=3, total=3)
    assert any(_typed_reason(6) in line for line in logs)
    _assert_not_ambiguous(logs)
    assert not any("no resolver can repair" in line for line in logs), (
        "a demotion-driven stop is pending work, not an operator escalation"
    )
