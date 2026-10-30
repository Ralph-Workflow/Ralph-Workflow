"""PRODUCT_CRITERIA §11 proofs: a known unresolved-conflict failure is typed, never AMBIGUOUS.

§11 headline: a resolver that exits "success" while the same 6 paths stay
conflicted did an **unproductive attempt**. Every fact needed to say so is
already typed in the driver (``ResolutionFailureCause.UNRESOLVED_CONFLICT``,
``ResolutionTerminationReason.RESOLUTION_INCOMPLETE``); the owner must be
handed a typed fact, not a prose sentence to re-match. Post-fix:

* the failure classifies as ``FailureCategory.AGENT`` (budget-debited, not a
  free retry) with the typed reason
  ``"agent reported success but {N} conflicted path(s) remain"``;
* nothing is logged as ambiguous or "flagged for review";
* progress is a fact: an attempt after which the unresolved count dropped (or
  the workspace probe changed) restarts the consecutive-unproductive streak;
* a genuinely unrecognised *external* error still classifies AMBIGUOUS and is
  bounded by the same consecutive (3) and total (9) unproductive limits.

TDD gate (U1 of the §11 fix): every scenario drives the public entry points
:func:`run_conflict_resolution_outcome` /
:func:`run_rebase_conflict_resolution_outcome` with a fake ``invoke`` runner,
U0's injectable :class:`WorktreeConflictObserver` scripting per-round
surviving marker sets, and a FakeClock :class:`RecoveryController` attached
through the session. No monkeypatch, no private counter reads, no import of
the typed enum U3 introduces. Assertions are on captured log text, agent
invocation order, and public terminal fields only.

Status: A/B/C/E, D1, and D2 pass — the driver observes the round-level
unresolved set exactly ONCE through the injected observer before every
classification/ledger charge (success-with-unresolved, false return, and
raised exception alike), and hands the ledger
``workspace_changed=(probe_changed or count_dropped)`` on every branch, so
a count drop restarts only the consecutive streak while the total still
accrues. D3/D4 prove the exception and false-return branches carry
``count_dropped`` progress; D1 pins the RAW/external bounding that must not
change.
"""

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
from ralph.pipeline.conflict_resolution.prompt import PromptMaterializer
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.sight import ConflictSight
from ralph.pipeline.conflict_resolution.worktree_observer import (
    GitWorktreeConflictObserver,
    WorktreeConflictObserver,
)
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.policy.loader import default_dir, load_policy
from ralph.policy.models import AgentChainConfig
from tests._resolver_controller import make_resolver_controller

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
    from ralph.policy.models import PolicyBundle

#: The 6 paths the §11 incident observed staying conflicted every round.
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


# ---------------------------------------------------------------------------
# Observable-boundary doubles (the only things this suite supplies)
# ---------------------------------------------------------------------------


class _FrozenSleepClock:
    """Installed wall-clock seam whose ``sleep`` never advances.

    A demotion that nothing brings back inside the scenario is what makes
    the recorded invocation order the order production chose, not the
    order a timer happened to allow. Mirrors ``FrozenClock`` in
    ``tests/pipeline/_auto_integrate_agent_failure_fixtures.py`` and is
    installed through the public :func:`install_executors` seam, exactly
    like the §7.5 suite.
    """

    def __init__(self) -> None:
        self._now = 1_000.0

    def now(self) -> float:
        """Return the frozen wall-clock reading."""
        return self._now

    def monotonic(self) -> float:
        """Return the frozen monotonic reading."""
        return self._now

    def epoch(self) -> float:
        """Return the frozen epoch reading."""
        return self._now

    def now_utc(self) -> datetime:
        """Return the frozen UTC wall-clock reading."""
        return datetime.fromtimestamp(self._now, tz=UTC)

    def sleep(self, seconds: float) -> None:
        """Wait without advancing: a frozen clock expires no cooldown."""
        del seconds


