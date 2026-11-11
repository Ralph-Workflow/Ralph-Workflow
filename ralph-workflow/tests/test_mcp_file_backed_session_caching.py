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

    assert session.session_id == "session-A"
    assert len(load_calls) == 1, f"cold access should call loader once; got {len(load_calls)} calls"

    assert session.run_id == "run-1"
    assert session.drain == "development"
    assert session.capabilities == {"mcp.read"}
    assert session.session_id == "session-A"
    assert len(load_calls) == 1, (
        f"warm cache must NOT re-invoke loader on subsequent reads; "
        f"got {len(load_calls)} calls (expected 1)"
    )


def test_loader_reinvoked_when_file_changes(tmp_path: Path) -> None:
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

    bigger = json.dumps({"session_id": "session-B", "run_id": "run-2", "extra_padding": "x" * 64})
    session_path.write_text(bigger, encoding="utf-8")
    stat = session_path.stat()
    os.utime(session_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000))

    assert session.session_id == "session-B", "session_id must reflect the new file content"
    assert len(load_calls) == 2, (
        f"loader must run again when file changes; got {len(load_calls)} calls"
    )


def test_loader_seam_preserved(tmp_path: Path) -> None:
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
    session_path = tmp_path / "session.json"
    _write_session(session_path, {"session_id": "session-A"})

    load_calls: list[int] = []

    def counting_loader(path: Path) -> dict[str, object]:
        load_calls.append(1)
        return json.loads(path.read_text(encoding="utf-8"))

    session = FileBackedSession(session_path, loader=counting_loader)

    assert session.session_id == "session-A"
    assert len(load_calls) == 1

    session_path.unlink()
    with pytest.raises(FileNotFoundError):
        _ = session.session_id
