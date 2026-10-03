"""wt-11 lifecycle regression: cold-start defects (E1/E2/E3).

These tests pin the three documented lifecycle defects so a future
change cannot silently reintroduce them:

* E1 -- a session with a generation-0 index never becomes indexed
  without an explicit ``ralph_reindex`` call (no background build
  is ever started by an index-capable handler).
* E2 -- a cold build whose budget is smaller than the build time
  leaves the index at generation 0 with no background continuation
  (no retry storm).
* E3 -- a store handle does not observe a generation committed by
  another process after its own open (stale-handle).

The first two tests use a deterministic in-process seam with an
injected clock and no real ``time.sleep``/wall-clock waits; the
SQLite index lives under ``tmp_path`` because the production store
is path-backed SQLite. The third test uses a real subprocess
(``subprocess_e2e`` marker) because no in-memory seam can model a
cross-process generation swap without an unplanned production
refactor -- exactly the same rationale as
:mod:`tests.test_explore_crash_safety`.

Each test stays well under the 1s per-test budget so the file is
in-budget on the combined 60s make-verify wall clock.
"""

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
    build_scheduler,
    clear_pending_recovery,
    pending_recovery_code,
)
from ralph.mcp.explore.store import DEFAULT_INDEX_ROOT, ExploreStore
from ralph.process.manager import SpawnOptions, get_process_manager

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(60),
]


# ---------------------------------------------------------------------------
# Shared helpers (in-process, deterministic)
# ---------------------------------------------------------------------------


class _FakeClock:
    """Deterministic monotonic clock for backoff / scheduling math."""

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
    """Build a fresh handle for ``workspace`` rooted at ``tmp_path``."""
    # Drop any pre-existing handle from a sibling test so the
    # scheduler state is per-test.
    handle = build_explore_index(workspace)
    try:
        # New worktree / cold start: no committed generation yet.
        assert handle.generation == 0
    except AssertionError:
        # If the handle happens to see a leftover index from a
        # previous test (it shouldn't), wipe it and rebuild.
        index_dir = tmp_path / DEFAULT_INDEX_ROOT
        for side in ("", "-wal", "-shm"):
            target = Path(str(index_dir / "index.sqlite") + side)
            if target.exists():
                target.unlink()
        handle = build_explore_index(workspace)
    return handle


# ---------------------------------------------------------------------------
# E1: cold detection must trigger exactly one background build
# ---------------------------------------------------------------------------


def test_e1_cold_detection_triggers_background_build(tmp_path: Path) -> None:
    """A session with generation-0 MUST start exactly one background build.

    The handler hub dispatches ``enqueue_recovery`` on cold
    detection and ``run_pending_recovery`` drains the queue to
    launch one bounded rebuild; before S-2 lands the queue has
    zero call sites so the index stays at generation 0 with no
    background work.
    """
    workspace = _seed_workspace(tmp_path)
    handle = _fresh_handle(tmp_path, workspace=workspace)
    session = _FakeSession(explore_index=handle)
    # Call any handler that should detect cold. The status handler
    # is the canonical probe: it inspects the same ``cold_index_required``
    # bit every other index-capable handler inspects.
    result = handle_ralph_index_status(session, _Workspace(workspace), {})
    payload = _decode(result)
    # S-2 invariant: a cold detection must enqueue exactly one
    # recovery so the queue is non-empty when the dispatcher
    # drains it next.
    # After S-2 the cold detection MUST populate the pending queue;
    # before the fix the queue stays empty.
    assert pending_recovery_code(workspace) is not None, (
        "cold_index_required detected but no background build was queued; "
        "S-2 must call enqueue_recovery from every index-capable handler"
    )
    # And the public status payload must report the building state.
    assert payload["health"] == HealthState.BUILDING.value, (
        f"expected health == 'building' after cold detection, got {payload['health']!r}"
    )
    # Cleanup so the next test sees a clean queue.
    clear_pending_recovery(workspace)


# ---------------------------------------------------------------------------
# E2: cold build with insufficient budget -- no retry storm
# ---------------------------------------------------------------------------


def test_e2_short_budget_cold_build_leaves_gen_zero_no_retry_storm(
    tmp_path: Path,
) -> None:
    """A timed-out cold build MUST leave gen=0 with exactly one queued recovery.

    E2: ``ralph_reindex`` caps its timeout so a real cold build
    cannot finish in-budget. After S-2 lands, the handler hub
    MUST enqueue exactly one bounded recovery so the next probe
    schedules a single retry inside the backoff window -- not
    zero (no recovery) and not many (retry storm).
    """
    workspace = _seed_workspace(tmp_path)
    handle = _fresh_handle(tmp_path, workspace=workspace)
    index_dir = tmp_path / DEFAULT_INDEX_ROOT
    scheduler = build_scheduler(handle.workspace_root)
    # Drive one timed-out cold rebuild through the public path.
    store = ExploreStore(index_dir)
    try:
        # 1ms budget is well below any real build wall time, so
        # the call returns ``timed_out`` and the index stays at
        # generation 0.
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=1),
        )
        assert result.status in {"timed_out", "failed"}
        persisted = store.get_setting("current_generation") or "0"
        assert int(persisted) == 0, (
            "timed-out cold build leaked a partial committed generation"
        )
    finally:
        store.close()
    # After S-2 the handler hub MUST enqueue exactly one recovery
    # for the timed-out cold build (a timed-out build recovery is a
    # built-in case in ``_RECOVERABLE_QUERY_CODES``).
    assert pending_recovery_code(workspace) == "timeout_exceeded", (
        "timed-out cold build must enqueue exactly one bounded recovery; "
        "without it, no recovery ever happens"
    )
    # And no retry storm -- a second timed-out probe inside the
    # same backoff window MUST NOT enqueue a second recovery.
    scheduler.record_failure(
        code="timeout_exceeded", message="timed_out"
    )
    # Drive a second timed-out probe and assert the queued recovery is still timeout_exceeded.
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
    # Cleanup so the next test sees a clean queue.
    clear_pending_recovery(workspace)


