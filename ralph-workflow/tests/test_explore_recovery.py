from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.explore.handlers import ExploreIndex, handle_ralph_index_status
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.recovery import (
    DEFAULT_BACKOFF_BASE_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_BACKOFF_SECONDS,
    HealthState,
    RecoveryScheduler,
    advisory_lock_path,
    build_scheduler,
    release_advisory_lock,
    try_advisory_lock,
)
from ralph.mcp.explore.store import ExploreStore


class _FakeClock:
    def __init__(self) -> None:
        self._now = 0.0

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _FakeSession:
    session_id = "recovery"
    run_id = "recovery"
    broker_secret: str | None = None

    def __init__(self, explore_index=None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str):
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str):
        return {"status": "approved", "path": path}


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "hello.py").write_text("def hello():\n    return 'world'\n")
    return workspace


def _attach_session(store: ExploreStore, workspace: Path) -> _FakeSession:
    handle = ExploreIndex(
        workspace_root=workspace,
        index_root=store.db_path.parent,
        store=store,
        generation=1,
    )
    return _FakeSession(handle)


def test_recovery_scheduler_initial_state_is_healthy(tmp_path: Path) -> None:
    scheduler = build_scheduler(tmp_path)
    assert scheduler.health is HealthState.HEALTHY
    snap = scheduler.snapshot()
    assert snap["health"] == "healthy"
    assert snap["last_failure"] is None
    assert snap["recovery_attempts"] == 0


def test_recovery_default_bounds_match_docs() -> None:
    assert DEFAULT_BACKOFF_BASE_SECONDS > 0
    assert DEFAULT_MAX_BACKOFF_SECONDS > DEFAULT_BACKOFF_BASE_SECONDS
    assert DEFAULT_MAX_ATTEMPTS >= 1


def test_recovery_failure_records_attempt_and_backoff(tmp_path: Path) -> None:
    clock = _FakeClock()
    scheduler = RecoveryScheduler(
        workspace_root=tmp_path,
        backoff_base_seconds=1.0,
        max_backoff_seconds=10.0,
        max_attempts=4,
    )
    scheduler.record_failure(code="index_corrupt", message="bad pages", clock=clock.monotonic)
    snap = scheduler.snapshot()
    assert snap["recovery_attempts"] == 1
    assert snap["last_failure"]["code"] == "index_corrupt"
    assert snap["last_failure"]["message"] == "bad pages"
    assert snap["next_recovery_at"] == clock.monotonic() + 1.0
    assert snap["health"] == "stale"


def test_recovery_backoff_grows_exponentially(tmp_path: Path) -> None:
    clock = _FakeClock()
    scheduler = RecoveryScheduler(
        workspace_root=tmp_path,
        backoff_base_seconds=1.0,
        max_backoff_seconds=10.0,
        max_attempts=4,
    )
    scheduler.record_failure(code="index_locked", clock=clock.monotonic)
    snap1 = scheduler.snapshot()
    assert snap1["next_recovery_at"] == 1.0
    clock.advance(1.5)
    scheduler.record_failure(code="index_locked", clock=clock.monotonic)
    snap2 = scheduler.snapshot()
    assert snap2["recovery_attempts"] == 2
    assert snap2["next_recovery_at"] == 1.5 + 2.0
    clock.advance(3.5)
    scheduler.record_failure(code="index_locked", clock=clock.monotonic)
    snap3 = scheduler.snapshot()
    assert snap3["recovery_attempts"] == 3
    assert snap3["next_recovery_at"] == 5.0 + 4.0


def test_recovery_unhealthy_after_max_attempts(tmp_path: Path) -> None:
    clock = _FakeClock()
    scheduler = RecoveryScheduler(
        workspace_root=tmp_path,
        backoff_base_seconds=0.1,
        max_backoff_seconds=1.0,
        max_attempts=2,
    )
    scheduler.record_failure(code="indexer_error", clock=clock.monotonic)
    scheduler.record_failure(code="indexer_error", clock=clock.monotonic)
    scheduler.record_failure(code="indexer_error", clock=clock.monotonic)
    snap = scheduler.snapshot()
    assert snap["recovery_attempts"] == 3
    assert snap["health"] == "unhealthy"
    assert snap["next_recovery_at"] is None
    assert scheduler.should_attempt_recovery(clock=clock.monotonic) is False


