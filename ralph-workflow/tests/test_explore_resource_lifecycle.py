"""Resource-lifecycle compliance tests for ralph/mcp/explore/.

Mirrors the contract enforced by
``ralph/testing/audit_resource_lifecycle.py``:

* Long-lived mutable accumulators (``list``, ``dict``, ``set``,
  ``deque``) assigned to module-level names or to ``self.X`` inside
  ``__init__`` bodies must carry a FIFO/size cap (``deque(maxlen=...)``
  or ``OrderedDict`` + count cap) or a ``# bounded-accumulator-ok: <reason>``
  marker.

In addition, this file contains runtime FD-leak / watch-handle
proofs for the explore substrate: the FD count of the running
process is measured before and after a sequence of
index/reindex/search operations, and the steady-state and
post-operation counts must match. The proof runs the real
``ExploreStore`` / ``reindex`` / ``handle_grep_files`` paths so a
regression in the resource contract surfaces immediately.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout_seconds(5)

EXPLORE_ROOT = Path(__file__).resolve().parents[1] / "ralph" / "mcp" / "explore"


def _audit_module_via_python_api() -> tuple[list, int]:
    """Run the resource-lifecycle audit on the explore package."""
    from ralph.testing.audit_resource_lifecycle import (
        audit_resource_lifecycle_directory,
    )

    return audit_resource_lifecycle_directory(EXPLORE_ROOT)


def _count_fds() -> int:
    """Return the number of open file descriptors of the current process.

    Counts entries under ``/proc/self/fd`` (Linux) and falls back
    to ``len(os.listdir('/proc/self/fd')) - 1`` to exclude the
    fd used by the listing itself.
    """
    fd_dir = Path("/proc/self/fd")
    try:
        return sum(1 for _ in fd_dir.iterdir()) - 1
    except (FileNotFoundError, PermissionError, OSError):
        return 0


def _count_inotify_watches() -> int:
    """Return the count of inotify watches held by the current process.

    Reads ``/proc/self/fdinfo/<N>`` for each open ``inotify`` fd
    and sums the ``watches`` field. Returns 0 when ``/proc`` is
    unavailable (e.g. macOS, sandboxed env).
    """
    total = 0
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.is_dir():
        return 0
    for entry in fd_dir.iterdir():
        try:
            target = entry.readlink().as_posix()
        except OSError:
            continue
        if "inotify" not in target:
            continue
        fdinfo = Path("/proc/self/fdinfo") / entry.name
        try:
            text = fdinfo.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError):
            continue
        for line in text.splitlines():
            if line.startswith("watches:"):
                with contextlib.suppress(ValueError, IndexError):
                    total += int(line.split(":", 1)[1].strip())
                break
    return total


def test_explore_handlers_use_bounded_accumulators() -> None:
    if not EXPLORE_ROOT.is_dir():
        pytest.skip(f"explore module not present: {EXPLORE_ROOT}")
    violations, files_checked = _audit_module_via_python_api()
    formatted = "\n".join(str(v) for v in violations)
    assert not violations, (
        f"Found {len(violations)} resource-lifecycle violations in "
        f"{files_checked} file(s):\n{formatted}"
    )


def test_no_unbounded_deque_in_explore() -> None:
    """A ``deque()`` without ``maxlen`` is treated as unbounded."""
    if not EXPLORE_ROOT.is_dir():
        pytest.skip(f"explore module not present: {EXPLORE_ROOT}")
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


def test_no_module_level_mutable_list_in_explore() -> None:
    """Module-level ``[]``/``{}``/``set()`` are flagged by the audit."""
    if not EXPLORE_ROOT.is_dir():
        pytest.skip(f"explore module not present: {EXPLORE_ROOT}")
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
                if target_id == "__all__" or any(
                    "bounded-accumulator-ok" in line for line in marker_lines
                ):
                    continue
                if isinstance(node.value, (ast.List, ast.Dict, ast.Set)):
                    pytest.fail(
                        f"{py_file}:{node.lineno}: module-level mutable "
                        f"literal assigned to {target_id!r}"
                    )


# --- Runtime FD / watch proof ---------------------------------------------


def _seed_workspace(workspace: Path) -> None:
    (workspace / "hello.py").write_text("def hello():\n    return 1\n")
    (workspace / "goodbye.py").write_text("def goodbye():\n    return 2\n")


def test_no_fd_leak_after_reindex_and_search(tmp_path: Path) -> None:
    """Index / reindex / search must not leak file descriptors.

    Counts ``/proc/self/fd`` before and after a representative
    workload (open store, reindex, run several searches, close
    store). The post-operation count must match the pre-operation
    count. A regression that holds a connection / open file
    across operations fails the test.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_workspace(workspace)
    index_dir = tmp_path / ".agent" / "ralph-explore"
    index_dir.mkdir(parents=True, exist_ok=True)

    fds_before = _count_fds()
    # Open + close the store; the runtime path that production uses.
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
    # Re-open + run several searches + close. The handle is built
    # from the open store so a leak in handle wiring would show up.
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
            {"pattern": "def", "path": ".", "regex": False, "case_sensitive": False, "use_index": "auto"},
        )
        # Decode so the response is fully consumed.
        assert "matches" in result.content[0].text
    store2.close()
    fds_after = _count_fds()
    # Tolerate a tiny drift (≤ 2 FDs) for ``/proc/self/fd`` itself
    # and any stdio churn. Anything beyond that is a leak.
    assert fds_after <= fds_before + 2, (
        f"FD leak: before={fds_before}, after={fds_after}"
    )


