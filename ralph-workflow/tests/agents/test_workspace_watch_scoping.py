"""Black-box tests for classifier-derived workspace watch scoping (AC #1-#5).

These tests prove that ``WorkspaceMonitor.start()`` schedules ONLY the
classifier-derived excluded subtrees as watchdog watches (instead of
a single recursive root watch that would route all fseventsd churn
through the classifier's classify-drop). Behavior is preserved for
every classifier configuration:

  - Default weights: CACHE/ARTIFACT subtrees excluded; ``src/`` and
    ``.agent/PLAN.md`` still watched.
  - ``cache=1.0``: CACHE subtrees (``.git``, ``.venv``,
    ``node_modules``, ...) kept watched.
  - ``artifact=1.0``: ``.agent/artifacts`` kept watched.
  - ``classifier=None``: single recursive root watch (status quo).

Dynamic scheduling covers ``created`` + ``moved-into-workspace``
directories with a bounded catch-up rescan so pre-watch descendant
source files are not lost.

No real filesystem or watchdog observer is touched. The
``_FakeObserver`` records every ``schedule()`` call into a list and
no-ops ``start``/``stop``/``join``. The in-memory tree is provided
via an injected ``list_subdirs`` callable so no real directory
enumeration runs.
"""

from __future__ import annotations

import os
import os.path
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.agents.invoke._workspace import (
    _WATCH_EXCLUSION_CANDIDATES,
    WorkspaceMonitor,
    _watch_exclusions,
    build_watch_plan,
)
from ralph.agents.invoke._workspace_change_classifier import (
    ARTIFACT_PARENT_DIRS,
    CACHE_PARENT_DIRS,
    WorkspaceChangeClassifier,
)

if TYPE_CHECKING:
    import pytest


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@runtime_checkable
class _DirEventFields(Protocol):
    """Protocol for watchdog events that expose directory-arrival fields."""

    is_directory: bool
    event_type: str
    src_path: str


@runtime_checkable
class _HasDestPath(Protocol):
    """Protocol for watchdog events that expose a destination path."""

    dest_path: str


class _FakeEvent:
    """Stand-in for watchdog events carrying the fields we duck-type on."""

    def __init__(
        self,
        src_path: str,
        dest_path: str | None = None,
        is_directory: bool = False,
        event_type: str = "modified",
    ) -> None:
        self.src_path = src_path
        if dest_path is not None:
            self.dest_path = dest_path
        self.is_directory = is_directory
        self.event_type = event_type


