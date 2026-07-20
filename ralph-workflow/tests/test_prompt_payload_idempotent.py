"""Black-box tests for idempotent oversized prompt-payload writes.

Verifies the per-phase payload writer honors the shared
``write_text_if_changed`` idempotent guard so a byte-identical re-emit
of an oversized prompt payload does not advance the file's mtime or
generate an additional fseventsd notification.

The tests drive the public :func:`write_payload_to_directory` and
:func:`phase_payload_variables` entry points with an injected
in-memory counting ``FileBackend`` (no real filesystem I/O, no
``tmp_path``, no ``time.sleep``), so the post-condition "file contains
``sanitize_surrogates(content)``" is observed through the backend's
recording of ``write_text`` invocations.

A ``_CountingBackend`` (defined here for test isolation) is structurally
shaped to match the public :class:`FileBackend` protocol: every method
is fully annotated to the protocol's signature with concrete return
types and lives entirely in-process.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.prompts.materialize_support import phase_payload_variables
from ralph.prompts.payload_refs import (
    MAX_INLINE_PROMPT_BYTES,
    write_payload_to_directory,
)


class _CountingBackend(FileBackend):
    """In-memory FileBackend that records every write_text call.

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
        # No filesystem state to mutate; the in-memory dict already
        # serves as the single source of truth for path->content.
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


def test_write_payload_to_directory_writes_first_content() -> None:
    backend = _CountingBackend()
    output_dir = Path("/virtual-ws/.agent/tmp/prompt_payloads")
    relative = ".agent/tmp/prompt_payloads/development_plan.txt"
    destination = output_dir / Path(relative).name
    content = "first payload body"

    result = write_payload_to_directory(output_dir, relative, content, backend=backend)

    assert result == str(destination)
    assert len(backend.write_text_calls) == 1
    assert backend.write_text_calls[0] == (destination, content)
    assert backend._files[destination] == content
    assert backend.mkdir_calls == [destination.parent]


def test_write_payload_to_directory_skips_identical_content() -> None:
    backend = _CountingBackend()
    output_dir = Path("/virtual-ws/.agent/tmp/prompt_payloads")
    relative = ".agent/tmp/prompt_payloads/development_plan.txt"
    destination = output_dir / Path(relative).name
    content = "first payload body"

    first = write_payload_to_directory(output_dir, relative, content, backend=backend)
    second = write_payload_to_directory(output_dir, relative, content, backend=backend)

    assert first == second
    assert second == str(destination)
    assert len(backend.write_text_calls) == 1
    assert backend._files[destination] == content


def test_write_payload_to_directory_writes_changed_content() -> None:
    backend = _CountingBackend()
    output_dir = Path("/virtual-ws/.agent/tmp/prompt_payloads")
    relative = ".agent/tmp/prompt_payloads/development_plan.txt"
    destination = output_dir / Path(relative).name

    write_payload_to_directory(output_dir, relative, "AAA", backend=backend)
    write_payload_to_directory(output_dir, relative, "BBB", backend=backend)

    assert len(backend.write_text_calls) == 2
    assert backend.write_text_calls[0] == (destination, "AAA")
    assert backend.write_text_calls[1] == (destination, "BBB")
    assert backend._files[destination] == "BBB"


def test_phase_payload_variables_skips_identical_oversized_payload() -> None:
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")
    # Any single value above MAX_INLINE_PROMPT_BYTES forces the
    # file-based payload path; a deterministic all-"x" body keeps
    # the test fast and trivially byte-identical between calls.
    oversized = "x" * (MAX_INLINE_PROMPT_BYTES + 1)
    values = {"PLAN": oversized}

    first = phase_payload_variables(
        phase="development",
        workspace_root=workspace_root,
        values=values,
        backend=backend,
    )
    second = phase_payload_variables(
        phase="development",
        workspace_root=workspace_root,
        values=values,
        backend=backend,
    )

    assert len(backend.write_text_calls) == 1
    assert first["PLAN_PATH"] == second["PLAN_PATH"]
    assert second["PLAN_PATH"] != ""
    assert first["PLAN"] == ""
    assert second["PLAN"] == ""
