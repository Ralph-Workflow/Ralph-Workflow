"""Unit tests for the background catch-up fast-forward worker.

The catch-up tick (:func:`attempt_catchup_fast_forward`) is a chain of
stateless gates over injected-by-monkeypatch collaborators; every gate
gets a test proving it short-circuits with the right outcome tag and
that only the fully-open chain reaches the ``merge --ff-only`` call.
The worker class is exercised with an injected recording ``tick`` and a
millisecond interval so no test performs git I/O or sleeps near the
per-test budget. The real-git proof lives in
``tests/test_auto_integrate_catchup_e2e.py``.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_catchup as catchup
from ralph.pipeline.auto_integrate_remote_sync import FETCH_TIMEOUT_SECONDS
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
from ralph.pipeline.integration_attempt_ledger import AttemptLedger

if TYPE_CHECKING:
    from collections.abc import Iterator

_TARGET_SHA = "b" * 40
_FEATURE_SHA = "a" * 40


@dataclass(frozen=True)
class _FakeRemoteLease:
    """Minimal remote-sync lease used by focused catch-up tests."""

    fetch_allowed: bool = True

    def record_fetch(self) -> None:
        """Record the synthetic fetch without touching the filesystem."""


@contextmanager
def _remote_lease(*_args: object, **_kwargs: object) -> Iterator[_FakeRemoteLease]:
    yield _FakeRemoteLease()


def _config(
    *,
    enabled: bool = True,
    target: str | None = None,
    remote_enabled: bool = False,
    remote: str = "origin",
) -> UnifiedConfig:
    general: dict[str, object] = {
        "auto_integrate_enabled": enabled,
        "auto_integrate_remote_enabled": remote_enabled,
        "auto_integrate_remote": remote,
    }
    if target is not None:
        general["auto_integrate_target"] = target
    return UnifiedConfig.model_validate({"general": general})


def _open_all_gates(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: str = "main",
    current: str | None = "feature",
    clean: bool = True,
    target_sha: str | None = _TARGET_SHA,
    current_sha: str | None = _FEATURE_SHA,
    ancestor: bool = True,
    ff_ok: bool = True,
) -> list[tuple[Path, str]]:
    """Patch every collaborator in the tick chain; return the ff call log."""
    ff_calls: list[tuple[Path, str]] = []

    def _fake_resolve(config: UnifiedConfig, root: Path) -> str | None:
        return target

    def _fake_current_branch(root: Path) -> str | None:
        return current

    def _fake_clean(root: Path) -> bool:
        return clean

    def _fake_observe(root: Path, name: str) -> tuple[str | None, bool]:
        if name == target:
            return target_sha, target_sha is not None
        return current_sha, current_sha is not None

    def _fake_is_ancestor(root: Path, a: str, b: str) -> bool:
        return ancestor

    def _fake_ff(root: Path, sha: str) -> bool:
        ff_calls.append((Path(root), sha))
        return ff_ok

    def _fake_still_safe(root: Path, expected_branch: str) -> bool:
        return True

    monkeypatch.setattr(catchup, "resolve_integration_target", _fake_resolve)
    monkeypatch.setattr(catchup, "_current_branch_name", _fake_current_branch)
    monkeypatch.setattr(catchup, "_worktree_is_clean", _fake_clean)
    monkeypatch.setattr(catchup, "observe_branch_sha", _fake_observe)
    monkeypatch.setattr(catchup, "is_ancestor", _fake_is_ancestor)
    monkeypatch.setattr(catchup, "_still_safe_to_merge", _fake_still_safe)
    def _not_held(_root: Path) -> bool:
        return False

    def _acquire(_root: Path, **_kwargs: object) -> object:
        return object()

    def _release(_lease: object) -> None:
        return None

    def _ledger(repo_root: Path) -> AttemptLedger:
        return AttemptLedger(
            repo_root,
            ledger_path=repo_root / ".agent" / "integration_attempt_ledger.json",
        )

    monkeypatch.setattr(catchup, "fast_forward_via_worktree", _fake_ff)
    monkeypatch.setattr(catchup, "is_lease_held", _not_held)
    monkeypatch.setattr(catchup, "try_acquire_lease", _acquire)
    monkeypatch.setattr(catchup, "end_agent_lease", _release)
    monkeypatch.setattr(catchup, "_repository_ledger", _ledger)
    return ff_calls


def _stop_and_wait(worker: catchup.AutoIntegrateCatchupWorker) -> None:
    worker.stop()
    thread = worker._thread
    assert thread is not None
    thread.join(timeout=0.5)
    assert not worker.is_running


# Each test section remains flat so the repo-structure audit enforces one public class.


class TestAutoIntegrateCatchup:
    """Catch-up tick gates and bounded worker lifecycle."""

    def test_disabled_short_circuits_before_any_git(self, tmp_path: Path) -> None:
        outcome = catchup.attempt_catchup_fast_forward(_config(enabled=False), tmp_path)
        assert outcome == catchup.CATCHUP_DISABLED

    def test_no_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def _no_target(config: UnifiedConfig, root: Path) -> str | None:
            return None

        _open_all_gates(monkeypatch)
        monkeypatch.setattr(catchup, "resolve_integration_target", _no_target)
        outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)
        assert outcome == catchup.CATCHUP_NO_TARGET

    def test_detached_head(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _open_all_gates(monkeypatch, current=None)
        outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)
        assert outcome == catchup.CATCHUP_NOT_ON_BRANCH

    def test_already_on_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _open_all_gates(monkeypatch, current="main")
        outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)
        assert outcome == catchup.CATCHUP_ON_TARGET

    def test_already_on_target_synchronizes_remote_without_checkout_catchup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _open_all_gates(monkeypatch, current="main")
        events: list[str] = []

        def _sync(_config: UnifiedConfig, _root: Path, target: str, **_kwargs: object) -> None:
            events.append(f"sync:{target}")

        def _checkout(*_args: object, **_kwargs: object) -> str:
            events.append("checkout-catchup")
            return catchup.CATCHUP_REFUSED

        monkeypatch.setattr(catchup, "_synchronize_remote_target", _sync)
        monkeypatch.setattr(catchup, "_fast_forward_if_strictly_behind", _checkout)

        outcome = catchup.attempt_catchup_fast_forward(_config(remote_enabled=True), tmp_path)

        assert outcome == catchup.CATCHUP_ON_TARGET
        assert events == ["sync:main"]

    def test_catchup_regression_target_owned_remote_advance_reports_and_logs_movement(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _open_all_gates(monkeypatch, current="main")

        def _sync(
            _config: UnifiedConfig,
            _root: Path,
            _target: str,
            **_kwargs: object,
        ) -> str:
            return catchup.CATCHUP_FAST_FORWARDED

        monkeypatch.setattr(catchup, "_synchronize_remote_target", _sync)
        messages: list[str] = []
        monkeypatch.setattr(logger, "info", messages.append)
        worker = catchup.AutoIntegrateCatchupWorker(
            _config(remote_enabled=True),
            tmp_path,
            tick=lambda: catchup.attempt_catchup_fast_forward(
                _config(remote_enabled=True), tmp_path
            ),
        )

        outcome = catchup.attempt_catchup_fast_forward(_config(remote_enabled=True), tmp_path)
        worker._tick_once()

        assert outcome == catchup.CATCHUP_FAST_FORWARDED
        assert messages == ["auto_integrate catch-up: fast-forwarded the checkout onto the target"]

    def test_dirty_worktree(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        ff_calls = _open_all_gates(monkeypatch, clean=False)
        outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)
        assert outcome == catchup.CATCHUP_DIRTY
        assert ff_calls == []

    def test_target_unreadable(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _open_all_gates(monkeypatch, target_sha=None)
        outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)
        assert outcome == catchup.CATCHUP_TARGET_UNREADABLE

    def test_head_unreadable(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _open_all_gates(monkeypatch, current_sha=None)
        outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)
        assert outcome == catchup.CATCHUP_HEAD_UNREADABLE

    def test_up_to_date(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        ff_calls = _open_all_gates(monkeypatch, target_sha=_TARGET_SHA, current_sha=_TARGET_SHA)
        outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)
        assert outcome == catchup.CATCHUP_UP_TO_DATE
        assert ff_calls == []

    def test_diverged(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        ff_calls = _open_all_gates(monkeypatch, ancestor=False)
        outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)
        assert outcome == catchup.CATCHUP_DIVERGED
        assert ff_calls == []

    def test_fast_forwards_with_observed_target_sha(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ff_calls = _open_all_gates(monkeypatch)
        outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)
        assert outcome == catchup.CATCHUP_FAST_FORWARDED
        # The merge is handed the OBSERVED SHA, not the ref name, so a
        # concurrently-advancing target can never turn this into a
        # non-fast-forward.
        assert ff_calls == [(tmp_path, _TARGET_SHA)]

    def test_remote_refresh_uses_configured_remote_before_observing_unchanged_target(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events: list[str] = []
        ff_calls = _open_all_gates(monkeypatch)
        refresh_calls: list[tuple[Path, str, float, str]] = []

        def _refresh(
            root: Path,
            target: str,
            *,
            timeout_seconds: float,
            remote: str,
        ) -> str:
            events.append("refresh")
            refresh_calls.append((root, target, timeout_seconds, remote))
            return "refreshed"

        original_observe = catchup.observe_branch_sha

        def _observe(root: Path, name: str) -> tuple[str | None, bool]:
            if name == "main":
                events.append("observe-target")
            return original_observe(root, name)

        monkeypatch.setattr(catchup, "refresh_target_from_remote", _refresh)
        monkeypatch.setattr(catchup, "observe_branch_sha", _observe)
        monkeypatch.setattr(catchup, "remote_sync_transaction", _remote_lease)

        outcome = catchup.attempt_catchup_fast_forward(
            _config(remote_enabled=True, remote="upstream"), tmp_path
        )

        assert outcome == catchup.CATCHUP_FAST_FORWARDED
        assert events == ["refresh", "observe-target"]
        assert refresh_calls == [(tmp_path, "main", FETCH_TIMEOUT_SECONDS, "upstream")]
        assert ff_calls == [(tmp_path, _TARGET_SHA)]

    def test_remote_sync_disabled_keeps_local_catchup_without_remote_probe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ff_calls = _open_all_gates(monkeypatch)
        refresh_calls: list[str] = []

        def _unexpected_refresh(*_args: object, **_kwargs: object) -> str:
            refresh_calls.append("refresh")
            return "refreshed"

        monkeypatch.setattr(catchup, "refresh_target_from_remote", _unexpected_refresh)

        outcome = catchup.attempt_catchup_fast_forward(_config(remote_enabled=False), tmp_path)

        assert outcome == catchup.CATCHUP_FAST_FORWARDED
        assert refresh_calls == []
        assert ff_calls == [(tmp_path, _TARGET_SHA)]

    def test_remote_refresh_unreachable_refuses_local_catchup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ff_calls = _open_all_gates(monkeypatch)

        def _unreachable(*_args: object, **_kwargs: object) -> str:
            return REFRESH_UNREACHABLE

        monkeypatch.setattr(catchup, "refresh_target_from_remote", _unreachable)
        monkeypatch.setattr(catchup, "remote_sync_transaction", _remote_lease)

        outcome = catchup.attempt_catchup_fast_forward(_config(remote_enabled=True), tmp_path)

        assert outcome == catchup.CATCHUP_REMOTE_SKIPPED
        assert ff_calls == []

    def test_definitely_failed_merge_reports_named_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _open_all_gates(monkeypatch, ff_ok=False)
        outcome = catchup.attempt_catchup_fast_forward(_config(), tmp_path)
        assert outcome.startswith(catchup.CATCHUP_DEFEDITELY_FAILED)

    def test_rejects_non_positive_interval(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="interval_seconds"):
            catchup.AutoIntegrateCatchupWorker(_config(), tmp_path, interval_seconds=0.0)

    def test_ticks_after_interval_and_stops(self, tmp_path: Path) -> None:
        fired = threading.Event()

        def _tick() -> str:
            fired.set()
            return catchup.CATCHUP_UP_TO_DATE

        worker = catchup.AutoIntegrateCatchupWorker(
            _config(), tmp_path, interval_seconds=0.005, tick=_tick
        )
        worker.start()
        try:
            assert fired.wait(timeout=0.5)
            assert worker.is_running
        finally:
            _stop_and_wait(worker)

    def test_stop_is_idempotent_and_start_restarts(self, tmp_path: Path) -> None:
        worker = catchup.AutoIntegrateCatchupWorker(
            _config(),
            tmp_path,
            interval_seconds=0.005,
            tick=lambda: catchup.CATCHUP_UP_TO_DATE,
        )
        worker.start()
        worker.start()  # idempotent while alive
        worker.stop()
        _stop_and_wait(worker)
        worker.start()  # a stopped worker can be restarted
        assert worker.is_running
        _stop_and_wait(worker)

    def test_tick_exception_does_not_kill_the_loop(self, tmp_path: Path) -> None:
        calls: list[int] = []
        iteration = iter((False, False, True))

        def _tick() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return catchup.CATCHUP_UP_TO_DATE

        def _wait(_interval: float) -> bool:
            return next(iteration)

        worker = catchup.AutoIntegrateCatchupWorker(
            _config(),
            tmp_path,
            interval_seconds=0.005,
            tick=_tick,
            wait=_wait,
        )
        worker.run()

        assert calls == [1, 1]

    def test_disabled_returns_none(self, tmp_path: Path) -> None:
        assert catchup.start_catchup_worker_if_enabled(_config(enabled=False), tmp_path) is None

    def test_enabled_returns_running_worker(self, tmp_path: Path) -> None:
        # The default 30 s interval guarantees no tick (and therefore no
        # git subprocess) fires within this test's lifetime.
        worker = catchup.start_catchup_worker_if_enabled(_config(), tmp_path)
        assert worker is not None
        try:
            assert worker.is_running
        finally:
            _stop_and_wait(worker)