class _FakeObserver:
    """Stand-in for ``watchdog.observers.Observer`` recording schedule calls."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[object, str, bool]] = []
        self.started: bool = False
        self.stopped: bool = False
        self.joined: bool = False

    def schedule(self, event_handler: object, path: str, recursive: bool = False) -> None:
        self.scheduled.append((event_handler, path, recursive))

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        self.joined = True


# ---------------------------------------------------------------------------
# In-memory tree seam
# ---------------------------------------------------------------------------


def _list_subdirs_factory() -> tuple[Callable[[str], list[str]], dict[str, list[str]]]:
    """Build an in-memory ``list_subdirs`` seam + the underlying tree.

    Tree under the fake root ``/ws``::

        /ws                                .git, .venv, node_modules,
                                           __pycache__, .pytest_cache,
                                           src, .agent
        /ws/src                            pkg
        /ws/.agent                         tmp, raw, artifacts, workers
        /ws/.agent/workers                 unit1
    """
    tree: dict[str, list[str]] = {
        "/ws": [".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "src", ".agent"],
        "/ws/src": ["pkg"],
        "/ws/.agent": ["tmp", "raw", "artifacts", "workers"],
        "/ws/.agent/workers": ["unit1"],
    }

    def list_subdirs(abs_dir: str) -> list[str]:
        return list(tree.get(abs_dir, []))

    return list_subdirs, tree


def _list_tree_files_factory(
    files: dict[str, list[str]],
) -> Callable[[str], list[str]]:
    """Build an in-memory ``list_tree_files`` seam.

    Args:
        files: Mapping of directory path -> file paths under it.
    """

    def list_tree_files(abs_dir: str) -> list[str]:
        return list(files.get(abs_dir, []))

    return list_tree_files


def _scheduled_rels(fake: _FakeObserver, workspace: str) -> list[tuple[str, bool]]:
    """Translate the _FakeObserver's absolute-path schedule log to
    workspace-relative POSIX pairs."""
    result: list[tuple[str, bool]] = []
    for _handler, abs_path, recursive in fake.scheduled:
        rel = os.path.relpath(abs_path, str(workspace))
        rel = "" if rel == "." else rel.replace(os.sep, "/")
        result.append((rel, recursive))
    return result


# ---------------------------------------------------------------------------
# Step 1: _WATCH_EXCLUSION_CANDIDATES drift guard
# ---------------------------------------------------------------------------


def test_watch_exclusion_candidates_equal_classifier_zero_weight_parents() -> None:
    """``_WATCH_EXCLUSION_CANDIDATES`` equals the union of
    ``CACHE_PARENT_DIRS`` and ``ARTIFACT_PARENT_DIRS`` (single source
    of truth). Drift guard: if anyone adds a new parent dir to either
    classifier set, the watcher must probe it for exclusion."""
    assert _WATCH_EXCLUSION_CANDIDATES == (CACHE_PARENT_DIRS | ARTIFACT_PARENT_DIRS)


# ---------------------------------------------------------------------------
# Step 1: _watch_exclusions derivation under various classifier configs
# ---------------------------------------------------------------------------


def test_watch_exclusions_default_weights_excludes_all_candidates() -> None:
    """Under the default conservative weights, every candidate is
    excluded (the classifier drops everything in CACHE_PARENT_DIRS
    and ARTIFACT_PARENT_DIRS by default)."""
    assert _watch_exclusions(WorkspaceChangeClassifier()) == (
        CACHE_PARENT_DIRS | ARTIFACT_PARENT_DIRS
    )


def test_watch_exclusions_none_classifier_is_empty() -> None:
    """``classifier=None`` (legacy) means every event counts, so
    NOTHING may be excluded from the watch (the recursive-root
    status quo is preserved)."""
    assert _watch_exclusions(None) == frozenset()


def test_watch_exclusions_cache_weight_one_keeps_cache_subtrees() -> None:
    """With ``cache=1.0`` configured, ``_watch_exclusions`` returns
    ONLY ``ARTIFACT_PARENT_DIRS`` -- the cache roots ``.git/.venv/
    node_modules/__pycache__/.pytest_cache/.mypy_cache/.ruff_cache/
    .agent/tmp/.agent/raw`` are NOT excluded because they now count.

    PA-001 closure: a static exclusion would have wrongly dropped
    cache activity under operator-configured ``cache=1.0``.
    """
    classifier = WorkspaceChangeClassifier(weights={"source": 1.0, "cache": 1.0})
    result = _watch_exclusions(classifier)
    assert result == ARTIFACT_PARENT_DIRS
    # The union contains cache entries; verify they are NOT in the result.
    for cache_root in CACHE_PARENT_DIRS:
        assert cache_root not in result, (
            f"{cache_root!r} should NOT be excluded when cache=1.0"
        )


def test_watch_exclusions_artifact_weight_one_keeps_artifacts() -> None:
    """With ``artifact=1.0`` configured, ``_watch_exclusions`` returns
    ONLY ``CACHE_PARENT_DIRS`` -- ``.agent/artifacts`` is NOT
    excluded because it now counts.

    PA-001 closure: a static exclusion would have wrongly dropped
    artifact activity under operator-configured ``artifact=1.0``.
    """
    classifier = WorkspaceChangeClassifier(weights={"source": 1.0, "artifact": 1.0})
    result = _watch_exclusions(classifier)
    assert result == CACHE_PARENT_DIRS
    for artifact_root in ARTIFACT_PARENT_DIRS:
        assert artifact_root not in result, (
            f"{artifact_root!r} should NOT be excluded when artifact=1.0"
        )


# ---------------------------------------------------------------------------
# Step 1: build_watch_plan pruning + empty-exclusions identity
# ---------------------------------------------------------------------------


def test_build_watch_plan_prunes_excluded_subtrees() -> None:
    """``build_watch_plan`` prunes excluded subtrees under default
    weights. The pruned walk emits one recursive watch per clean
    subtree and non-recursive watches only on ancestors of excluded
    roots."""
    list_subdirs, _tree = _list_subdirs_factory()
    exclusions = _watch_exclusions(WorkspaceChangeClassifier())
    plan = build_watch_plan("/ws", list_subdirs, exclusions)

    # (i) Excluded paths NEVER scheduled
    excluded_paths = [
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".agent/tmp",
        ".agent/raw",
        ".agent/artifacts",
    ]
    for ex in excluded_paths:
        assert all(rel != ex for (rel, _recursive) in plan), (
            f"Excluded path {ex!r} should not be scheduled but plan={plan}"
        )

    # (ii) Descendants of excluded paths are also pruned
    descendants_of_excluded = [
        ".git/objects",
        ".venv/lib",
        "node_modules/lodash",
        "__pycache__/foo",
        ".agent/tmp/foo.log",
        ".agent/raw/stream.bin",
        ".agent/artifacts/plan.json",
    ]
    for sub in descendants_of_excluded:
        assert all(rel != sub for (rel, _recursive) in plan), (
            f"Descendant {sub!r} of an excluded root should not be scheduled"
        )

    # (iii) Root scheduled non-recursive (has excluded descendants)
    assert ("", False) in plan, f"root should be non-recursive but plan={plan}"

    # (iv) 'src' scheduled recursive
    assert ("src", True) in plan, f"'src' should be recursive but plan={plan}"
    # 'src/pkg' must NOT be separately scheduled (covered by the recursive 'src' watch)
    assert all(rel != "src/pkg" for (rel, _recursive) in plan), (
        f"'src/pkg' must not be separately scheduled but plan={plan}"
    )

    # (v) '.agent' scheduled non-recursive (has excluded descendants)
    assert (".agent", False) in plan, f"'.agent' should be non-recursive but plan={plan}"

    # (vi) '.agent/workers' scheduled recursive
    assert (".agent/workers", True) in plan, (
        f"'.agent/workers' should be recursive but plan={plan}"
    )
    # '.agent/workers/unit1' must NOT be separately scheduled
    assert all(rel != ".agent/workers/unit1" for (rel, _recursive) in plan), (
        f"'.agent/workers/unit1' must not be separately scheduled but plan={plan}"
    )


def test_build_watch_plan_empty_exclusions_single_recursive_root() -> None:
    """With empty exclusions, ``build_watch_plan`` yields exactly one
    recursive root watch -- byte-identical to today's behavior."""
    list_subdirs, _tree = _list_subdirs_factory()
    plan = build_watch_plan("/ws", list_subdirs, frozenset())
    assert plan == [("", True)]


