from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_catchup as catchup
from ralph.pipeline.auto_integrate_catchup_coordination import remote_sync_transaction
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_UNREACHABLE,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


_LOCAL_SHA = "a" * 40
_REMOTE_SHA = "b" * 40


def _config(*, remote_enabled: bool = True, interval: float = 30.0) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": remote_enabled,
                "auto_integrate_remote": "origin",
                "auto_integrate_remote_interval_seconds": interval,
            }
        }
    )


def _open_local_gates(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    checkout_merges: list[str] = []
    monkeypatch.setattr(catchup, "_current_branch_name", lambda _root: "feature")
    monkeypatch.setattr(catchup, "resolve_integration_target", lambda _config, _root: "main")
    monkeypatch.setattr(catchup, "_worktree_is_clean", lambda _root: True)
    monkeypatch.setattr(catchup, "_still_safe_to_merge", lambda _root, _branch: True)
    monkeypatch.setattr(catchup, "is_ancestor", lambda _root, _older, _newer: True)
    monkeypatch.setattr(catchup, "find_main_worktree_root", lambda root: root)
    monkeypatch.setattr(catchup, "worktree_lookup", lambda _root, _target: ("not-found", None))

    def _observe(_root: Path, branch: str) -> tuple[str | None, bool]:
        return ("b" * 40 if branch == "main" else "a" * 40), True

    monkeypatch.setattr(catchup, "observe_branch_sha", _observe)
    monkeypatch.setattr(
        catchup,
        "fast_forward_via_worktree",
        lambda _root, sha: checkout_merges.append(sha) is None,
    )
    return checkout_merges


def _lease(*, acquired: bool = True, fetch_allowed: bool = True):
    @contextmanager
    def _acquire(_root: Path, _remote: str, _target: str) -> Iterator[object | None]:
        if not acquired:
            yield None
            return
        yield type("Lease", (), {"fetch_allowed": fetch_allowed, "record_fetch": lambda self: None})()

    return _acquire


def test_origin_ahead_advances_target_before_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []
    checkout_merges = _open_local_gates(monkeypatch)
    monkeypatch.setattr(catchup, "remote_sync_transaction", _lease())
    monkeypatch.setattr(
        catchup,
        "refresh_target_from_remote",
        lambda *_args, **_kwargs: events.append("fetch") or REFRESH_ORIGIN_AHEAD,
    )
    monkeypatch.setattr(catchup, "observe_remote_target_sha", lambda *_args: _REMOTE_SHA)

    def _advance(
        _root: Path,
        _target: str,
        sha: str,
        *,
        reclaim_target_worktree: bool,
    ) -> tuple[bool, str]:
        assert sha == _REMOTE_SHA
        assert reclaim_target_worktree is False
        events.append("advance-target")
        return True, ""

    monkeypatch.setattr(catchup, "fast_forward_target", _advance)
    original_checkout = catchup.fast_forward_via_worktree
    monkeypatch.setattr(
        catchup,
        "fast_forward_via_worktree",
        lambda root, sha: events.append("advance-checkout") or original_checkout(root, sha),
    )

    outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert events == ["fetch", "advance-target", "advance-checkout"]
    assert checkout_merges == [_REMOTE_SHA]


def test_target_advance_refusal_does_not_touch_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout_merges = _open_local_gates(monkeypatch)
    monkeypatch.setattr(catchup, "remote_sync_transaction", _lease())
    monkeypatch.setattr(
        catchup, "refresh_target_from_remote", lambda *_args, **_kwargs: REFRESH_ORIGIN_AHEAD
    )
    monkeypatch.setattr(catchup, "observe_remote_target_sha", lambda *_args: _REMOTE_SHA)
    monkeypatch.setattr(catchup, "fast_forward_target", lambda *_args, **_kwargs: (False, "cas"))

    outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)

    assert outcome == catchup.CATCHUP_REFUSED
    assert checkout_merges == []


@pytest.mark.parametrize("refresh_outcome", [REFRESH_DIVERGED, REFRESH_UNREACHABLE])
def test_non_ahead_refresh_never_advances_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, refresh_outcome: str
) -> None:
    _open_local_gates(monkeypatch)
    target_advances: list[str] = []
    monkeypatch.setattr(catchup, "remote_sync_transaction", _lease())
    monkeypatch.setattr(
        catchup, "refresh_target_from_remote", lambda *_args, **_kwargs: refresh_outcome
    )
    monkeypatch.setattr(
        catchup,
        "fast_forward_target",
        lambda *_args, **_kwargs: target_advances.append("advanced") or (True, ""),
    )

    catchup.attempt_catchup_fast_forward(_config(), tmp_path)

    assert target_advances == []


def test_held_coordination_lock_prevents_remote_and_checkout_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout_merges = _open_local_gates(monkeypatch)
    fetches: list[str] = []
    monkeypatch.setattr(catchup, "remote_sync_transaction", _lease(acquired=False))
    monkeypatch.setattr(
        catchup,
        "refresh_target_from_remote",
        lambda *_args, **_kwargs: fetches.append("fetch") or REFRESH_ORIGIN_AHEAD,
    )

    outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)

    assert outcome == catchup.CATCHUP_REMOTE_SKIPPED
    assert fetches == []
    assert checkout_merges == []


