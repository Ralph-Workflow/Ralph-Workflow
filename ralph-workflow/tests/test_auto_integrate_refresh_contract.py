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
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_ALREADY_CURRENT,
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
    # One refresh at context resolution, one immediately before the
    # fast-forward observes the target SHA.
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