@pytest.fixture
def frozen_clock() -> Iterator[_FrozenSleepClock]:
    """Install the never-advancing clock seam for the duration of one test."""
    clock = _FrozenSleepClock()
    token = install_executors(clock=clock)
    try:
        yield clock
    finally:
        reset_executors(token)


class _ScriptedObserver(WorktreeConflictObserver):
    """Scripts every worktree read the round loop's verdict rests on.

    ``surviving`` is the set of paths the on-disk marker re-scan reports;
    the scripted runner shrinks it to script count drops and clears it to
    script a resolution. Existence/emptiness checks delegate to the
    production observer's content checks (they read the real temporary
    workspace and touch no git).
    """

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
    """Writes a real prompt file per round and records the request."""

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
    """One scripted invocation of one resolution agent.

    ``surviving`` is the unresolved set the on-disk re-scan reports AFTER
    this invocation (``None`` = leave unchanged); it is applied BEFORE
    ``raw_error`` is raised, so an exception can carry observable progress
    (the D2-exception interleave). ``raw_error`` turns the invocation into
    an exception whose text is genuinely unrecognised external output —
    the external half of the §11.4 rewording proof. ``report_success=False``
    scripts a bare unsuccessful return (the generic attempt-failed shape)
    instead of the default success claim.
    """

    surviving: tuple[str, ...] | None = None
    raw_error: str | None = None
    report_success: bool = True


class _ScriptedRunner:
    """Fake ``invoke`` runner playing each agent's scripted per-attempt steps."""

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

    def __call__(self, agent_name: str, prompt_path: Path, round_index: int) -> bool:
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
        return step.report_success


# ---------------------------------------------------------------------------
# Capture + shared assertions
# ---------------------------------------------------------------------------


@contextmanager
def _captured_logs() -> Iterator[list[str]]:
    """Collect everything production logged while the block runs."""
    captured: list[str] = []
    sink_id = loguru_logger.add(lambda message: captured.append(str(message)), level="INFO")
    try:
        yield captured
    finally:
        loguru_logger.remove(sink_id)


def _typed_reason(unresolved_count: int) -> str:
    """The §11.2 typed reason string U3 implements (Design item 3)."""
    return f"agent reported success but {unresolved_count} conflicted path(s) remain"


def _assert_not_ambiguous(logs: Sequence[str]) -> None:
    """A typed AGENT failure is never logged as ambiguous / flagged for review."""
    assert not any("flagged_for_review" in line for line in logs), (
        "an owner-known failure fact must never reach the ambiguous text fall-through"
    )
    assert not any("Ambiguous failure" in line for line in logs), (
        "an owner-known failure fact must never be logged as an ambiguous failure"
    )


def _assert_demoted(logs: Sequence[str], agent: str, *, consecutive: int, total: int) -> None:
    """The ledger demoted ``agent`` at exactly the asserted unproductive counts."""
    needle = (
        f"Agent '{agent}' demoted in phase '{_RESOLUTION_PHASE}' "
        f"(consecutive={consecutive}, total={total},"
    )
    assert any(needle in line for line in logs), (
        f"expected demotion log {needle!r} in:\n" + "\n".join(logs)
    )


# ---------------------------------------------------------------------------
# Scenario drivers
# ---------------------------------------------------------------------------


def _resolution_policy_bundle(candidates: Sequence[str]) -> PolicyBundle:
    """Shipped policy with only the resolution chain's agent list overridden."""
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
    """A fresh session whose recovery ledger runs on a FakeClock."""
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
) -> tuple[ResolutionOutcome, list[str], ResolutionSession]:
    """Drive one full resolution through a public entry point; return observable facts."""
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
) -> tuple[ResolutionOutcome, list[str], list[str], ResolutionSession]:
    """Drive one resolution while capturing every log line production emitted."""
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
    """Steps that report success while leaving ``count`` paths conflicted."""
    return tuple(_AttemptStep() for _ in range(count))