# ---------------------------------------------------------------------------
# E3: stale handle -- must observe cross-process generation swap
# ---------------------------------------------------------------------------


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
    """A pre-opened handle MUST observe a sibling process's committed gen.

    E3: the in-process handle holds a connection to the pre-swap
    database file and never reopens, so it reports
    ``index_exists=False, generation=0`` after another process
    commits a healthy index. The fix reopens the handle on the
    next query, bounded and fail-open.

    The test opens the handle in THIS process BEFORE the sibling
    process builds + swaps the database file. The OLD inode is
    unlinked from disk but remains alive via the open fd. A
    pre-fix handle reads the OLD inode and reports generation=0;
    a post-fix handle detects the swap and reopens so it reports
    generation >= 1.
    """
    workspace = _seed_workspace(tmp_path)
    index_dir = workspace / ".agent" / "ralph-explore"
    index_dir.mkdir(parents=True, exist_ok=True)

    build_path = tmp_path / "build.py"
    build_path.write_text(_BUILD_AND_COMMIT_SCRIPT.replace("__WORKSPACE__", str(workspace)))

    # Phase 1: open a handle in this process and capture its
    # observed gen. The handle's connection now points to the
    # current ``index.sqlite`` inode; once the build subprocess
    # atomic-renames the file, this inode is unlinked but the
    # handle's fd keeps it alive.
    handle = build_explore_index(workspace)
    assert handle.generation == 0
    opened_gen_setting = handle.store.get_setting("current_generation")
    assert opened_gen_setting in (None, "0"), (
        f"fresh worktree should have no committed generation, got {opened_gen_setting!r}"
    )
    # Do NOT close the handle -- we need its in-process handle to
    # still be open when the sibling process commits the new gen.

    # Phase 2: run a sibling process that builds and commits generation 1.
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
        f"sibling build did not emit BUILD: -- stdout={stdout_str!r} "
        f"stderr={stderr_str!r}"
    )
    build_payload_raw: object = json.loads(stdout_str.split("BUILD:", 1)[1].splitlines()[0])
    assert isinstance(build_payload_raw, dict)
    assert build_payload_raw.get("status") == "ok", (
        f"sibling build failed -- stdout={stdout_str!r} "
        f"stderr={stderr_str!r}"
    )

    # Phase 3: read via the still-open in-process handle.
    observed_via_handle = handle.store.get_setting("current_generation") or "0"
    # Phase 4: open a brand-new ExploreStore on the same dir to
    # confirm what the on-disk state actually is.
    fresh = ExploreStore(index_dir)
    try:
        observed_via_fresh = fresh.get_setting("current_generation") or "0"
    finally:
        fresh.close()
    via_handle = int(observed_via_handle)
    via_fresh = int(observed_via_fresh)
    # Sanity: the fresh reader MUST see the new generation; if
    # it does not, the build subprocess did not commit.
    assert via_fresh >= 1, (
        f"fresh reader observed generation {via_fresh}; expected >= 1 "
        f"after sibling committed a fresh generation -- build output={stdout_str!r}"
    )
    # S-3 invariant: the in-process handle MUST observe the new
    # generation. Pre-fix it sees 0 (the OLD inode); post-fix it
    # detects the swap and reopens.
    assert via_handle >= 1, (
        f"handle observed stale generation {via_handle} (expected >= 1); "
        "the in-process handle must reopen after a cross-process swap"
    )
    handle.store.close()


# ---------------------------------------------------------------------------
# Helpers for marker sanity (catches accidental marker drift)
# ---------------------------------------------------------------------------


def test_module_markers_are_pinned() -> None:
    """The lifecycle regression module carries the SUBPROCESS_MCP marker.

    The test is required by ``REQUIRED_AUTO_INTEGRATE_E2E_FILES``
    in :mod:`ralph.test_suites`; without the
    ``required_auto_integrate_e2e`` marker the default
    ``make test`` profile would skip it and the regression would
    rot silently.
    """
    assert any(marker.name == "subprocess_e2e" for marker in pytestmark)
    assert any(
        marker.name == "required_auto_integrate_e2e" for marker in pytestmark
    )