def test_build_watch_plan_cache_watched_when_cache_weight_one() -> None:
    """With ``cache=1.0``, ``.git/.venv/node_modules`` ARE scheduled
    recursive (PA-001 regression guard). Only ``.agent/artifacts``
    remains pruned."""
    list_subdirs, _tree = _list_subdirs_factory()
    classifier = WorkspaceChangeClassifier(weights={"source": 1.0, "cache": 1.0})
    exclusions = _watch_exclusions(classifier)
    plan = build_watch_plan("/ws", list_subdirs, exclusions)

    # Root still non-recursive (has excluded descendants under .agent/artifacts)
    assert ("", False) in plan

    # .git, .venv, node_modules ARE scheduled recursive (cache counts now)
    assert (".git", True) in plan
    assert (".venv", True) in plan
    assert ("node_modules", True) in plan

    # .agent still non-recursive (has artifact excluded descendants)
    assert (".agent", False) in plan

    # .agent/artifacts remains pruned
    assert all(rel != ".agent/artifacts" for (rel, _recursive) in plan)
    # .agent/tmp and .agent/raw now NOT pruned (cache=1.0 makes them count)
    assert any(rel == ".agent/tmp" for (rel, _recursive) in plan)
    assert any(rel == ".agent/raw" for (rel, _recursive) in plan)