# ---------------------------------------------------------------------------
# Scenario A — same 6 paths, three rounds: AGENT, typed reason, demoted, next runs
# ---------------------------------------------------------------------------


def test_A_success_with_unchanged_conflicted_set_is_typed_agent_failure(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    """§11 proof 1: resolver exits success, the same 6 paths stay conflicted.

    Pre-U3 RED evidence: each round logs ``Ambiguous failure ...`` with
    ``[flagged_for_review=true]`` and the typed reason never appears, so
    the not-ambiguous and typed-reason assertions fail. Post-U3: category
    AGENT, the typed reason is logged, the resolver is demoted exactly at
    the consecutive limit (3, total 3), the next candidate lands, and
    nothing is ambiguous or "flagged for review".
    """
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts={"agentA": _no_progress(3), "agentB": (_AttemptStep(surviving=()),)},
        clock=frozen_clock,
    )

    assert outcome.succeeded is True
    assert invocations == ["agentA", "agentA", "agentA", "agentB"]
    assert any(_typed_reason(6) in line for line in logs), (
        "the typed failure reason must be logged, not rediscovered from prose"
    )
    _assert_demoted(logs, "agentA", consecutive=3, total=3)
    _assert_not_ambiguous(logs)


# ---------------------------------------------------------------------------
# Scenario B1 — count drops 6 -> 4 -> 2: the streak restarts, nobody is demoted
# ---------------------------------------------------------------------------