def test_shared_throttle_prevents_second_fetch_but_allows_local_catchup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout_merges = _open_local_gates(monkeypatch)
    fetches: list[str] = []
    monkeypatch.setattr(catchup, "remote_sync_transaction", _lease(fetch_allowed=False))
    monkeypatch.setattr(
        catchup,
        "refresh_target_from_remote",
        lambda *_args, **_kwargs: fetches.append("fetch") or REFRESH_ORIGIN_AHEAD,
    )

    outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert fetches == []
    assert checkout_merges == [_REMOTE_SHA]


def test_remote_sync_uses_five_minute_cadence_while_local_catchup_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    common_dir = tmp_path / ".git"
    common_dir.mkdir()
    checkout_merges = _open_local_gates(monkeypatch)
    fetches: list[float] = []
    now = 0.0

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup_coordination._common_git_dir",
        lambda _root: common_dir,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup_coordination.time.time", lambda: now
    )
    monkeypatch.setattr(
        catchup,
        "refresh_target_from_remote",
        lambda *_args, **_kwargs: fetches.append(now) or REFRESH_DIVERGED,
    )

    first = catchup.attempt_catchup_fast_forward(_config(interval=0.0), tmp_path)
    now = 299.0
    inside_cadence = catchup.attempt_catchup_fast_forward(_config(interval=0.0), tmp_path)
    now = 300.0
    at_boundary = catchup.attempt_catchup_fast_forward(_config(interval=0.0), tmp_path)

    assert [first, inside_cadence, at_boundary] == [
        catchup.CATCHUP_FAST_FORWARDED,
        catchup.CATCHUP_FAST_FORWARDED,
        catchup.CATCHUP_FAST_FORWARDED,
    ]
    assert fetches == [0.0, 300.0]
    assert checkout_merges == [_REMOTE_SHA, _REMOTE_SHA, _REMOTE_SHA]


def test_remote_disabled_does_not_attempt_coordination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout_merges = _open_local_gates(monkeypatch)
    coordination: list[str] = []
    monkeypatch.setattr(
        catchup,
        "remote_sync_transaction",
        lambda *_args, **_kwargs: coordination.append("lock"),
    )

    outcome = catchup.attempt_catchup_fast_forward(
        _config(remote_enabled=False), tmp_path
    )

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert coordination == []
    assert checkout_merges == [_REMOTE_SHA]


def test_independent_coordinator_respects_persisted_throttle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    common_dir = tmp_path / ".git"
    common_dir.mkdir()
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup_coordination._common_git_dir",
        lambda _root: common_dir,
    )

    with remote_sync_transaction(tmp_path, "origin", "main") as first:
        assert first is not None
        assert first.fetch_allowed is True
        first.record_fetch()

    with remote_sync_transaction(tmp_path, "origin", "main") as second:
        assert second is not None
        assert second.fetch_allowed is False


def test_malformed_throttle_state_permits_owned_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    common_dir = tmp_path / ".git"
    state_dir = common_dir / "ralph/auto-integrate-catchup"
    state_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup_coordination._common_git_dir",
        lambda _root: common_dir,
    )

    with remote_sync_transaction(tmp_path, "origin", "main") as initial:
        assert initial is not None
        initial.record_fetch()
        initial._state_path.write_text("not-json", encoding="utf-8")

    with remote_sync_transaction(tmp_path, "origin", "main") as recovered:
        assert recovered is not None
        assert recovered.fetch_allowed is True


def test_clock_rollback_reanchors_throttle_without_fetch_storm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    common_dir = tmp_path / ".git"
    common_dir.mkdir()
    now = 1_000.0
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup_coordination._common_git_dir",
        lambda _root: common_dir,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup_coordination.time.time", lambda: now
    )

    with remote_sync_transaction(tmp_path, "origin", "main") as initial:
        assert initial is not None
        initial.record_fetch()

    now = 900.0
    with remote_sync_transaction(tmp_path, "origin", "main") as rolled_back:
        assert rolled_back is not None
        assert rolled_back.fetch_allowed is False

    now = 1_199.0
    with remote_sync_transaction(tmp_path, "origin", "main") as still_throttled:
        assert still_throttled is not None
        assert still_throttled.fetch_allowed is False

    now = 1_200.0
    with remote_sync_transaction(tmp_path, "origin", "main") as boundary:
        assert boundary is not None
        assert boundary.fetch_allowed is True


def test_implausibly_future_record_is_bounded_to_one_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    common_dir = tmp_path / ".git"
    common_dir.mkdir()
    now = 100.0
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup_coordination._common_git_dir",
        lambda _root: common_dir,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup_coordination.time.time", lambda: now
    )

    with remote_sync_transaction(tmp_path, "origin", "main") as initial:
        assert initial is not None
        initial._state_path.write_text('{"fetch_after":10000.0}', encoding="utf-8")

    with remote_sync_transaction(tmp_path, "origin", "main") as future_record:
        assert future_record is not None
        assert future_record.fetch_allowed is False

    now = 400.0
    with remote_sync_transaction(tmp_path, "origin", "main") as boundary:
        assert boundary is not None
        assert boundary.fetch_allowed is True