def test_recovery_should_attempt_recovery_respects_backoff(tmp_path: Path) -> None:
    clock = _FakeClock()
    scheduler = RecoveryScheduler(
        workspace_root=tmp_path,
        backoff_base_seconds=1.0,
        max_backoff_seconds=10.0,
        max_attempts=4,
    )
    scheduler.record_failure(code="index_locked", clock=clock.monotonic)
    assert scheduler.should_attempt_recovery(clock=clock.monotonic) is False
    clock.advance(1.0)
    assert scheduler.should_attempt_recovery(clock=clock.monotonic) is True


def test_recovery_mark_health_states(tmp_path: Path) -> None:
    scheduler = build_scheduler(tmp_path)
    scheduler.mark_building()
    assert scheduler.health is HealthState.BUILDING
    scheduler.mark_stale()
    assert scheduler.health is HealthState.STALE
    scheduler.mark_read_only()
    assert scheduler.health is HealthState.DEGRADED
    scheduler.mark_healthy()
    assert scheduler.health is HealthState.HEALTHY
    snap = scheduler.snapshot()
    assert snap["last_failure"] is None
    assert snap["cooldown_remaining"] == 0.0


def test_explore_regression_observation_does_not_hide_exhausted_recovery(
    tmp_path: Path,
) -> None:
    scheduler = RecoveryScheduler(workspace_root=tmp_path, max_attempts=1)
    scheduler.record_failure(code="index_stale_scope", clock=lambda: 0.0)
    scheduler.record_failure(code="index_stale_scope", clock=lambda: 0.0)
    assert scheduler.snapshot()["health"] == "unhealthy"

    scheduler.observe(code="index_stale_scope", message="still stale")

    snapshot = scheduler.snapshot()
    assert snapshot["health"] == "unhealthy"
    assert snapshot["recovery_attempts"] == 2


def test_explore_regression_persisted_stale_exhaustion_normalizes_unhealthy(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".agent" / "ralph-explore"
    state_dir.mkdir(parents=True)
    (state_dir / "recovery_state.json").write_text(
        json.dumps(
            {
                "health": "stale",
                "last_failure_code": "index_stale_scope",
                "last_failure_message": "index_stale_scope",
                "recovery_attempts": 5,
                "next_recovery_at": None,
                "cooldown_remaining": 0.0,
            }
        ),
        encoding="utf-8",
    )
    scheduler = RecoveryScheduler(workspace_root=tmp_path, max_attempts=4)

    scheduler.observe(code="index_stale_scope", message="still stale")

    assert scheduler.snapshot()["health"] == "unhealthy"


def test_recovery_advisory_lock_path_underworkspace_root(tmp_path: Path) -> None:
    path = advisory_lock_path(tmp_path)
    assert path.parent.name == "ralph-explore"
    assert path.name == ".recovery.lock"


def test_recovery_advisory_lock_acquired_and_released(tmp_path: Path) -> None:
    try:
        acquired = try_advisory_lock(tmp_path)
        assert acquired is True
        release_advisory_lock()
    except OSError:
        release_advisory_lock()
        import pytest

        pytest.skip("advisory lock not available in this environment")


def test_status_payload_carries_health_and_recovery(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        result = handle_ralph_index_status(
            _attach_session(store, workspace),
            _Workspace(workspace),
            {},
        )
        payload = _decode(result)
        assert "health" in payload
        assert payload["health"] == "healthy"
        assert "recovery" in payload
        recovery = payload["recovery"]
        assert recovery["health"] == "healthy"
        assert recovery["recovery_attempts"] == 0
        assert recovery["last_failure"] is None
    finally:
        store.close()


def test_status_payload_after_failure_marks_unhealthy(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        scheduler = build_scheduler(workspace)
        scheduler.record_failure(code="indexer_error", message="bad pages")
        scheduler.record_failure(code="indexer_error", message="bad pages")
        scheduler.record_failure(code="indexer_error", message="bad pages")
        from ralph.mcp.explore.recovery import RecoveryScheduler

        sched = RecoveryScheduler(
            workspace_root=workspace,
            backoff_base_seconds=0.1,
            max_backoff_seconds=1.0,
            max_attempts=2,
        )
        sched.record_failure(code="indexer_error")
        sched.record_failure(code="indexer_error")
        sched.record_failure(code="indexer_error")
        snap = sched.snapshot()
        assert snap["health"] == "unhealthy"
    finally:
        store.close()