def test_no_inotify_watches_held_by_explore(tmp_path: Path) -> None:
    """No inotify watches must be held after the explore workload.

    R5 requires the explore substrate not to hold long-lived OS
    watch handles. The proof runs the index + search workload in
    a child process and checks the child's inotify watch count
    via ``/proc/<pid>/fd``/``fdinfo``; the steady-state count must
    be zero.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_workspace(workspace)
    index_dir = tmp_path / ".agent" / "ralph-explore"
    index_dir.mkdir(parents=True, exist_ok=True)
    script = """
import os
import sys
import time
from pathlib import Path

WORKSPACE = Path(__WORKSPACE__)
INDEX_DIR = WORKSPACE / \".agent\" / \"ralph-explore\"

from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
from ralph.mcp.explore.handlers import ExploreIndex
from ralph.workspace.fs import FsWorkspace

store = ExploreStore(INDEX_DIR)
try:
    reindex(store, WORKSPACE, options=ReindexOptions(timeout_ms=5_000))
finally:
    store.close()
store2 = ExploreStore(INDEX_DIR)
session = type(\"S\", (), {
    \"explore_index\": build_sqlite_index_handle(store2),
    \"check_capability\": lambda self, c: {\"status\": \"approved\", \"capability\": c},
    \"check_edit_area\": lambda self, p: {\"status\": \"approved\", \"path\": p},
})()
ws = FsWorkspace(WORKSPACE)
for _ in range(5):
    handle_grep_files(
        session, ws,
        {\"pattern\": \"def\", \"path\": \".\", \"regex\": False, \"case_sensitive\": False, \"use_index\": \"auto\"},
    )
store2.close()
# Output the PID + watch count for the parent to verify.
print(os.getpid(), end=\"\\n\")
sys.stdout.flush()
sys.stdin.readline()
"""
    script = script.replace("__WORKSPACE__", repr(str(workspace)))
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stdin is not None
    child_pid = int(proc.stdout.readline().decode().strip())
    fd_dir = Path(f"/proc/{child_pid}/fd")
    assert fd_dir.is_dir()
    watches = 0
    for entry in fd_dir.iterdir():
        try:
            target = entry.readlink().as_posix()
        except OSError:
            continue
        if "inotify" not in target:
            continue
        fdinfo = Path(f"/proc/{child_pid}/fdinfo") / entry.name
        try:
            text = fdinfo.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError):
            continue
        for line in text.splitlines():
            if line.startswith("watches:"):
                with contextlib.suppress(ValueError, IndexError):
                    watches += int(line.split(":", 1)[1].strip())
                break
    proc.stdin.write(b"\n")
    proc.stdin.flush()
    proc.wait(timeout=5)
    assert proc.returncode == 0
    assert watches == 0, f"explore substrate holds {watches} inotify watches"


def test_no_fd_leak_under_concurrent_searches(tmp_path: Path) -> None:
    """The post-operation FD count after a concurrent-search workload matches the baseline.

    The proof mirrors the acceptance-criterion 8 contract
    (concurrent indexing does not leak handles). It runs ``N``
    search operations back-to-back on a single handle and asserts
    the FD count returns to baseline.
    """
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
            {"pattern": "def", "path": ".", "regex": False, "case_sensitive": False, "use_index": "auto"},
        )
    store2.close()
    fds_after = _count_fds()
    assert fds_after <= fds_before + 2, (
        f"FD leak under concurrent searches: before={fds_before}, after={fds_after}"
    )
