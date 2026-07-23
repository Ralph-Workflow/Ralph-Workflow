"""The mainline pointer must be re-read immediately before it is landed on.

Auto-integration used to refresh ``refs/heads/<target>`` from origin
exactly once, in ``_auto_integrate_resolve_context``, and then run the
rebase, the endpoint merge and -- when configured -- a full dev-agent
conflict resolution bounded at 900 s by default BEFORE the fast-forward
observed the target SHA. With several agents landing on the same
mainline continuously, that pointer is minutes stale by the time it
matters. These tests pin the second refresh, its disabled counterpart,
and the fact that the refresh outcome is reported to the operator
instead of being silently swallowed.

File-level markers. ``subprocess_e2e`` excludes this file from ``make
test`` (the budget-tracked 60 s step) because
:func:`auto_integrate_after_commit` calls
``_current_branch_or_detached_marker``, which opens a real GitPython
``Repo``, so the ``tmp_git_repo`` fixture is required.
``timeout_seconds(5)`` sizes the budget for that real git I/O, matching
the convention in tests/test_auto_integrate_race.py. This does not
weaken any cap: the file stays out of the 60 s combined budget and
inside the 60 s per-suite cap on ``make test-subprocess-e2e``.

The ``_run`` / ``_base_branch`` / ``_commit`` / ``_build_config``
helpers are duplicated here to keep this file standalone, matching the
convention documented at tests/test_auto_integrate_race.py:11-15.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.auto_integrate_message import format_auto_integrate_message
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.auto_integrate_recovery import TerminalStateViolationError
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_ALREADY_CURRENT,
    REFRESH_NO_ORIGIN,
    REFRESH_SUPPRESSED,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _run(
    repo_root: Path, *args: str, timeout: float = 10.0
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root`` with a configurable timeout."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    """Return the seed template's default branch name."""
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(
    *,
    target: str,
    fetch_enabled: bool = True,
) -> UnifiedConfig:
    """Build a real ``UnifiedConfig`` with the auto-integrate knobs set."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_fetch_enabled": fetch_enabled,
            }
        }
    )


def _feature_ahead_of_base(tmp_git_repo: Path) -> str:
    """Put one commit on ``feature`` beyond an unchanged base branch."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    return base


def _record_refreshes(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> list[str]:
    """Replace the origin refresh with a recorder returning ``outcome``."""
    calls: list[str] = []

    def _fake_refresh(
        repo_root: Path, target: str, *, timeout_seconds: float
    ) -> str:
        calls.append(target)
        return outcome

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_refresh.refresh_target_from_remote",
        _fake_refresh,
    )
    return calls


