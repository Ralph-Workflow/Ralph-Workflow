from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ralph.mcp.explore.handlers import (
    ExploreIndex,
    build_explore_index,
    handle_ralph_index_status,
)
from ralph.mcp.explore.pipeline import (
    ReindexOptions,
    reindex,
)
from ralph.mcp.explore.recovery import (
    HealthState,
    _drain_pending_recoveries_once,
    build_scheduler,
    clear_pending_recovery,
    pending_recovery_code,
)
from ralph.mcp.explore.store import DEFAULT_INDEX_ROOT, ExploreStore
from ralph.process.manager import SpawnOptions, get_process_manager

pytestmark = [
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(60),
]


class _FakeClock:
    def __init__(self) -> None:
        self._now = 0.0

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _FakeSession:
    session_id = "lifecycle"
    run_id = "lifecycle"
    broker_secret: str | None = None

    def __init__(self, explore_index: ExploreIndex | None = None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str) -> dict[str, str]:
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> dict[str, str]:
        return {"status": "approved", "path": path}


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "hello.py").write_text("def hello():\n    return 'world'\n")
    (workspace / "world.py").write_text("x = 1\n")
    return workspace


def _decode(result: object) -> dict[str, object]:
    content = getattr(result, "content", None)
    if isinstance(content, list) and content:
        text = getattr(content[0], "text", "{}")
        parsed: object = json.loads(text)
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items()}
    return {}


def _fresh_handle(tmp_path: Path, *, workspace: Path) -> ExploreIndex:
    handle = build_explore_index(workspace)
    try:
        assert handle.generation == 0
    except AssertionError:
        index_dir = tmp_path / DEFAULT_INDEX_ROOT
        for side in ("", "-wal", "-shm"):
            target = Path(str(index_dir / "index.sqlite") + side)
            if target.exists():
                target.unlink()
        handle = build_explore_index(workspace)
    return handle


def test_e1_cold_detection_triggers_background_build(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = _fresh_handle(tmp_path, workspace=workspace)
    session = _FakeSession(explore_index=handle)
    result = handle_ralph_index_status(session, _Workspace(workspace), {})
    payload = _decode(result)
    assert pending_recovery_code(workspace) is not None, (
        "cold_index_required detected but no background build was queued; "
        "S-2 must call enqueue_recovery from every index-capable handler"
    )
    assert payload["health"] == HealthState.BUILDING.value, (
        f"expected health == 'building' after cold detection, got {payload['health']!r}"
    )
    clear_pending_recovery(workspace)


def test_s2_drain_rebuilds_cold_index(tmp_path: Path) -> None:
    from ralph.mcp.explore.recovery import enqueue_recovery

    workspace = _seed_workspace(tmp_path)
    handle = _fresh_handle(tmp_path, workspace=workspace)
    assert handle.generation == 0
    enqueue_recovery(
        workspace,
        "no_committed_generation",
        message="cold start",
    )
    assert pending_recovery_code(workspace) == "no_committed_generation"
    attempted = _drain_pending_recoveries_once()
    assert attempted == 1
    assert pending_recovery_code(workspace) is None, "drain must empty the queue"
    committed = handle.store.get_setting("current_generation") or "0"
    assert int(committed) >= 1, f"drain rebuilt but no generation committed (got {committed!r})"
    clear_pending_recovery(workspace)


def test_e2_short_budget_cold_build_leaves_gen_zero_no_retry_storm(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = _fresh_handle(tmp_path, workspace=workspace)
    index_dir = tmp_path / DEFAULT_INDEX_ROOT
    scheduler = build_scheduler(handle.workspace_root)
    store = ExploreStore(index_dir)
    try:
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=1),
        )
        assert result.status in {"timed_out", "failed"}
        persisted = store.get_setting("current_generation") or "0"
        assert int(persisted) == 0, "timed-out cold build leaked a partial committed generation"
    finally:
        store.close()
    assert pending_recovery_code(workspace) == "timeout_exceeded", (
        "timed-out cold build must enqueue exactly one bounded recovery; "
        "without it, no recovery ever happens"
    )
    scheduler.record_failure(code="timeout_exceeded", message="timed_out")
    store = ExploreStore(index_dir)
    try:
        reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=1),
        )
    finally:
        store.close()
    assert pending_recovery_code(workspace) == "timeout_exceeded", (
        "second timed-out probe must coalesce into the existing queue entry"
    )
    clear_pending_recovery(workspace)


