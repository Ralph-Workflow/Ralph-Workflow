"""Scenario tests for the observer/materializer seam of the round loop.

These tests drive FULL resolution rounds through the public entry point
:func:`run_conflict_resolution_outcome` with only a fake
:class:`WorktreeConflictObserver`, a fake :class:`PromptMaterializer`,
and a temporary workspace. Nothing here monkeypatches driver internals:
the fake observer scripts every filesystem/git read the round loop's
verdict rests on, and the fake materializer stands in for the prompt
render+write.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_outcome
from ralph.pipeline.conflict_resolution.prompt import PromptMaterializer
from ralph.pipeline.conflict_resolution.sight import ConflictSight
from ralph.pipeline.conflict_resolution.worktree_observer import WorktreeConflictObserver
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.driver import ResolutionInvoker
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

_CONFLICTED = ("src/alpha.py", "docs/beta.md")
_POLICY_BUNDLE = load_policy(Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults")
_CONFIG = UnifiedConfig.model_validate({"general": {}})

pytestmark = pytest.mark.timeout_seconds(5)


class _FakeObserver(WorktreeConflictObserver):
    """Scripts every read the round loop's verdict rests on."""

    def __init__(
        self,
        *,
        unmerged: Sequence[str],
        surviving_per_scan: Sequence[Sequence[str]],
    ) -> None:
        self._unmerged = tuple(unmerged)
        self._surviving = [tuple(scan) for scan in surviving_per_scan]
        self.marker_scans: list[tuple[str, ...]] = []
        self.classified: list[tuple[str, ...]] = []

    def unmerged_paths(self, root: Path) -> Sequence[str]:
        return self._unmerged

    def retained_merge_paths(self, root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
        return paths

    def classify_unmerged_conflicts(
        self, root: Path, paths: tuple[str, ...]
    ) -> dict[str, ConflictSight]:
        self.classified.append(paths)
        return dict.fromkeys(paths, ConflictSight.AGENT)

    def declared_decision_paths(self, kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
        return ()

    def stage_mechanical_conflicts(
        self, root: Path, kinds: dict[str, ConflictSight]
    ) -> tuple[str, ...]:
        return ()

    def paths_with_conflict_markers(
        self, root: Path, paths: tuple[str, ...]
    ) -> Sequence[str]:
        index = min(len(self.marker_scans), len(self._surviving) - 1)
        self.marker_scans.append(tuple(paths))
        return self._surviving[index]

    def path_exists(self, path: Path) -> bool:
        return True

    def is_effectively_empty(self, path: Path) -> bool:
        return False


class _FakeMaterializer(PromptMaterializer):
    """Records each round's materialization request and writes a real prompt file."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.rounds: list[int] = []
        self.conflicted_per_round: list[tuple[str, ...]] = []
        self.surviving_feedback: list[tuple[str, ...]] = []
        self.rendered_bodies: list[str] = []

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
        del root, target, round_cap, stop, strategy_history
        self.rounds.append(round_index)
        self.conflicted_per_round.append(tuple(conflicted_paths))
        self.surviving_feedback.append(
            () if round_index == 1 else tuple(session.unresolved_paths)
        )
        prompt_path = self._workspace / ".agent" / "tmp" / f"round_{round_index}_prompt.md"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("resolve the conflicts", encoding="utf-8")
        # The driver unlinks the prompt when the round ends, so capture
        # the rendered body now rather than reading the file back later.
        self.rendered_bodies.append(prompt_path.read_text(encoding="utf-8"))
        return prompt_path


def _run(
    workspace: Path,
    *,
    observer: _FakeObserver,
    materializer: _FakeMaterializer,
    invoke: ResolutionInvoker,
) -> ResolutionOutcome:
    return run_conflict_resolution_outcome(
        root=workspace,
        target="main",
        config=_CONFIG,
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_POLICY_BUNDLE,
        display=None,
        display_context=None,
        invoke=invoke,
        observer=observer,
        prompt_materializer=materializer,
    )


def test_full_round_resolves_through_fake_observer_and_materializer(tmp_path: Path) -> None:
    observer = _FakeObserver(unmerged=_CONFLICTED, surviving_per_scan=[[]])
    materializer = _FakeMaterializer(tmp_path)
    invoked: list[tuple[str, Path, int]] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        invoked.append((agent_name, prompt_path, round_index))
        return True

    outcome = _run(tmp_path, observer=observer, materializer=materializer, invoke=_invoke)

    assert outcome.succeeded is True
    assert [round_index for _, _, round_index in invoked] == [1]
    assert materializer.rendered_bodies == ["resolve the conflicts"]
    assert materializer.rounds == [1]
    assert materializer.conflicted_per_round == [_CONFLICTED]
    assert observer.classified == [_CONFLICTED]
    assert observer.marker_scans == [_CONFLICTED]


def test_surviving_markers_drive_a_second_round_with_feedback(tmp_path: Path) -> None:
    """Round 2 exists because the on-disk re-scan says round 1 left markers."""
    observer = _FakeObserver(unmerged=_CONFLICTED, surviving_per_scan=[["src/alpha.py"], []])
    materializer = _FakeMaterializer(tmp_path)
    invoked_rounds: list[int] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del agent_name, prompt_path
        invoked_rounds.append(round_index)
        return True

    outcome = _run(tmp_path, observer=observer, materializer=materializer, invoke=_invoke)

    assert outcome.succeeded is True
    assert invoked_rounds == [1, 2]
    assert materializer.rounds == [1, 2]
    assert materializer.surviving_feedback == [(), ("src/alpha.py",)]
    assert observer.marker_scans == [_CONFLICTED, _CONFLICTED]


def test_empty_unmerged_set_exits_without_invoking_anyone(tmp_path: Path) -> None:
    observer = _FakeObserver(unmerged=[], surviving_per_scan=[[]])
    materializer = _FakeMaterializer(tmp_path)
    invoked: list[int] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        invoked.append(round_index)
        return True

    outcome = _run(tmp_path, observer=observer, materializer=materializer, invoke=_invoke)

    assert outcome.succeeded is False
    assert invoked == []
    assert materializer.rounds == []


def test_markers_that_never_clear_decline_the_merge(tmp_path: Path) -> None:
    """The repository outranks the agent: surviving markers are not a resolution."""
    observer = _FakeObserver(unmerged=_CONFLICTED, surviving_per_scan=[_CONFLICTED])
    materializer = _FakeMaterializer(tmp_path)

    outcome = _run(
        tmp_path,
        observer=observer,
        materializer=materializer,
        invoke=lambda _agent, _prompt, _round: True,
    )

    assert outcome.succeeded is False
    assert outcome.unresolved_paths == _CONFLICTED
