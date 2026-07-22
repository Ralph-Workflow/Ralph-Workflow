"""Tests for the bounded conflict-resolution driver.

The load-bearing contract is that the DETERMINISTIC marker scan decides
whether a round resolved anything. An agent that reports success over a
file still carrying ``<<<<<<<`` has resolved nothing, and the driver must
say so; ``git add`` clears a file's unmerged bit even with markers
intact, so the textual re-scan is the only remaining proof.

Every test injects the round runner and the git seams, so nothing here
launches a process or touches a repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import (
    run_conflict_resolution_pipeline,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.graph import MAX_RESOLUTION_ROUNDS
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
from ralph.pipeline.conflict_resolution.status import (
    NEUTRAL_PHASE_LABEL,
    PHASE_LABEL,
)
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["src/alpha.py", "docs/beta.md"]


class _FakeStatusBar:
    """Records the model the driver last pushed."""

    def __init__(self) -> None:
        self.last_model: object | None = None


class _FakeDisplay:
    """Display double recording status-bar pushes and warn lines."""

    def __init__(self) -> None:
        self.status_bar = _FakeStatusBar()
        self.models: list[object] = []
        self.warn_lines: list[str] = []

    def update_status_bar(self, model: object) -> None:
        self.models.append(model)
        self.status_bar.last_model = model

    def emit_warn_line(self, unit: str, channel: str, message: str) -> None:
        self.warn_lines.append(message)


@lru_cache(maxsize=1)
def _policy_bundle() -> PolicyBundle:
    """The real default policy, which declares the resolution drain."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {}})


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str],
    surviving_per_round: Sequence[Sequence[str]],
) -> list[Sequence[str]]:
    """Stub the two git queries the driver's verdict rests on."""
    scans: list[Sequence[str]] = []
    monkeypatch.setattr(
        driver_module, "unmerged_paths", lambda root: list(unmerged)
    )

    def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
        index = min(len(scans), len(surviving_per_round) - 1)
        scans.append(paths)
        return list(surviving_per_round[index])

    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)
    return scans


def _run(
    tmp_path: Path,
    *,
    invoke: driver_module.ResolutionInvoker,
    display: _FakeDisplay | None = None,
) -> bool:
    return run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=_config(),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=display,
        display_context=None,
        invoke=invoke,
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
    """Round 2's prompt must name what round 1 failed to resolve."""
    _install_seams(
        monkeypatch,
        unmerged=_CONFLICTED,
        surviving_per_round=[["src/alpha.py"], []],
    )
    feedback_seen: list[tuple[str, ...]] = []
    real_render = driver_module.render_conflict_prompt

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
        feedback_seen.append(tuple(surviving_marker_paths))
        return real_render(
            root=root,
            target=target,
            conflicted_paths=conflicted_paths,
            round_index=round_index,
            round_cap=round_cap,
            surviving_marker_paths=surviving_marker_paths,
            replaying_commit_sha=replaying_commit_sha,
            replaying_commit_subject=replaying_commit_subject,
            stop_index=stop_index,
            stop_cap=stop_cap,
        )

    monkeypatch.setattr(driver_module, "render_conflict_prompt", _spy_render)

    assert _run(tmp_path, invoke=lambda name, path, index: True) is True
    assert feedback_seen == [(), ("src/alpha.py",)]


def test_bounded_at_max_resolution_rounds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Markers that never clear exhaust the budget and decline."""
    _install_seams(
        monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[["src/alpha.py"]]
    )
    calls: list[int] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        calls.append(round_index)
        return True

    assert _run(tmp_path, invoke=_invoke) is False
    assert calls == list(range(1, MAX_RESOLUTION_ROUNDS + 1))


def test_invoker_exception_is_contained_and_returns_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(
        monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[["src/alpha.py"]]
    )

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        raise RuntimeError("agent exploded")

    assert _run(tmp_path, invoke=_invoke) is False


def test_agent_success_over_surviving_markers_is_not_a_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The repository outranks the agent's own claim."""
    _install_seams(
        monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[["src/alpha.py"]]
    )
    assert _run(tmp_path, invoke=lambda name, path, index: True) is False


def test_failed_invocation_with_clean_markers_is_not_a_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail closed: a round that did not run cannot claim the merge is clean."""
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
    display = _FakeDisplay()
    sentinel = object()
    display.status_bar.last_model = sentinel

    assert _run(tmp_path, invoke=lambda name, path, index: True, display=display) is True
    assert display.models[-1] is sentinel


def test_status_bar_is_not_left_on_the_resolution_label_without_a_prior_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-05: nothing to restore must still clear the resolution label.

    The pipeline is entered from four seams, one of which is the startup
    seam, where the run loop's next status-bar push can be a whole phase
    away. A footer left claiming ``Rebase Conflict Resolution`` after the
    pipeline has exited reads exactly like the hang the label exists to
    rule out.
    """
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
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
    """Presentation must never break integration, on the clear path too."""
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
    _install_seams(
        monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[["src/alpha.py"]]
    )
    display = _FakeDisplay()

    assert _run(tmp_path, invoke=lambda name, path, index: True, display=display) is False

    joined = "\n".join(display.warn_lines)
    assert "entering rebase conflict resolution" in joined
    assert "abandoning conflict resolution" in joined
    assert "src/alpha.py" in joined


def test_merge_mode_success_reports_committing_the_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The endpoint-merge path really does commit a merge next."""
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    display = _FakeDisplay()

    assert _run(tmp_path, invoke=lambda name, path, index: True, display=display) is True

    joined = "\n".join(display.warn_lines)
    assert "conflicts resolved in round 1; verifying and committing the merge" in joined


def test_rebase_mode_success_reports_continuing_the_rebase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The rebase path stages and continues; it commits no merge.

    Claiming a merge commit here is observably wrong phase feedback for
    the dedicated rebase-conflict workflow, so the wording is asserted.
    """
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
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=display,
        display_context=None,
        invoke=lambda name, path, index: True,
    )

    assert resolved is True
    joined = "\n".join(display.warn_lines)
    assert "conflicts resolved in round 1; verifying and continuing the rebase" in joined
    assert "committing the merge" not in joined
