"""Black-box tests for the dev/fix session lifecycle refresh hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.lifecycle import (
    DEFAULT_HOOK_TIMEOUT_MS,
    LifecycleHookResult,
    after_agent_refresh,
    before_agent_refresh,
    is_execution_phase_for_refresh,
)
from ralph.mcp.explore.store import ExploreStore


class _Index:
    """Tiny index handle exposing ``store`` and a deterministic runner."""

    def __init__(self, store: ExploreStore, runner) -> None:
        self.store = store
        self._runner = runner

    def build_options(self, *, timeout_ms: int):
        from ralph.mcp.explore.pipeline import ReindexOptions

        return ReindexOptions(mode="changed", timeout_ms=timeout_ms)

    # The lifecycle hook calls ``runner(store, workspace_root, opts=...)``
    # via duck-typing; expose the runner through a method.
    def runner(self):
        return self._runner


class _Workspace:
    def __init__(self, root: Path, index: Any | None) -> None:
        self.root = root
        self.explore_index = index


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("x = 1\n")
    return workspace


def test_is_execution_phase_for_refresh_only_true_for_execution_role() -> None:
    # AC-04: the role alone is not enough — the phase drain must also
    # be in REFRESHABLE_PHASE_DRAINS. ``planning`` is also mapped to
    # ``role=execution`` in the default pipeline but must NOT trigger
    # a refresh.
    assert is_execution_phase_for_refresh(
        phase_role="execution", phase_drain="development"
    ) is True
    assert is_execution_phase_for_refresh(
        phase_role="execution", phase_drain="fix"
    ) is True
    assert is_execution_phase_for_refresh(
        phase_role="execution", phase_drain="planning"
    ) is False
    assert is_execution_phase_for_refresh(
        phase_role="execution", phase_drain="commit"
    ) is False
    assert is_execution_phase_for_refresh(
        phase_role="analysis", phase_drain="development"
    ) is False
    assert is_execution_phase_for_refresh(
        phase_role="commit", phase_drain="development"
    ) is False
    assert is_execution_phase_for_refresh(phase_role=None, phase_drain=None) is False
    # Backward-compat: callers that pass only the role get False
    # because the drain defaults to None, which is never refreshable.
    assert is_execution_phase_for_refresh(phase_role="execution") is False


def test_planning_phase_does_not_trigger_refresh() -> None:
    """AC-04 regression: the planning block (``role=execution``,
    ``drain=planning``) must not trigger an indexed refresh.

    The default pipeline maps the planning block to ``role =
    \"execution\"`` for historical reasons. A role-only gate would
    therefore refresh the index for the planning agent, which is
    uncosted. The drain is the authoritative gate.
    """
    assert is_execution_phase_for_refresh(
        phase_role="execution", phase_drain="planning"
    ) is False
    assert is_execution_phase_for_refresh(
        phase_role="execution", phase_drain="planning_analysis"
    ) is False


def test_development_and_fix_phases_trigger_refresh() -> None:
    """AC-04: only the development / fix drains trigger a refresh."""
    assert is_execution_phase_for_refresh(
        phase_role="execution", phase_drain="development"
    ) is True
    assert is_execution_phase_for_refresh(
        phase_role="execution", phase_drain="fix"
    ) is True


def test_review_and_commit_phases_do_not_trigger_refresh() -> None:
    """AC-04: review and commit drains never trigger a refresh."""
    for drain in ("commit", "commit_cleanup", "review", "terminal", "complete"):
        assert is_execution_phase_for_refresh(
            phase_role="execution", phase_drain=drain
        ) is False, f"drain {drain!r} must not trigger refresh"


def test_before_hook_skips_when_explore_index_is_none(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    result = before_agent_refresh(
        workspace_root=workspace,
        explore_index=None,
    )
    assert result.invoked is False
    assert result.skipped_reason == "explore_index_disabled"
    assert result.timed_out is False


def test_after_hook_skips_when_explore_index_is_none(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    result = after_agent_refresh(
        workspace_root=workspace,
        explore_index=None,
    )
    assert result.invoked is False


def test_before_hook_skips_when_handle_has_no_store(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    result = before_agent_refresh(
        workspace_root=workspace,
        explore_index=object(),
    )
    assert result.invoked is False
    assert result.skipped_reason == "no_store"


def test_before_hook_invokes_reindex_with_injected_runner(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")

    calls: list[tuple[Path, dict]] = []

    class _FakeResult:
        status = "ok"

    def runner(s, r, *, opts):
        calls.append((r, {"timeout_ms": opts.timeout_ms, "mode": opts.mode}))
        return _FakeResult()

    index = _Index(store, runner)
    result = before_agent_refresh(
        workspace_root=workspace,
        explore_index=index,
        timeout_ms=DEFAULT_HOOK_TIMEOUT_MS,
        reindex_runner=runner,
    )
    assert result.invoked is True
    assert result.timed_out is False
    assert len(calls) == 1
    assert calls[0][0] == workspace
    assert calls[0][1]["mode"] == "changed"
    store.close()


def test_after_hook_invokes_reindex_with_injected_runner(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")

    calls: list[Path] = []

    class _FakeResult:
        status = "ok"

    def runner(s, r, *, opts):
        calls.append(r)
        return _FakeResult()

    index = _Index(store, runner)
    result = after_agent_refresh(
        workspace_root=workspace,
        explore_index=index,
        reindex_runner=runner,
    )
    assert result.invoked is True
    assert len(calls) == 1
    store.close()


def test_hooks_are_fail_open_on_runner_exception(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")

    def runner(s, r, *, opts):
        raise RuntimeError("simulated failure")

    index = _Index(store, runner)
    result = before_agent_refresh(
        workspace_root=workspace,
        explore_index=index,
        reindex_runner=runner,
    )
    assert result.invoked is True
    assert result.timed_out is False
    assert result.skipped_reason == "error:RuntimeError"
    store.close()


def test_hooks_report_timeout_when_runner_returns_timed_out(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")

    class _FakeResult:
        status = "timed_out"

    def runner(s, r, *, opts):
        return _FakeResult()

    index = _Index(store, runner)
    result = after_agent_refresh(
        workspace_root=workspace,
        explore_index=index,
        reindex_runner=runner,
    )
    assert result.invoked is True
    assert result.timed_out is True
    store.close()


def test_hooks_use_default_runner_when_injected_runner_is_none(tmp_path: Path) -> None:
    """When no runner is injected, the default pipeline.reindex runs."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    index = build_sqlite_index_handle(store)

    result = before_agent_refresh(
        workspace_root=workspace,
        explore_index=index,
        timeout_ms=DEFAULT_HOOK_TIMEOUT_MS,
    )
    assert result.invoked is True
    assert result.timed_out is False
    # Default reindex ran; the index now has a generation.
    assert store.get_setting("current_generation") == "1"
    store.close()


def test_lifecycle_hook_result_dataclass_defaults() -> None:
    """LifecycleHookResult is frozen with safe defaults."""
    result = LifecycleHookResult(invoked=False, timed_out=False)
    assert result.reindex_result is None
    assert result.skipped_reason is None