# ---------------------------------------------------------------------------
# Step 3: scoped start() schedules per classifier config
# ---------------------------------------------------------------------------


def test_start_schedules_scoped_watches_excluding_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default classifier -> ``start()`` schedules exactly the
    ``build_watch_plan`` entries. No excluded subtrees get a watch."""
    list_subdirs, _tree = _list_subdirs_factory()
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=list_subdirs,
        list_tree_files=lambda _d: [],
    )
    monitor.start()

    rels = _scheduled_rels(fake, "/ws")
    assert rels == [
        ("", False),
        ("src", True),
        (".agent", False),
        (".agent/workers", True),
    ]
    # Defensive: no excluded subtree name slipped through
    excluded = {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".agent/tmp",
        ".agent/raw",
        ".agent/artifacts",
    }
    for (rel, _recursive) in rels:
        assert rel not in excluded, f"{rel!r} should not be scheduled"
    assert fake.started is True


def test_start_single_recursive_root_when_classifier_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``classifier=None`` (legacy) -> ``start()`` schedules exactly
    ONE recursive watch on the workspace root (status quo)."""
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=None,
        list_subdirs=lambda _d: [],
        list_tree_files=lambda _d: [],
    )
    monitor.start()

    assert fake.scheduled == [(fake.scheduled[0][0], "/ws", True)]
    assert len(fake.scheduled) == 1
    assert fake.scheduled[0][1] == "/ws"
    assert fake.scheduled[0][2] is True
    assert fake.started is True


def test_start_keeps_cache_watched_when_cache_weight_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``cache=1.0``, ``.git/.venv`` ARE scheduled recursive
    (PA-001 regression guard at the WorkspaceMonitor level)."""
    list_subdirs, _tree = _list_subdirs_factory()
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(weights={"source": 1.0, "cache": 1.0}),
        list_subdirs=list_subdirs,
        list_tree_files=lambda _d: [],
    )
    monitor.start()

    rels = _scheduled_rels(fake, "/ws")
    assert (".git", True) in rels
    assert (".venv", True) in rels
    assert (".agent/artifacts", True) not in rels
    assert (".agent/artifacts", False) not in rels


# ---------------------------------------------------------------------------
# Step 3: dynamic created-dir scheduling
# ---------------------------------------------------------------------------


def test_created_directory_schedules_recursive_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-excluded directory CREATED after ``start()`` gets a
    recursive watch on ``src_path`` (PA-002)."""
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=lambda _d: [],
        list_tree_files=lambda _d: [],
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    event = _FakeEvent(
        src_path="/ws/newpkg",
        is_directory=True,
        event_type="created",
    )
    monitor.dispatch_event(event)

    assert len(fake.scheduled) == initial_count + 1
    last = fake.scheduled[-1]
    assert last[1] == "/ws/newpkg"
    assert last[2] is True


def test_created_under_excluded_root_schedules_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A directory created UNDER an excluded root (``.git/...``)
    schedules NO new watch and no catch-up recording."""
    list_subdirs, _tree = _list_subdirs_factory()
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=list_subdirs,
        list_tree_files=lambda _d: [],
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    event = _FakeEvent(
        src_path="/ws/.git/objects/aa",
        is_directory=True,
        event_type="created",
    )
    monitor.dispatch_event(event)

    assert len(fake.scheduled) == initial_count
    assert monitor.event_count == 0


def test_dynamic_scheduling_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two identical created events for the same path produce exactly
    one new schedule call (no double-watch)."""
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=lambda _d: [],
        list_tree_files=lambda _d: [],
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    event = _FakeEvent(
        src_path="/ws/newpkg",
        is_directory=True,
        event_type="created",
    )
    monitor.dispatch_event(event)
    monitor.dispatch_event(event)

    new_schedules = fake.scheduled[initial_count:]
    assert len(new_schedules) == 1
    assert new_schedules[0][1] == "/ws/newpkg"
    assert new_schedules[0][2] is True


