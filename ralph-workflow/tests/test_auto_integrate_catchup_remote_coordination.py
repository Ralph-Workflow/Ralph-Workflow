from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_catchup as catchup
from ralph.pipeline.auto_integrate_catchup_coordination import (
    REMOTE_SYNC_INTERVAL_SECONDS,
    remote_sync_interval_seconds,
    remote_sync_transaction,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger

if TYPE_CHECKING:
    from collections.abc import Iterator


_LOCAL_SHA = "a" * 40
_REMOTE_SHA = "b" * 40


def _config(*, remote_enabled: bool = True, interval: float | None = 30.0) -> UnifiedConfig:
    general: dict[str, object] = {
        "auto_integrate_enabled": True,
        "auto_integrate_target": "main",
        "auto_integrate_remote_enabled": remote_enabled,
        "auto_integrate_remote": "origin",
    }
    if interval is not None:
        general["auto_integrate_remote_interval_seconds"] = interval
    return UnifiedConfig.model_validate({"general": general})


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
        if branch == "main":
            return "b" * 40, True
        return f"a{len(checkout_merges):039d}", True

    monkeypatch.setattr(catchup, "observe_branch_sha", _observe)
    monkeypatch.setattr(catchup, "is_lease_held", lambda _root: False)
    monkeypatch.setattr(catchup, "try_acquire_lease", lambda _root, holder=None: object())
    monkeypatch.setattr(catchup, "end_agent_lease", lambda _lease: None)
    monkeypatch.setattr(
        catchup,
        "_repository_ledger",
        lambda repo_root: AttemptLedger(
            repo_root,
            ledger_path=repo_root / ".agent" / "integration_attempt_ledger.json",
        ),
    )
    monkeypatch.setattr(
        catchup,
        "fast_forward_via_worktree",
        lambda _root, sha: checkout_merges.append(sha) is None,
    )
    monkeypatch.setattr(catchup, "is_lease_held", lambda _root: False)
    monkeypatch.setattr(catchup, "try_acquire_lease", lambda _root, **_kw: object())
    monkeypatch.setattr(catchup, "end_agent_lease", lambda _lease: None)
    monkeypatch.setattr(
        catchup,
        "_repository_ledger",
        lambda repo_root: AttemptLedger(
            repo_root,
            ledger_path=repo_root / ".agent" / "integration_attempt_ledger.json",
        ),
    )
    return checkout_merges


def _lease(*, acquired: bool = True, fetch_allowed: bool = True):
    @contextmanager
    def _acquire(
        _root: Path, _remote: str, _target: str, **_kwargs: object
    ) -> Iterator[object | None]:
        if not acquired:
            yield None
            return
        yield type(
            "Lease", (), {"fetch_allowed": fetch_allowed, "record_fetch": lambda self: None}
        )()

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

    assert outcome.startswith(catchup.CATCHUP_DEFEDITELY_FAILED)
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


@pytest.mark.parametrize(
    ("interval", "probe_times", "expected_fetches"),
    [
        pytest.param(None, (0.0, 299.0, 300.0), [0.0, 300.0], id="unset-defaults-to-300s"),
        pytest.param(0.0, (0.0, 299.0, 300.0), [0.0, 300.0], id="zero-defaults-to-300s"),
        pytest.param(60.0, (0.0, 59.0, 60.0), [0.0, 60.0], id="configured-60s"),
        pytest.param(900.0, (0.0, 300.0, 900.0), [0.0, 900.0], id="configured-900s"),
    ],
)
def test_remote_sync_fetch_cadence_honours_configured_interval_while_local_catchup_continues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interval: float | None,
    probe_times: tuple[float, float, float],
    expected_fetches: list[float],
) -> None:
    """The ticker fetches once per ``auto_integrate_remote_interval_seconds``.

    A positive configured interval is the fleet-wide fetch cadence; unset or
    ``0.0`` (the field default, "fetch at every seam") keeps the ticker's
    documented 300 s cadence. Probes at the start, just inside and exactly
    at one cadence fetch twice; every probe still fast-forwards the checkout.
    Before the fix the cadence was hard-coded to 300 s, so a 60 s interval
    fetched at 0 s only and a 900 s interval fetched again at 300 s.
    """
    common_dir = tmp_path / ".git"
    common_dir.mkdir()
    checkout_merges = _open_local_gates(monkeypatch)
    fetches: list[float] = []
    now = 0.0

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup_coordination._common_git_dir",
        lambda _root: common_dir,
    )
    monkeypatch.setattr("ralph.pipeline.auto_integrate_catchup_coordination.time.time", lambda: now)
    monkeypatch.setattr(
        catchup,
        "refresh_target_from_remote",
        lambda *_args, **_kwargs: fetches.append(now) or REFRESH_DIVERGED,
    )

    outcomes: list[str] = []
    for probe_time in probe_times:
        now = probe_time
        outcomes.append(catchup.attempt_catchup_fast_forward(_config(interval=interval), tmp_path))

    assert outcomes == [catchup.CATCHUP_FAST_FORWARDED] * 3
    assert fetches == expected_fetches
    assert checkout_merges == [_REMOTE_SHA, _REMOTE_SHA, _REMOTE_SHA]


@pytest.mark.parametrize(
    ("interval", "expected"),
    [
        pytest.param(None, REMOTE_SYNC_INTERVAL_SECONDS, id="unset"),
        pytest.param(0.0, REMOTE_SYNC_INTERVAL_SECONDS, id="zero-means-every-seam"),
        pytest.param(45.0, 45.0, id="configured"),
        pytest.param(3600.0, 3600.0, id="configured-hour"),
        pytest.param(float("inf"), REMOTE_SYNC_INTERVAL_SECONDS, id="infinite-is-unusable"),
    ],
)
def test_remote_sync_interval_reads_the_configured_cadence(
    interval: float | None, expected: float
) -> None:
    """Only a positive, finite configured interval replaces the 300 s default.

    An infinite interval would persist a non-finite ``fetch_after`` that the
    throttle treats as corrupt -- a fetch on every tick -- so it falls back.
    """
    assert REMOTE_SYNC_INTERVAL_SECONDS == 300.0
    assert remote_sync_interval_seconds(_config(interval=interval)) == expected


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

    outcome = catchup.attempt_catchup_fast_forward(_config(remote_enabled=False), tmp_path)

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
    monkeypatch.setattr("ralph.pipeline.auto_integrate_catchup_coordination.time.time", lambda: now)

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
    monkeypatch.setattr("ralph.pipeline.auto_integrate_catchup_coordination.time.time", lambda: now)

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