def test_auto_integrate_regression_target_is_refreshed_again_before_landing(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The landing observation is bound to a pointer read seconds ago."""
    base = _feature_ahead_of_base(tmp_git_repo)
    calls = _record_refreshes(monkeypatch, REFRESH_ALREADY_CURRENT)

    outcome = auto_integrate_after_commit(
        _build_config(target=base), WorkspaceScope(tmp_git_repo), RebaseState()
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    # One refresh at context resolution, immediately before the rebase
    # leg, and one immediately before the fast-forward observes the
    # target SHA. Every retry adds its own pair, because the retry loop
    # re-refreshes at the top before calling back in.
    #
    # Exactly two, not three: a further refresh inside the integration
    # pass would sit between two reads that already bracket the rebase,
    # and ``rebase_onto`` hands git the target BY NAME so the replay
    # resolves the freshest ref itself. The extra git subprocess on every
    # integration is real cost against the immutable 60 s test budget for
    # no freshness this pair does not already provide.
    assert calls == [base, base]


def test_fetch_disabled_skips_both_refreshes_and_still_lands_locally(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative case: no fetch configured means no fetch attempted."""
    base = _feature_ahead_of_base(tmp_git_repo)
    calls = _record_refreshes(monkeypatch, REFRESH_ALREADY_CURRENT)

    outcome = auto_integrate_after_commit(
        _build_config(target=base, fetch_enabled=False),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )

    assert calls == []
    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert (
        _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
        == _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    )


def test_refresh_outcome_is_recorded_and_rendered_for_the_operator(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable mainline degrades to local-only, but never silently."""
    base = _feature_ahead_of_base(tmp_git_repo)
    _record_refreshes(monkeypatch, REFRESH_UNREACHABLE)

    outcome = auto_integrate_after_commit(
        _build_config(target=base), WorkspaceScope(tmp_git_repo), RebaseState()
    )

    assert outcome is not None
    assert outcome.last_refresh == REFRESH_UNREACHABLE
    # Fail-open is preserved: an unreachable remote still lands locally.
    assert outcome.fast_forwarded is True
    message = format_auto_integrate_message(
        outcome.last_action,
        outcome.last_target,
        outcome.last_reason,
        fast_forwarded=outcome.fast_forwarded,
        refresh=outcome.last_refresh,
    )
    assert REFRESH_UNREACHABLE in message


def _feature_level_with_base(tmp_git_repo: Path) -> str:
    """Put ``feature`` at exactly the base tip: nothing to integrate."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    return base


def test_unreachable_refresh_is_recorded_on_the_early_no_op_skip(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unhealthy refresh must not be silenced by an early no-op.

    ``no commits beyond target`` is decided from the target pointer the
    refresh was supposed to freshen. When that refresh could not reach
    origin, the "nothing to do" verdict rests on a pointer of unknown
    age, so the outcome that produced it has to travel with the record
    instead of being discarded at the context-resolution seam.
    """
    base = _feature_level_with_base(tmp_git_repo)
    calls = _record_refreshes(monkeypatch, REFRESH_UNREACHABLE)

    outcome = auto_integrate_after_commit(
        _build_config(target=base), WorkspaceScope(tmp_git_repo), RebaseState()
    )

    assert calls == [base]
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_reason == "no commits beyond target"
    assert outcome.last_refresh == REFRESH_UNREACHABLE
    message = format_auto_integrate_message(
        outcome.last_action,
        outcome.last_target,
        outcome.last_reason,
        fast_forwarded=outcome.fast_forwarded,
        refresh=outcome.last_refresh,
    )
    assert REFRESH_UNREACHABLE in message


def test_unreachable_refresh_is_recorded_at_the_phase_boundary_no_op(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The quiet phase-boundary hook stays quiet only while origin is readable."""
    base = _feature_level_with_base(tmp_git_repo)
    _record_refreshes(monkeypatch, REFRESH_UNREACHABLE)

    outcome = auto_integrate_on_phase_transition(
        _build_config(target=base), WorkspaceScope(tmp_git_repo), RebaseState()
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_refresh == REFRESH_UNREACHABLE
    message = format_auto_integrate_message(
        outcome.last_action,
        outcome.last_target,
        outcome.last_reason,
        fast_forwarded=outcome.fast_forwarded,
        refresh=outcome.last_refresh,
    )
    assert REFRESH_UNREACHABLE in message


def _diverged_conflicting_repo(tmp_git_repo: Path) -> str:
    """Diverge ``feature`` and base on the SAME line so the rebase conflicts."""
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    seed = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    _run(tmp_git_repo, "checkout", "feature")
    return base


def test_unreachable_refresh_is_recorded_on_the_plain_conflict(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-04: the conflict record says how fresh the pointer was too.

    The success path stamped ``last_refresh`` from the start; every
    conflict short circuit returned without it, so on exactly the path
    the operator most needs to diagnose -- integration stopped on a
    conflict -- nothing reported whether the mainline pointer the
    decision was made against had even been readable.
    """
    base = _diverged_conflicting_repo(tmp_git_repo)
    _record_refreshes(monkeypatch, REFRESH_UNREACHABLE)

    outcome = auto_integrate_after_commit(
        _build_config(target=base), WorkspaceScope(tmp_git_repo), RebaseState()
    )

    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert outcome.last_refresh == REFRESH_UNREACHABLE


def test_unreachable_refresh_is_recorded_when_the_abort_leaves_a_rebase(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-04: the rebase-in-progress-after-abort short circuit carries it."""
    import ralph.pipeline.auto_integrate_rebase_merge as _rm_mod

    base = _diverged_conflicting_repo(tmp_git_repo)
    refresh_calls = _record_refreshes(monkeypatch, REFRESH_UNREACHABLE)

    def _failing_abort(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated abort failure")

    monkeypatch.setattr(_rm_mod, "abort_rebase", _failing_abort)

    with pytest.raises(TerminalStateViolationError, match=r"rebase-merge|REBASE_HEAD"):
        auto_integrate_after_commit(
            _build_config(target=base), WorkspaceScope(tmp_git_repo), RebaseState()
        )

    record = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    assert record.exists(), "R6: leaked rebase state must retain its recovery record"
    assert refresh_calls == [base], "AC-04: the failed attempt still recorded its refresh"


def test_unreachable_refresh_is_recorded_when_the_merge_attempt_raises(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-04: the merge-exception short circuit carries it."""
    import ralph.pipeline.auto_integrate_rebase_merge as _rm_mod

    base = _diverged_conflicting_repo(tmp_git_repo)
    _record_refreshes(monkeypatch, REFRESH_UNREACHABLE)
    monkeypatch.setattr(
        _rm_mod, "endpoint_merge_with_resolution", lambda *_a, **_k: None
    )

    outcome = auto_integrate_after_commit(
        _build_config(target=base), WorkspaceScope(tmp_git_repo), RebaseState()
    )

    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert (
        outcome.last_reason
        == "rebase conflict followed by merge attempt exception"
    )
    assert outcome.last_refresh == REFRESH_UNREACHABLE


def test_unreachable_refresh_is_recorded_on_the_resolution_failed_record(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-04: the resolution-failed record carries it."""
    import ralph.pipeline.auto_integrate_rebase_merge as _rm_mod
    from ralph.git.merge import MergeResult
    from ralph.pipeline.auto_integrate_resolve import RESOLUTION_FAILED

    base = _diverged_conflicting_repo(tmp_git_repo)
    _record_refreshes(monkeypatch, REFRESH_UNREACHABLE)
    monkeypatch.setattr(
        _rm_mod,
        "endpoint_merge_with_resolution",
        lambda *_a, **_k: MergeResult(outcome=RESOLUTION_FAILED),
    )

    outcome = auto_integrate_after_commit(
        _build_config(target=base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=lambda *_a, **_k: False,
    )

    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert outcome.last_reason == "conflict resolution failed; merge aborted"
    assert outcome.last_refresh == REFRESH_UNREACHABLE


def test_no_origin_refresh_is_recorded_at_the_phase_boundary_no_op(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``no origin remote`` is the LEAST trustworthy outcome, not a healthy one.

    Defect this pins (it lived in
    ``auto_integrate_context._HEALTHY_REFRESH_OUTCOMES``): the constant
    was left in the healthy set after its meaning changed. It used to
    mean "local fleet, no remote configured", which really is harmless;
    :mod:`ralph.pipeline.auto_integrate_sync` now returns
    ``REFRESH_LOCAL_FLEET`` for that topology and reserves
    ``REFRESH_NO_ORIGIN`` for a target that could not be observed AT ALL
    -- remotely or locally. While it stayed in the healthy set, the one
    outcome that can vouch for nothing silenced the very staleness
    record that exists to expose it.
    """
    base = _feature_level_with_base(tmp_git_repo)
    _record_refreshes(monkeypatch, REFRESH_NO_ORIGIN)

    outcome = auto_integrate_on_phase_transition(
        _build_config(target=base), WorkspaceScope(tmp_git_repo), RebaseState()
    )

    assert outcome is not None, (
        "a boundary decided from an unobservable target must be recorded"
    )
    assert outcome.last_action == "skipped"
    assert outcome.last_refresh == REFRESH_NO_ORIGIN


def test_a_suppressed_refresh_is_recorded_rather_than_absent(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent refresh vouches for nothing, so it cannot count as healthy.

    Defect this pins (it lived in
    ``auto_integrate_context.refresh_outcome_is_healthy``, which
    returned ``True`` for ``None``): when the boundary throttle
    suppressed the probe no refresh happened at all, and the resulting
    ``None`` was classified HEALTHY -- so ``record_when_stale`` returned
    ``None`` and the whole boundary went silent. The suppression is now
    a first-class ``REFRESH_*`` outcome, so it is recorded and rendered
    like every other one.
    """
    base = _feature_level_with_base(tmp_git_repo)
    _record_refreshes(monkeypatch, REFRESH_SUPPRESSED)

    outcome = auto_integrate_on_phase_transition(
        _build_config(target=base), WorkspaceScope(tmp_git_repo), RebaseState()
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_refresh == REFRESH_SUPPRESSED
    message = format_auto_integrate_message(
        outcome.last_action,
        outcome.last_target,
        outcome.last_reason,
        fast_forwarded=outcome.fast_forwarded,
        refresh=outcome.last_refresh,
    )
    assert REFRESH_SUPPRESSED in message


def test_healthy_refresh_keeps_the_phase_boundary_no_op_silent(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unchanged contract: a healthy nothing-to-do boundary records nothing."""
    base = _feature_level_with_base(tmp_git_repo)
    _record_refreshes(monkeypatch, REFRESH_ALREADY_CURRENT)

    assert (
        auto_integrate_on_phase_transition(
            _build_config(target=base), WorkspaceScope(tmp_git_repo), RebaseState()
        )
        is None
    )
