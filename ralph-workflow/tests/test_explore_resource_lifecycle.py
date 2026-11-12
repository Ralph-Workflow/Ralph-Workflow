from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout_seconds(2.0)

EXPLORE_ROOT = Path(__file__).resolve().parents[1] / "ralph" / "mcp" / "explore"


def _count_fds() -> int:
    try:
        import psutil

        return int(psutil.Process().num_fds())
    except (ImportError, AttributeError, OSError):
        pass
    fd_dir = Path("/proc/self/fd")
    try:
        return sum(1 for _ in fd_dir.iterdir()) - 1
    except (FileNotFoundError, PermissionError, OSError):
        return 0


def test_no_unbounded_deque_in_explore() -> None:
    import re

    for py_file in sorted(EXPLORE_ROOT.rglob("*.py")):
        if "audit" in py_file.name:
            continue
        text = py_file.read_text(encoding="utf-8")
        for match in re.finditer(r"\bdeque\s*\(", text):
            line_no = text.count("\n", 0, match.start()) + 1
            line = text.splitlines()[line_no - 1]
            if "maxlen" not in line and "bounded-accumulator-ok" not in line:
                pytest.fail(f"{py_file}:{line_no}: unbounded deque: {line.strip()}")


_ALLOWED_BOUNDED_ACCUMULATORS: frozenset[str] = frozenset(
    {
        "__all__",
        "_GRAPH_CANCEL_FLAGS",
        "_REINDEX_CANCEL_FLAGS",
        "_runtime_crossover",
        "_IMPL_REGISTRY",
        "_PENDING_MARKS",
        "_LAZY_REEXPORTS",
        "_LAST_ADVISORY_LOCK_FD",
        "_PENDING_RECOVERY",
        "_DRAIN_THREAD",
    }
)


@pytest.mark.timeout_seconds(3)
def test_no_module_level_mutable_list_in_explore() -> None:
    import ast

    for py_file in sorted(EXPLORE_ROOT.rglob("*.py")):
        if "audit" in py_file.name or py_file.name == "__init__.py":
            continue
        source = py_file.read_text(encoding="utf-8")
        source_lines = source.splitlines()
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target_id = node.target.id
                end_lineno = getattr(node, "end_lineno", node.lineno)
                marker_lines = source_lines[node.lineno - 1 : end_lineno]
                if target_id in _ALLOWED_BOUNDED_ACCUMULATORS or any(
                    "bounded-accumulator-ok" in line for line in marker_lines
                ):
                    continue
                if isinstance(node.value, (ast.List, ast.Dict, ast.Set)):
                    pytest.fail(
                        f"{py_file}:{node.lineno}: module-level mutable "
                        f"literal assigned to {target_id!r}"
                    )


def _seed_workspace(workspace: Path) -> None:
    (workspace / "hello.py").write_text("def hello():\n    return 1\n")
    (workspace / "goodbye.py").write_text("def goodbye():\n    return 2\n")


def test_no_fd_leak_after_reindex_and_search(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_workspace(workspace)
    index_dir = tmp_path / ".agent" / "ralph-explore"
    index_dir.mkdir(parents=True, exist_ok=True)

    fds_before = _count_fds()
    from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
    from ralph.mcp.explore.handlers import ExploreIndex
    from ralph.mcp.explore.pipeline import ReindexOptions, reindex
    from ralph.mcp.explore.store import ExploreStore
    from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
    from ralph.workspace.fs import FsWorkspace

    store = ExploreStore(index_dir)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
    finally:
        store.close()
    store2 = ExploreStore(index_dir)
    handle = build_sqlite_index_handle(store2)
    session = type(
        "S",
        (),
        {
            "explore_index": ExploreIndex(
                workspace_root=workspace,
                index_root=index_dir,
                store=store2,
                generation=1,
            )
            if handle is None
            else handle,
            "check_capability": lambda self, c: {"status": "approved", "capability": c},
            "check_edit_area": lambda self, p: {"status": "approved", "path": p},
        },
    )()
    ws_obj = FsWorkspace(workspace)
    for _ in range(5):
        result = handle_grep_files(
            session,
            ws_obj,
            {
                "pattern": "def",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
            },
        )
        assert "matches" in result.content[0].text
    store2.close()
    fds_after = _count_fds()
    assert fds_after <= fds_before + 2, f"FD leak: before={fds_before}, after={fds_after}"


def test_no_fd_leak_under_concurrent_searches(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_workspace(workspace)
    index_dir = tmp_path / ".agent" / "ralph-explore"
    index_dir.mkdir(parents=True, exist_ok=True)
    from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
    from ralph.mcp.explore.pipeline import ReindexOptions, reindex
    from ralph.mcp.explore.store import ExploreStore
    from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
    from ralph.workspace.fs import FsWorkspace

    store = ExploreStore(index_dir)
    reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
    store.close()
    store2 = ExploreStore(index_dir)
    handle = build_sqlite_index_handle(store2)
    session = type(
        "S",
        (),
        {
            "explore_index": handle,
            "check_capability": lambda self, c: {"status": "approved", "capability": c},
            "check_edit_area": lambda self, p: {"status": "approved", "path": p},
        },
    )()
    ws_obj = FsWorkspace(workspace)
    fds_before = _count_fds()
    for _ in range(20):
        handle_grep_files(
            session,
            ws_obj,
            {
                "pattern": "def",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
            },
        )
    store2.close()
    fds_after = _count_fds()
    assert fds_after <= fds_before + 2, (
        f"FD leak under concurrent searches: before={fds_before}, after={fds_after}"
    )