def test_created_under_recursive_ancestor_schedules_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a directory CREATED under an already-recursive
    scoped watch must NOT receive a redundant recursive watch and
    must NOT trigger a catch-up rescan -- the existing ancestor
    watch already covers it.

    Default classifier plans ``/ws/src`` recursive (because src is
    not excluded). Dispatching a created-directory event for
    ``/ws/src/newpkg`` adds NO new ``observer.schedule`` call and
    NO catch-up recording; the existing ``/ws/src`` watch handles
    all descendant events.

    This is the analyzer's regression: a static
    ``rel in _scheduled_watch_paths`` check only catches duplicates
    on the SAME path; without the recursive-ancestor check, a new
    directory under an existing recursive watch would be double-
    watched, and its descendant events would arrive through both
    recursive subscriptions -- doubling ``event_count`` / callbacks
    AND adding the filesystem-event work this refactor is intended
    to avoid.
    """
    list_subdirs, _tree = _list_subdirs_factory()
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    pre_existing = ["/ws/src/newpkg/early.py"]
    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=list_subdirs,
        list_tree_files=lambda d: list(pre_existing) if d == "/ws/src/newpkg" else [],
    )
    monitor.start()
    initial_count = len(fake.scheduled)
    # Sanity: start() plans /ws/src recursive
    scheduled_paths = [path for _h, path, recursive in fake.scheduled]
    assert "/ws/src" in scheduled_paths
    src_recursive = next(
        recursive for _h, path, recursive in fake.scheduled if path == "/ws/src"
    )
    assert src_recursive is True

    event = _FakeEvent(
        src_path="/ws/src/newpkg",
        is_directory=True,
        event_type="created",
    )
    monitor.dispatch_event(event)

    # No new schedule call -- /ws/src is already recursive and covers it.
    assert len(fake.scheduled) == initial_count
    # No catch-up recording either -- the existing watch handles descendants.
    assert monitor.event_count == 0
    assert "/ws/src/newpkg/early.py" not in monitor.changed_files


# ---------------------------------------------------------------------------
# Step 3: moved-into-workspace directory scheduling
# ---------------------------------------------------------------------------


def test_moved_in_directory_schedules_watch_and_counts_descendant_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A directory MOVED into the workspace (watchdog DirMovedEvent,
    ``is_directory=True``, ``event_type='moved'``, ``src_path`` outside,
    ``dest_path`` inside) gets a recursive watch on ``dest_path``.

    The bounded catch-up rescan records pre-existing descendant
    source files exactly once. A subsequent descendant source event
    is recorded exactly once (no loss, no double).

    PA-002 closure: the pre-fix handler only checked
    ``_HasSrcPath`` and would have skipped the moved-in subtree.
    """
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    pre_existing = ["/ws/imported/mod.py"]
    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=lambda _d: [],
        list_tree_files=lambda d: list(pre_existing) if d == "/ws/imported" else [],
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    move_event = _FakeEvent(
        src_path="/outside/imported",
        dest_path="/ws/imported",
        is_directory=True,
        event_type="moved",
    )
    monitor.dispatch_event(move_event)

    new_schedules = fake.scheduled[initial_count:]
    assert len(new_schedules) == 1
    assert new_schedules[0][1] == "/ws/imported"
    assert new_schedules[0][2] is True

    # Catch-up recorded the pre-existing source file exactly once.
    assert monitor.event_count == 1
    assert "/ws/imported/mod.py" in monitor.changed_files

    # Subsequent descendant source event records exactly once.
    modify_event = _FakeEvent(
        src_path="/ws/imported/new.py",
        is_directory=False,
        event_type="modified",
    )
    monitor.dispatch_event(modify_event)
    assert monitor.event_count == 2
    assert "/ws/imported/mod.py" in monitor.changed_files
    assert "/ws/imported/new.py" in monitor.changed_files