_BUILD_AND_COMMIT_SCRIPT = """
import json
import os
import sys
import time
from pathlib import Path

# Prefer THIS checkout's ralph over any inherited PYTHONPATH entries
# (a stale pinned dev generation on PYTHONPATH would run mismatched
# schema code against the parent's freshly-created index directory).
sys.path.insert(0, os.environ["RALPH_TEST_REPO_ROOT"])

WORKSPACE = Path(\"__WORKSPACE__\")
INDEX_DIR = WORKSPACE / \".agent\" / \"ralph-explore\"
INDEX_DIR.mkdir(parents=True, exist_ok=True)

from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore


store = ExploreStore(INDEX_DIR)
try:
    result = reindex(
        store,
        WORKSPACE,
        options=ReindexOptions(mode=\"full\", timeout_ms=10_000),
    )
    sys.stdout.write(
        \"BUILD:\"
        + json.dumps(
            {
                \"status\": result.status,
                \"error\": str(result.error_summary),
                \"gen\": result.generation,
            }
        )
        + \"\\n\"
    )
    sys.stdout.flush()
finally:
    store.close()
"""


def test_e3_handle_observes_cross_process_generation_swap(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    index_dir = workspace / ".agent" / "ralph-explore"
    index_dir.mkdir(parents=True, exist_ok=True)

    build_path = tmp_path / "build.py"
    build_path.write_text(_BUILD_AND_COMMIT_SCRIPT.replace("__WORKSPACE__", str(workspace)))

    handle = build_explore_index(workspace)
    assert handle.generation == 0
    opened_gen_setting = handle.store.get_setting("current_generation")
    assert opened_gen_setting in (None, "0"), (
        f"fresh worktree should have no committed generation, got {opened_gen_setting!r}"
    )

    spawn_env = os.environ.copy()
    spawn_env["RALPH_TEST_REPO_ROOT"] = str(Path(__file__).resolve().parents[1])
    proc = get_process_manager().spawn(
        [sys.executable, str(build_path)],
        SpawnOptions(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            label="cold-start-lifecycle-build",
            env=spawn_env,
        ),
    )
    stdout, stderr = proc.communicate(timeout=30)
    stdout_str = stdout if isinstance(stdout, str) else (stdout.decode() if stdout else "")
    stderr_str = stderr if isinstance(stderr, str) else (stderr.decode() if stderr else "")
    assert "BUILD:" in stdout_str, (
        f"sibling build did not emit BUILD: -- stdout={stdout_str!r} stderr={stderr_str!r}"
    )
    build_payload_raw: object = json.loads(stdout_str.split("BUILD:", 1)[1].splitlines()[0])
    assert isinstance(build_payload_raw, dict)
    assert build_payload_raw.get("status") == "ok", (
        f"sibling build failed -- stdout={stdout_str!r} stderr={stderr_str!r}"
    )

    observed_via_handle = handle.store.get_setting("current_generation") or "0"
    fresh = ExploreStore(index_dir)
    try:
        observed_via_fresh = fresh.get_setting("current_generation") or "0"
    finally:
        fresh.close()
    via_handle = int(observed_via_handle)
    via_fresh = int(observed_via_fresh)
    assert via_fresh >= 1, (
        f"fresh reader observed generation {via_fresh}; expected >= 1 "
        f"after sibling committed a fresh generation -- build output={stdout_str!r}"
    )
    assert via_handle >= 1, (
        f"handle observed stale generation {via_handle} (expected >= 1); "
        "the in-process handle must reopen after a cross-process swap"
    )
    handle.store.close()


def test_module_markers_are_pinned() -> None:
    assert any(marker.name == "required_auto_integrate_e2e" for marker in pytestmark)
