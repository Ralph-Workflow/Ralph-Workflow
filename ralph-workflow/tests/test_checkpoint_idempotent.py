"""Black-box tests for ralph.pipeline.checkpoint.save idempotent-write guard.

Satisfies AC-04 and AC-05:

* AC-04: ``checkpoint.save`` skips the atomic temp-write+replace when
  the serialized ``PipelineState`` equals the existing checkpoint
  bytes, and otherwise performs the same atomic write;
  ``_normalize_recovery_state`` normalization, atomic semantics,
  stray-tmp failure cleanup, and the ``load()``/callers contract are
  preserved. ``save`` gains only an optional keyword-only ``backend``
  param.

* AC-05: new black-box tests use an in-memory FileBackend whose
  ``replace()`` MOVES stored source content to the destination
  (diverging from the reference no-op), prove each writer skips the
  write (and the replace) on identical content, writes/replaces on
  changed content, and leaves the correct final content. Tests use
  no ``tmp_path``, no real I/O, no ``time.sleep``, and are fully
  typed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from collections.abc import Dict


class _ReplacingCountingBackend(FileBackend):
    """In-memory FileBackend that records write_text and replace invocations.

    ``replace(source, destination)`` MOVES stored source content to the
    destination (``self._files[destination] = self._files.pop(source)``)
    so atomic-save assertions on destination content are reachable.
    """

    def __init__(self) -> None:
        self._files: Dict[Path, str] = {}
        self.write_text_calls: list[tuple[Path, str]] = []
        self.replace_calls: list[tuple[Path, Path]] = []
        self.mkdir_calls: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self.mkdir_calls.append(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_text_calls.append((path, content))
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.replace_calls.append((source, destination))
        self._files[destination] = self._files.pop(source)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def test_checkpoint_save_regression_writes_and_replaces_on_first_save() -> None:
    """AC-04 + AC-05: first ``checkpoint.save`` performs one tmp-write plus one replace returning True.

    Verifies that an absent destination triggers the full atomic cycle:
    exactly one ``write_text`` to the tmp path plus exactly one
    ``replace(tmp, dest)``. The stored destination equals the serialized
    state, the tmp path is absent, and the parent directory creation was
    routed through the injected backend rather than the real filesystem.
    """
    backend = _ReplacingCountingBackend()
    dest = Path("/virtual-ws/.agent/checkpoint.json")
    tmp = dest.with_suffix(".tmp")
    state = PipelineState(phase="planning")

    ckpt.save(state, dest, backend=backend)

    assert backend.write_text_calls == [(tmp, state.model_dump_json(indent=2))]
    assert backend.replace_calls == [(tmp, dest)]
    assert backend._files[dest] == state.model_dump_json(indent=2)
    assert tmp not in backend._files
    # Parent directory creation is routed through the injected backend, not
    # the real filesystem.
    assert dest.parent in backend.mkdir_calls


def test_checkpoint_save_regression_skips_replace_when_state_identical() -> None:
    """AC-04 + AC-05: second identical ``checkpoint.save`` performs zero additional writes and replaces.

    Verifies the skip half of AC-04: a repeated ``save`` with a
    ``PipelineState`` whose serialized JSON equals the destination bytes
    must not write the tmp path and must not call ``replace``. The
    stored destination remains the original bytes; the tmp path stays
    absent.
    """
    backend = _ReplacingCountingBackend()
    dest = Path("/virtual-ws/.agent/checkpoint.json")
    tmp = dest.with_suffix(".tmp")
    state = PipelineState(phase="planning")

    ckpt.save(state, dest, backend=backend)
    writes_after_first = len(backend.write_text_calls)
    replaces_after_first = len(backend.replace_calls)
    stored_after_first = backend._files[dest]

    ckpt.save(state, dest, backend=backend)

    assert len(backend.write_text_calls) == writes_after_first
    assert len(backend.replace_calls) == replaces_after_first
    assert backend._files[dest] == stored_after_first
    assert tmp not in backend._files


def test_checkpoint_save_regression_writes_and_replaces_when_state_changed() -> None:
    """AC-04 + AC-05: changed ``PipelineState`` re-fires one write_text plus one replace.

    Verifies the changed-content half of AC-04: a ``save`` whose
    serialized state differs from the destination bytes performs the
    full atomic tmp-write plus replace cycle and the destination ends
    up holding the new serialized state.
    """
    backend = _ReplacingCountingBackend()
    dest = Path("/virtual-ws/.agent/checkpoint.json")
    tmp = dest.with_suffix(".tmp")
    initial = PipelineState(phase="planning")
    changed = PipelineState(phase="development")

    ckpt.save(initial, dest, backend=backend)
    writes_after_first = len(backend.write_text_calls)
    replaces_after_first = len(backend.replace_calls)

    ckpt.save(changed, dest, backend=backend)

    assert len(backend.write_text_calls) == writes_after_first + 1
    assert len(backend.replace_calls) == replaces_after_first + 1
    assert backend._files[dest] == changed.model_dump_json(indent=2)
    assert tmp not in backend._files
