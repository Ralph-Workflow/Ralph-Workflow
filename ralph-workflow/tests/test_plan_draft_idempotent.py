"""Black-box tests for idempotent plan-draft re-stage writes.

Verifies that :func:`save_plan_draft` honors a semantic
``updated_at``-exempt comparison so a byte-identical re-stage of the
same sections performs zero filesystem mutation, eliminating the
per-plan-tool-call fseventsd notification that long unattended runs
were emitting. A real section change still rewrites the file and
advances ``updated_at``.

The tests drive the public entry points
(:func:`new_plan_draft`, :func:`save_plan_draft`,
:func:`load_plan_draft`) with an injected in-memory counting
``FileBackend`` (no real filesystem I/O, no ``tmp_path``, no
``time.sleep``) and a monotonically-incrementing ``now_iso`` stub,
so every post-condition is observed through the backend's recording
of ``write_text`` / ``replace`` invocations and the stubbed clock.

A ``_CountingBackend`` (defined here for test isolation) is
structurally shaped to match the public :class:`FileBackend` protocol
so a counting backend is substitutable wherever a real
:class:`PathFileBackend` would go. Every method is fully annotated
to the protocol's signature with concrete return types so the test
stays black-box and fully typed.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.plan._draft_io import (
    load_plan_draft,
    new_plan_draft,
    save_plan_draft,
)


class _CountingBackend(FileBackend):
    """In-memory FileBackend that records every write_text and replace call.

    Structurally implements the public :class:`FileBackend` protocol
    so a counting backend is substitutable wherever a real
    :class:`PathFileBackend` would go. Each method is annotated to
    the protocol signature with concrete return types.
    """

    def __init__(self) -> None:
        self._files: dict[Path, str] = {}
        self.write_text_calls: list[tuple[Path, str]] = []
        self.mkdir_calls: list[Path] = []
        self.replace_calls: list[tuple[Path, Path]] = []
        self.unlink_calls: list[Path] = []
        self.glob_calls: list[tuple[Path, str]] = []

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        self.mkdir_calls.append(path)
        _ = parents
        _ = exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        _ = encoding
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        _ = encoding
        self.write_text_calls.append((path, content))
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.replace_calls.append((source, destination))
        self._files[destination] = self._files[source]
        del self._files[source]

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        self.unlink_calls.append(path)
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        self.glob_calls.append((path, pattern))
        return []


class _Clock:
    """Monotonically increasing clock stub.

    Returns a distinct ISO-shaped string on every call so tests can
    assert that ``updated_at`` advances on real content changes
    without depending on ``datetime.now`` or ``time.sleep``.
    """

    def __init__(self) -> None:
        self._counter = 0

    def __call__(self) -> str:
        self._counter += 1
        return f"2026-01-01T00:00:0{self._counter}+00:00"


def test_first_save_writes(tmp_path: Path) -> None:
    backend = _CountingBackend()
    clock = _Clock()
    artifact_dir = tmp_path
    draft = new_plan_draft(now_iso=clock)
    draft["sections"] = {"summary": {"context": "first stage"}}

    save_plan_draft(artifact_dir, draft, backend=backend, now_iso=clock)

    assert len(backend.write_text_calls) == 1
    assert len(backend.replace_calls) == 1
    loaded = load_plan_draft(artifact_dir, backend=backend)
    assert loaded is not None
    sections = loaded["sections"]
    assert isinstance(sections, dict)
    assert "summary" in sections


def test_identical_restage_performs_no_write(tmp_path: Path) -> None:
    backend = _CountingBackend()
    clock = _Clock()
    artifact_dir = tmp_path
    draft = new_plan_draft(now_iso=clock)
    draft["sections"] = {"summary": {"context": "first stage"}}

    save_plan_draft(artifact_dir, draft, backend=backend, now_iso=clock)
    first_updated_at = load_plan_draft(artifact_dir, backend=backend)
    assert first_updated_at is not None
    first_stamp = first_updated_at["updated_at"]

    backend.write_text_calls.clear()
    backend.replace_calls.clear()
    backend.mkdir_calls.clear()

    save_plan_draft(artifact_dir, draft, backend=backend, now_iso=clock)

    assert backend.write_text_calls == []
    assert backend.replace_calls == []
    assert backend.mkdir_calls == []

    second_updated_at = load_plan_draft(artifact_dir, backend=backend)
    assert second_updated_at is not None
    assert second_updated_at["updated_at"] == first_stamp


def test_changed_sections_write_and_advance_updated_at(tmp_path: Path) -> None:
    backend = _CountingBackend()
    clock = _Clock()
    artifact_dir = tmp_path
    draft = new_plan_draft(now_iso=clock)
    draft["sections"] = {"summary": {"context": "first stage"}}

    save_plan_draft(artifact_dir, draft, backend=backend, now_iso=clock)
    first_updated_at = load_plan_draft(artifact_dir, backend=backend)
    assert first_updated_at is not None
    first_stamp = first_updated_at["updated_at"]

    backend.write_text_calls.clear()
    backend.replace_calls.clear()
    backend.mkdir_calls.clear()

    updated_draft = dict(draft)
    updated_sections = dict(draft["sections"])
    updated_sections["design"] = {"notes": "added design section"}
    updated_draft["sections"] = updated_sections

    save_plan_draft(artifact_dir, updated_draft, backend=backend, now_iso=clock)

    assert len(backend.write_text_calls) == 1
    assert len(backend.replace_calls) == 1

    loaded = load_plan_draft(artifact_dir, backend=backend)
    assert loaded is not None
    sections = loaded["sections"]
    assert isinstance(sections, dict)
    assert "design" in sections
    assert loaded["updated_at"] != first_stamp