def test_B1_count_drop_restarts_the_consecutive_streak(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    """§11 proof 2 (drops): progress restarts the unproductive streak.

    Post-U3: agentA alone resolves (6 -> 4 -> 0) and is never demoted.
    Pre-U3 RED evidence: rounds 1-2 log the ambiguous free-retry lines and
    the typed reason is absent, so the not-ambiguous and typed-reason
    assertions fail (the invocation order alone cannot distinguish).
    """
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

    assert outcome.succeeded is True
    assert invocations == ["agentA", "agentA", "agentA"]
    assert not any("demoted" in line for line in logs), (
        "a resolver whose attempts drop the count must not be demoted"
    )
    assert any(_typed_reason(4) in line for line in logs)
    assert any(_typed_reason(2) in line for line in logs)
    _assert_not_ambiguous(logs)


# ---------------------------------------------------------------------------
# Scenario B2 — drop, then no progress: demotion only after 3 FURTHER attempts
# ---------------------------------------------------------------------------


def test_B2_demotion_requires_three_no_progress_attempts_after_a_drop(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    """§11 proof 2 (restart + disjunction): 6 -> 4, then count frozen at 4.

    Post-U3: the drop restarts the streak, so demotion fires only on the
    third no-progress attempt afterwards — four agentA invocations, then
    agentB lands. Pre-U3 RED evidence: the probe-only fact means the drop
    never restarts anything, agentA is demoted on its third invocation,
    and only three agentA invocations are recorded.
    """
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

    assert outcome.succeeded is True
    assert invocations == ["agentA", "agentA", "agentA", "agentA", "agentB"], (
        "the drop must restart the streak: demotion only after 3 further "
        f"no-progress attempts; got {invocations}"
    )
    _assert_demoted(logs, "agentA", consecutive=3, total=4)
    _assert_not_ambiguous(logs)


# ---------------------------------------------------------------------------
# Scenario C — selection after demotion (§11.5) + §7 pending, cross-phase identity
# ---------------------------------------------------------------------------

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
    """One paused rebase stop conflicting on the six §11 paths."""
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
    """§11.5: the demoted resolver is skipped for a stated reason; the next
    eligible candidate lands; a demotion in the resolution phase does not
    touch the same agent's standing in other phases.

    With seven candidates, the round-4 selection line must name the
    demoted ``lead`` with its cooldown reason and exactly five
    ``lower_priority`` skips (third..seventh). Pre-U3 RED evidence: the
    ambiguous free-retry lines replace the typed reason, so the
    not-ambiguous and typed-reason assertions fail while the selection
    and demotion mechanics already behave.
    """
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

    assert outcome.succeeded is True
    assert invocations == ["lead", "lead", "lead", "second"]
    selection_lines = [line for line in logs if "Selected agent second" in line]
    assert selection_lines, "the next eligible candidate must be selected after demotion"
    assert all(line.count("lower_priority") == 5 for line in selection_lines), (
        f"expected exactly five lower_priority skips in: {selection_lines}"
    )
    assert all("lead" in line for line in selection_lines), (
        "the demoted resolver must be named in the skip evidence"
    )
    _assert_demoted(logs, "lead", consecutive=3, total=3)
    assert any(_typed_reason(6) in line for line in logs)
    _assert_not_ambiguous(logs)

    # §7: the (phase, agent) ledger key is independent — the resolution-phase
    # demotion of ``lead`` must not change its development-phase standing.
    controller = session.recovery_controller
    assert controller is not None
    selection = controller.preferred_agent_index("development", ("lead", "second"))
    assert selection.agent == "lead"
    assert all(skipped_agent != "lead" for skipped_agent, _ in selection.skipped_reasons)


def test_C_all_resolvers_demoted_reports_pending_not_failed(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    """§11.5/§7: every resolver demoted -> the rebase stop is reported
    pending (unresolved, not an operator handoff).

    Post-U3: both candidates are demoted at the consecutive limit, the
    outcome is unsuccessful with the full unresolved set preserved, and
    no escalation names the operator. Pre-U3 RED evidence: the ambiguous
    free-retry lines and the absent typed reason.
    """
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts={"agentA": _no_progress(3), "agentB": _no_progress(3)},
        stop=_rebase_stop(),
        clock=frozen_clock,
    )

    assert outcome.succeeded is False
    assert outcome.unresolved_paths == _CONFLICTED
    assert outcome.reason is not ResolutionTerminationReason.OUT_OF_REACH
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


# ---------------------------------------------------------------------------
# Scenario D1 — RAW external errors: still AMBIGUOUS, bounded by the consecutive limit
# ---------------------------------------------------------------------------

#: Genuinely unrecognised EXTERNAL error texts (§11.4): nothing Ralph wrote.
_RAW_EXTERNAL_TEXTS = (
    "provider transient fault 0xA4: azimuth thruster miscalibration",
    "upstream relay hung up mid-frame: reason code QZZ-77 (undocumented)",
    "quota gateway answered with a haiku and closed the connection",
)


def test_D1_unrecognised_external_error_stays_ambiguous_and_hits_consecutive_limit(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    """§11 proof 4 (consecutive half): RAW failures with varied external
    text, unchanged workspace, unchanged unresolved set.

    This must pass BOTH before and after U3: unrecognised external output
    keeps classifying AMBIGUOUS (``flagged_for_review=true`` is expected
    here) and the ledger demotes at exactly 3 consecutive unproductive
    attempts, then the next candidate lands. The varied texts are the
    external half of the rewording proof: the category rides on the
    external text, never on a Ralph-authored sentence.
    """
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("agentA", "agentB"),
        scripts={
            "agentA": tuple(_AttemptStep(raw_error=text) for text in _RAW_EXTERNAL_TEXTS),
            "agentB": (_AttemptStep(surviving=()),),
        },
        clock=frozen_clock,
    )

    assert outcome.succeeded is True
    assert invocations == ["agentA", "agentA", "agentA", "agentB"], (
        f"three consecutive RAW failures demote, then the next agent runs; got {invocations}"
    )
    assert sum("Ambiguous failure classification" in line for line in logs) >= 3, (
        "each genuinely external error must classify AMBIGUOUS"
    )
    assert any("flagged_for_review=true" in line for line in logs)
    _assert_demoted(logs, "agentA", consecutive=3, total=3)


# ---------------------------------------------------------------------------
# Scenario D2 — RAW interleaved with progress: the TOTAL limit bounds it (U3 gate)
# ---------------------------------------------------------------------------


def test_D2_raw_failures_interleaved_with_progress_hit_the_total_limit(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    """§11 proof 4 (total half) + §11.3: the disjunction ``probe_changed or
    count_dropped`` makes count-drop progress restart the streak.

    Pattern: RAW, drop (6->5), RAW, drop (5->4), RAW, drop (4->3), RAW,
    drop (3->2), RAW. The count drop after every RAW restarts the
    consecutive streak, so the resolver is NOT demoted at 3 consecutive;
    it is demoted (spent) at the 9th total unproductive attempt, and only
    then does the next candidate run.

    U3 acceptance gate — PRE-U3 RED evidence (observed 2026-10-29): the
    RAW exception branch charges the ledger with probe-only
    ``workspace_changed=False`` BEFORE the round-level unresolved
    observation, so the count drops never restart the streak; agentA is
    demoted (cooldown) on its 3rd invocation, ``invocations`` is
    ``["agentA"] * 3 + ["agentB"]``, and the demotion line reads
    ``consecutive=3, total=3, reason=consecutive_unproductive_limit_reached``
    instead of ``total=9, reason=total_unproductive_limit_reached``.
    """
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

    assert outcome.succeeded is True
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


# ---------------------------------------------------------------------------
# Scenario D3 — an exception that CARRIES progress: the drop restarts the streak
# ---------------------------------------------------------------------------


def test_D3_exception_that_drops_the_unresolved_set_restarts_the_streak(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    """§11 proof 4 (exception branch): the single observation runs BEFORE the
    exception's charge, so an exception that repaired 6 -> 4 paths is
    observable progress.

    Pattern: RAW-with-drop (6->4), then three RAW no-progress attempts. The
    drop must restart the consecutive streak even though the attempt raised,
    so agentA is demoted only on its fourth invocation (consecutive=3,
    total=4) and agentB then lands. The exception text is still genuinely
    external: every RAW attempt keeps classifying AMBIGUOUS.
    """
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

    assert outcome.succeeded is True
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


# ---------------------------------------------------------------------------
# Scenario D4 — a false return that CARRIES progress: the drop restarts the streak
# ---------------------------------------------------------------------------


def test_D4_false_return_with_count_drop_restarts_the_streak(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    """§11 proof 4 (false-return branch): a bare unsuccessful return whose
    attempt still repaired 6 -> 4 paths is observable progress.

    The false-return charge used to ride on the workspace probe only; with
    the single observation boundary it carries ``count_dropped`` too, so
    the streak restarts and agentA is demoted only on its fourth invocation
    (consecutive=3, total=4) before agentB lands. A bare unsuccessful
    return is the typed generic attempt-failed shape, never AMBIGUOUS.
    """
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

    assert outcome.succeeded is True
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


# ---------------------------------------------------------------------------
# Scenario E — sole resolver demoted: pending with a clear message, no operator handoff
# ---------------------------------------------------------------------------


def test_E_sole_resolver_demoted_reports_pending_without_operator_handoff(
    tmp_path: Path, frozen_clock: _FrozenSleepClock
) -> None:
    """§11/§7: the only resolver does nothing three times and is demoted.

    The phase stops with the full unresolved set preserved, the demotion
    is named for the sole agent, and nothing escalates to the operator.
    Pre-U3 RED evidence: the ambiguous free-retry lines replace the typed
    reason, so the not-ambiguous and typed-reason assertions fail.
    """
    outcome, invocations, logs, _ = _drive_captured(
        tmp_path,
        candidates=("solo",),
        scripts={"solo": _no_progress(3)},
        clock=frozen_clock,
    )

    assert outcome.succeeded is False
    assert outcome.unresolved_paths == _CONFLICTED
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
