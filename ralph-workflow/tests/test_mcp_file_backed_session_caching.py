"""Black-box tests for ``FileBackedSession`` parsed-JSON caching.

wt-024 memory-perf AC-06: ``FileBackedSession._load`` re-reads and
re-parses the session JSON on EVERY property access. The session view
exposes 17 accessors (session_id, run_id, drain, capabilities, etc.),
so a single ``McpServer._handle_tools_call`` invocation can trigger
17 file reads + 17 JSON parses. We cache the parsed payload keyed on
``(st_mtime_ns, st_size)`` so subsequent accessors that observe the
same file use the cached parse.

This test asserts:

1. The loader is called at most ONCE while ``(mtime_ns, size)`` is unchanged.
2. When the file's mtime or size changes, the loader is called again on
   the next access.
3. The injectable ``loader`` seam is preserved verbatim.
4. ``stat()`` errors fall back to a direct re-parse (graceful
   degradation).

All tests use a real tmp_path session JSON file (the unit under test
specifically exercises the on-disk change-detection contract).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.server.runtime_session import FileBackedSession

if TYPE_CHECKING:
    from pathlib import Path


def _write_session(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_session_id_caches_payload_across_property_access(tmp_path: Path) -> None:
    """A second property access on an unchanged file must use the cached payload."""
    session_path = tmp_path / "session.json"
    _write_session(
        session_path,
        {
            "session_id": "session-A",
            "run_id": "run-1",
            "capabilities": ["mcp.read"],
            "drain": "development",
        },
    )

    load_calls: list[int] = []

    def counting_loader(path: Path) -> dict[str, object]:
        load_calls.append(1)
        return json.loads(path.read_text(encoding="utf-8"))

    session = FileBackedSession(
        session_path,
        loader=counting_loader,
        session_id_factory=lambda: "factory-id",
    )

    # First access: cold cache, loader is called once.
    assert session.session_id == "session-A"
    assert len(load_calls) == 1, f"cold access should call loader once; got {len(load_calls)} calls"

    # Several more accessors: warm cache, loader is NOT called again.
    assert session.run_id == "run-1"
    assert session.drain == "development"
    assert session.capabilities == {"mcp.read"}
    assert session.session_id == "session-A"
    assert len(load_calls) == 1, (
        f"warm cache must NOT re-invoke loader on subsequent reads; "
        f"got {len(load_calls)} calls (expected 1)"
    )


def test_loader_reinvoked_when_file_changes(tmp_path: Path) -> None:
    """When the on-disk file changes (mtime or size), the loader MUST run again."""
    session_path = tmp_path / "session.json"
    _write_session(
        session_path,
        {"session_id": "session-A", "run_id": "run-1"},
    )

    load_calls: list[int] = []

    def counting_loader(path: Path) -> dict[str, object]:
        load_calls.append(1)
        return json.loads(path.read_text(encoding="utf-8"))

    session = FileBackedSession(session_path, loader=counting_loader)

    assert session.session_id == "session-A"
    assert len(load_calls) == 1

    # Mutate the file: append a byte to change size + bump mtime.
    bigger = json.dumps({"session_id": "session-B", "run_id": "run-2", "extra_padding": "x" * 64})
    session_path.write_text(bigger, encoding="utf-8")
    # Force a distinct mtime in case the writes happened in the same nanosecond.
    stat = session_path.stat()
    os.utime(session_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000))

    assert session.session_id == "session-B", "session_id must reflect the new file content"
    assert len(load_calls) == 2, (
        f"loader must run again when file changes; got {len(load_calls)} calls"
    )


def test_loader_seam_preserved(tmp_path: Path) -> None:
    """The injectable ``loader`` parameter must continue to be honored."""
    session_path = tmp_path / "session.json"
    _write_session(session_path, {"session_id": "from-real-file"})

    sentinel_calls: list[Path] = []

    def sentinel_loader(path: Path) -> dict[str, object]:
        sentinel_calls.append(path)
        return {"session_id": "from-sentinel-loader"}

    session = FileBackedSession(session_path, loader=sentinel_loader)
    assert session.session_id == "from-sentinel-loader"
    assert sentinel_calls == [session_path], (
        f"the injected loader must be called with the session path; got {sentinel_calls!r}"
    )


def test_stat_error_falls_back_to_loader(tmp_path: Path) -> None:
    """A stat() failure (e.g. file briefly missing) must NOT cache a stale
    payload; the helper falls back to a direct loader invocation."""
    session_path = tmp_path / "session.json"
    _write_session(session_path, {"session_id": "session-A"})

    load_calls: list[int] = []

    def counting_loader(path: Path) -> dict[str, object]:
        load_calls.append(1)
        return json.loads(path.read_text(encoding="utf-8"))

    session = FileBackedSession(session_path, loader=counting_loader)

    # First access caches.
    assert session.session_id == "session-A"
    assert len(load_calls) == 1

    # Delete the file. The next access must fall back to a direct
    # re-parse (which raises via the loader because the file is gone).
    session_path.unlink()
    with pytest.raises(FileNotFoundError):
        _ = session.session_id