def test_move_into_excluded_destination_schedules_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``dest_path='/ws/.git/imported'`` -> NO schedule call AND NO
    catch-up recording (the destination is inside an excluded subtree).
    """
    list_subdirs, _tree = _list_subdirs_factory()
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=list_subdirs,
        list_tree_files=lambda _d: [],
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    event = _FakeEvent(
        src_path="/outside/imported",
        dest_path="/ws/.git/imported",
        is_directory=True,
        event_type="moved",
    )
    monitor.dispatch_event(event)

    assert len(fake.scheduled) == initial_count
    assert monitor.event_count == 0


def test_created_directory_with_dotdot_prefixed_name_schedules_recursive_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a legal in-workspace directory whose name starts with
    two dots (e.g. ``/ws/..keep``) must NOT be rejected as an
    out-of-workspace arrival. The fix narrows the rejection to a true
    parent traversal (``rel == '..'`` or ``rel.startswith('../')``),
    so legitimate dot-dot-prefixed directory names receive a recursive
    watch.

    AC-04 requires a non-excluded directory created after start to
    receive a recursive watch; only out-of-workspace destinations
    should be rejected.
    """
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=lambda _d: [],
        list_tree_files=lambda _d: [],
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    event = _FakeEvent(
        src_path="/ws/..keep",
        is_directory=True,
        event_type="created",
    )
    monitor.dispatch_event(event)

    new_schedules = fake.scheduled[initial_count:]
    assert len(new_schedules) == 1
    assert new_schedules[0][1] == "/ws/..keep"
    assert new_schedules[0][2] is True


def test_created_directory_at_workspace_root_dotdot_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sister guard: a directory whose src_path equals ``/ws/..`` (the
    parent traversal itself) IS still rejected -- only legal
    dot-dot-prefixed NAMES are accepted.
    """
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=lambda _d: [],
        list_tree_files=lambda _d: [],
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    event = _FakeEvent(
        src_path="/ws/..",
        is_directory=True,
        event_type="created",
    )
    monitor.dispatch_event(event)

    assert len(fake.scheduled) == initial_count


def test_move_out_of_workspace_destination_schedules_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``dest_path='/elsewhere/x'`` -> NO schedule call (destination
    is outside the workspace root)."""
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=lambda _d: [],
        list_tree_files=lambda _d: [],
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    event = _FakeEvent(
        src_path="/outside/x",
        dest_path="/elsewhere/x",
        is_directory=True,
        event_type="moved",
    )
    monitor.dispatch_event(event)

    assert len(fake.scheduled) == initial_count


# ---------------------------------------------------------------------------
# Step 3: catch-up transition (no-loss)
# ---------------------------------------------------------------------------


def test_catchup_records_prewatch_file_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing source file under a newly-created directory
    is recorded exactly once via the catch-up rescan (PA-003). No
    source activity is lost across the watch-scope transition."""
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    pre_existing = ["/ws/newpkg/early.py"]
    monitor = WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        list_subdirs=lambda _d: [],
        list_tree_files=lambda d: list(pre_existing) if d == "/ws/newpkg" else [],
    )
    monitor.start()

    event = _FakeEvent(
        src_path="/ws/newpkg",
        is_directory=True,
        event_type="created",
    )
    monitor.dispatch_event(event)

    assert monitor.event_count == 1
    assert "/ws/newpkg/early.py" in monitor.changed_files


# ---------------------------------------------------------------------------
# Step 3: record_event equivalence
# ---------------------------------------------------------------------------


def test_record_event_counts_unchanged_equivalence(tmp_path: Path) -> None:
    """``record_event`` produces identical observable behavior under
    the new constructor: ``src/app.py``, ``.agent/PLAN.md``,
    ``README.md`` count; ``.git/index``, ``.venv/lib/x.py``,
    ``.agent/raw/out.log``, ``.agent/artifacts/plan.json``, and
    ``foo.log`` do NOT. ``last_event_at`` advances only for the
    three counted events."""
    monitor = WorkspaceMonitor(
        tmp_path,
        classifier=WorkspaceChangeClassifier(),
    )
    paths = [
        "src/app.py",
        ".agent/PLAN.md",
        ".git/index",
        ".venv/lib/x.py",
        ".agent/raw/out.log",
        ".agent/artifacts/plan.json",
        "README.md",
        "foo.log",
    ]
    for p in paths:
        monitor.record_event(str(tmp_path / p))

    assert monitor.event_count == 3
    expected_files = {
        str(tmp_path / "src/app.py"),
        str(tmp_path / ".agent/PLAN.md"),
        str(tmp_path / "README.md"),
    }
    assert monitor.changed_files == expected_files
    assert monitor.last_event_at is not None
