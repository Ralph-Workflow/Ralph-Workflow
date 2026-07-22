"""Regression tests for stable prompt writes that previously emitted redundant FSEvents."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.phases import review as review_module
from ralph.pipeline import cycle_baseline
from ralph.pipeline.conflict_resolution import prompt as conflict_prompt
from ralph.pipeline.parallel import worker_runtime
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable


class RecordingFileBackend(FileBackend):
    """In-memory file backend that counts physical text writes."""

    def __init__(self) -> None:
        self.files: dict[Path, str] = {}
        self.write_text_calls = 0

    def exists(self, path: Path) -> bool:
        return path in self.files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self.files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_text_calls += 1
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.files[destination] = self.files.pop(source)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.files.pop(path, None)
            return
        del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def _auto_integrate_prompt_writer() -> Callable[..., Path | None]:
    return cast(
        "Callable[..., Path | None]", conflict_prompt.render_conflict_prompt
    )


def _parallel_worker_prompt_writer() -> Callable[..., None]:
    return cast("Callable[..., None]", worker_runtime._write_worker_prompt)


def _persist_review_baseline() -> Callable[..., None]:
    return cast("Callable[..., None]", review_module._persist_review_baseline)


def _write_cycle_baseline() -> Callable[..., None]:
    return cast("Callable[..., None]", cycle_baseline.write_cycle_baseline)


def _render(
    write_prompt: Callable[..., Path | None],
    root: Path,
    target: str,
    conflicted: tuple[str, ...],
    backend: RecordingFileBackend,
) -> Path | None:
    return write_prompt(
        root=root,
        target=target,
        conflicted_paths=conflicted,
        round_index=1,
        round_cap=3,
        surviving_marker_paths=(),
        backend=backend,
    )


def test_write_prompt_regression_skips_byte_identical_rewrite() -> None:
    """Step 2: an unchanged auto-integrate prompt performs one physical write total."""
    backend = RecordingFileBackend()
    root = Path("/virtual-workspace")
    write_prompt = _auto_integrate_prompt_writer()

    first_path = _render(write_prompt, root, "main", ("shared.txt",), backend)
    second_path = _render(write_prompt, root, "main", ("shared.txt",), backend)

    expected_path = (
        root / ".agent" / "tmp" / "rebase_conflict_resolution_prompt.md"
    )
    assert first_path == expected_path
    assert second_path == expected_path
    assert backend.write_text_calls == 1
    assert "main" in backend.files[expected_path]
    assert "shared.txt" in backend.files[expected_path]


def test_write_prompt_regression_writes_on_changed_content() -> None:
    """Step 2: a changed auto-integrate target persists the newly rendered prompt."""
    backend = RecordingFileBackend()
    root = Path("/virtual-workspace")
    write_prompt = _auto_integrate_prompt_writer()

    prompt_path = _render(write_prompt, root, "main", ("shared.txt",), backend)
    changed_path = _render(write_prompt, root, "release", ("shared.txt",), backend)

    assert prompt_path is not None
    assert changed_path == prompt_path
    assert backend.write_text_calls == 2
    assert "release" in backend.files[prompt_path]

    # A changed conflicted-path list is changed content too, so it must
    # persist rather than be skipped as a byte-identical rewrite.
    _render(write_prompt, root, "release", ("other.txt",), backend)
    assert backend.write_text_calls == 3
    assert "other.txt" in backend.files[prompt_path]


def test_parallel_worker_prompt_regression_skips_byte_identical_rewrite() -> None:
    """Step 2: an unchanged worker prompt performs one physical write total."""
    backend = RecordingFileBackend()
    prompt_path = Path("/virtual-workspace/.agent/workers/unit-a/tmp/development_prompt.md")
    write_prompt = _parallel_worker_prompt_writer()

    write_prompt(prompt_path, "worker prompt", backend=backend)
    write_prompt(prompt_path, "worker prompt", backend=backend)

    assert backend.write_text_calls == 1
    assert backend.files[prompt_path] == "worker prompt"


def test_parallel_worker_prompt_regression_writes_on_changed_content() -> None:
    """Step 2: changed worker prompt content is persisted through the backend seam."""
    backend = RecordingFileBackend()
    prompt_path = Path("/virtual-workspace/.agent/workers/unit-a/tmp/development_prompt.md")
    write_prompt = _parallel_worker_prompt_writer()

    write_prompt(prompt_path, "first prompt", backend=backend)
    write_prompt(prompt_path, "changed prompt", backend=backend)

    assert backend.write_text_calls == 2
    assert backend.files[prompt_path] == "changed prompt"


def test_cycle_baseline_regression_skips_byte_identical_rewrite() -> None:
    """Step 1: a force=True rewrite of the same SHA performs one physical write total."""
    backend = RecordingFileBackend()
    root = Path("/virtual-workspace")
    write_baseline = _write_cycle_baseline()

    write_baseline(root, "sha-x", force=True, backend=backend)
    write_baseline(root, "sha-x", force=True, backend=backend)

    expected_path = root / ".agent" / "start_commit"
    assert backend.write_text_calls == 1
    assert backend.files[expected_path] == "sha-x\n"


def test_cycle_baseline_regression_writes_on_changed_content() -> None:
    """Step 1: a force=True rewrite of a different SHA performs a second physical write."""
    backend = RecordingFileBackend()
    root = Path("/virtual-workspace")
    write_baseline = _write_cycle_baseline()

    write_baseline(root, "sha-first", force=True, backend=backend)
    write_baseline(root, "sha-second", force=True, backend=backend)

    expected_path = root / ".agent" / "start_commit"
    assert backend.write_text_calls == 2
    assert backend.files[expected_path] == "sha-second\n"


def test_review_baseline_regression_skips_byte_identical_rewrite() -> None:
    """Step 2: a review baseline rewrite of the same SHA performs one physical write."""
    backend = RecordingFileBackend()
    marker_path = Path("/virtual-workspace/.agent/tmp/last_reviewed_sha.txt")
    persist = _persist_review_baseline()

    persist(marker_path, "deadbeef", backend=backend)
    persist(marker_path, "deadbeef", backend=backend)

    assert backend.write_text_calls == 1
    assert backend.files[marker_path] == "deadbeef"


def test_review_baseline_regression_writes_on_changed_content() -> None:
    """Step 2: a review baseline rewrite with a different SHA performs a second write."""
    backend = RecordingFileBackend()
    marker_path = Path("/virtual-workspace/.agent/tmp/last_reviewed_sha.txt")
    persist = _persist_review_baseline()

    persist(marker_path, "aaaa", backend=backend)
    persist(marker_path, "bbbb", backend=backend)

    assert backend.write_text_calls == 2
    assert backend.files[marker_path] == "bbbb"


def test_fsworkspace_write_skips_byte_identical_rewrite(tmp_path: Path) -> None:
    """Step 4: identical workspace content writes once and changed content writes again."""
    backend = RecordingFileBackend()
    workspace = FsWorkspace(tmp_path, backend=backend)
    expected_path = (tmp_path / "file.txt").resolve()

    workspace.write("file.txt", "same")
    workspace.write("file.txt", "same")

    assert backend.write_text_calls == 1
    assert backend.files[expected_path] == "same"

    workspace.write("file.txt", "changed")

    assert backend.write_text_calls == 2
    assert backend.files[expected_path] == "changed"
