"""Concurrency proofs for the shared agents TOML writer.

The canonical plan requires every agents-file write to stage its payload in a
UNIQUE temporary file ("unique temp (two writers, no shared name)"). These
black-box proofs drive two writers at the same time and assert that the
staging names never collide and that no scratch file survives the race.

The proofs observe the filesystem, not the writer's internals: every atomic
move of a staged file into place is recorded, so the staging names are read
back from the moves the writer actually performed.
"""

from __future__ import annotations

import os
import threading
import tomllib
from pathlib import Path

import pytest

from ralph.config.agents_file_writer import write_agents_toml

_AGENTS_FILENAME = "ralph-workflow-agents.toml"


def _record_atomic_moves(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record every atomic move performed while the patch is active.

    ``os.replace`` / ``os.rename`` are the only POSIX calls that move a
    staged file onto its final name, so recording them observes the
    staging names the writer really used without reaching into the
    writer's internals.
    """
    moves: list[tuple[str, str]] = []
    lock = threading.Lock()
    real_replace = os.replace
    real_rename = os.rename

    def recording_replace(
        src: str | bytes | os.PathLike[str],
        dst: str | bytes | os.PathLike[str],
    ) -> None:
        with lock:
            moves.append((os.fsdecode(src), os.fsdecode(dst)))
        real_replace(src, dst)

    def recording_rename(
        src: str | bytes | os.PathLike[str],
        dst: str | bytes | os.PathLike[str],
    ) -> None:
        with lock:
            moves.append((os.fsdecode(src), os.fsdecode(dst)))
        real_rename(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)
    monkeypatch.setattr(os, "rename", recording_rename)
    return moves


def _same_path(left: str | Path, right: str | Path) -> bool:
    """Return True when both paths name the same file."""
    left_path = Path(left)
    right_path = Path(right)
    if left_path == right_path:
        return True
    return left_path.resolve() == right_path.resolve()


def _run_concurrent_writers(
    target: Path, texts: list[str]
) -> tuple[list[bool], list[BaseException]]:
    """Write ``texts`` to ``target`` from concurrent threads."""
    start = threading.Barrier(len(texts))
    written: list[bool] = []
    failures: list[BaseException] = []
    guard = threading.Lock()

    def writer(text: str) -> None:
        try:
            start.wait(timeout=10)
            result = write_agents_toml(target, text, trigger="concurrency-proof")
        except BaseException as exc:
            with guard:
                failures.append(exc)
            return
        with guard:
            written.append(result)

    threads = [threading.Thread(target=writer, args=(text,)) for text in texts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return written, failures


def test_concurrent_writers_to_same_target_use_distinct_temp_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two writers racing on one agents file stage into DIFFERENT temp names."""
    target = tmp_path / _AGENTS_FILENAME
    target.write_text('[agents.claude]\ncmd = "claude-v0"\n', encoding="utf-8")
    moves = _record_atomic_moves(monkeypatch)

    texts = [
        '[agents.claude]\ncmd = "claude-a"\n',
        '[agents.claude]\ncmd = "claude-b"\n',
    ]
    written, failures = _run_concurrent_writers(target, texts)

    assert not failures, f"concurrent writers raised: {failures}"
    assert len(written) == 2

    staged = [(src, dst) for src, dst in moves if _same_path(dst, target)]
    assert len(staged) == 2, f"expected two staged writes, saw: {moves}"

    staged_names = {Path(src).name for src, _ in staged}
    assert len(staged_names) == 2, f"the two writers shared a temp name: {staged_names}"
    assert all(Path(src).parent == target.parent for src, _ in staged)


def test_concurrent_writers_leave_no_stray_temp_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither racing writer leaves a scratch file next to the agents file."""
    target = tmp_path / _AGENTS_FILENAME
    target.write_text('[agents.claude]\ncmd = "claude-v0"\n', encoding="utf-8")
    _record_atomic_moves(monkeypatch)

    texts = [
        '[agents.claude]\ncmd = "claude-a"\n',
        '[agents.claude]\ncmd = "claude-b"\n',
    ]
    written, failures = _run_concurrent_writers(target, texts)

    assert not failures, f"concurrent writers raised: {failures}"
    assert len(written) == 2

    leftovers = {
        entry.name
        for entry in tmp_path.iterdir()
        if entry.name != target.name and not entry.name.endswith(".bak")
    }
    assert leftovers == set(), f"stray files left behind: {sorted(leftovers)}"

    final_text = target.read_text(encoding="utf-8")
    assert final_text in set(texts)
    assert "agents" in tomllib.loads(final_text)


def test_concurrent_writers_in_one_directory_use_distinct_temp_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two writers sharing a directory never share a staged file name."""
    first = tmp_path / "first-agents.toml"
    second = tmp_path / "second-agents.toml"
    moves = _record_atomic_moves(monkeypatch)

    start = threading.Barrier(2)
    failures: list[BaseException] = []
    guard = threading.Lock()

    def writer(target: Path, text: str) -> None:
        try:
            start.wait(timeout=10)
            write_agents_toml(target, text, trigger="concurrency-proof")
        except BaseException as exc:
            with guard:
                failures.append(exc)

    threads = [
        threading.Thread(target=writer, args=(first, '[agents.claude]\ncmd = "a"\n')),
        threading.Thread(target=writer, args=(second, '[agents.claude]\ncmd = "b"\n')),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not failures, f"concurrent writers raised: {failures}"

    staged = [(src, dst) for src, dst in moves if _same_path(dst, first) or _same_path(dst, second)]
    assert len(staged) == 2, f"expected two staged writes, saw: {moves}"

    staged_names = {Path(src).name for src, _ in staged}
    assert len(staged_names) == 2, f"the two writers shared a temp name: {staged_names}"
    assert all(Path(src).parent == tmp_path for src, _ in staged)

    leftovers = {
        entry.name
        for entry in tmp_path.iterdir()
        if entry.name not in {first.name, second.name} and not entry.name.endswith(".bak")
    }
    assert leftovers == set(), f"stray files left behind: {sorted(leftovers)}"


def test_failed_writer_keeps_a_concurrent_writers_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing writer must never destroy another writer's recovery copy.

    The race is deterministic: writer A publishes its backup and is then
    held at the target replace, writer B completes a successful write in
    that window, and only then does A fail. The final file must be B's
    valid TOML and the backup must still hold the recoverable predecessor
    bytes -- the failing writer may not delete a backup it does not own.
    """
    target = tmp_path / _AGENTS_FILENAME
    original = '[agents.claude]\nmodel = "operator"\n'
    target.write_text(original, encoding="utf-8")
    reached_replace = threading.Event()
    release_a = threading.Event()
    real_replace = Path.replace

    def blocking_replace(self: Path, target_path: object, *args: object, **kwargs: object) -> Path:
        # Only the TARGET replace is held: writer A has already published its
        # backup by then, which is the state the race needs.
        if threading.current_thread().name == "writer-a" and str(target_path) == str(target):
            reached_replace.set()
            assert release_a.wait(timeout=10), "writer A was never released"
            raise OSError("simulated replace failure")
        real_replace(self, target_path, *args, **kwargs)
        return self

    monkeypatch.setattr(Path, "replace", blocking_replace)

    outcome: dict[str, object] = {}

    def run_writer_a() -> None:
        try:
            write_agents_toml(target, '[agents.claude]\nmodel = "writer-a"\n', trigger="writer-a")
        except OSError as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run_writer_a, name="writer-a")
    thread.start()
    assert reached_replace.wait(timeout=10), "writer A never reached the target replace"

    write_agents_toml(target, '[agents.claude]\nmodel = "writer-b"\n', trigger="writer-b")
    backup = target.with_name(f"{target.name}.bak")
    writer_b_backup_identity = (backup.stat().st_dev, backup.stat().st_ino)

    release_a.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), OSError)

    # The successful writer owns the file.
    assert target.read_text(encoding="utf-8") == '[agents.claude]\nmodel = "writer-b"\n'
    tomllib.loads(target.read_text(encoding="utf-8"))

    # The failed writer left the concurrent writer's recovery copy alone.
    assert backup.read_text(encoding="utf-8") == original
    assert (backup.stat().st_dev, backup.stat().st_ino) == writer_b_backup_identity

    # No scratch file survives the race.
    assert sorted(entry.name for entry in tmp_path.iterdir()) == sorted([target.name, backup.name])


def test_failed_writer_interleaved_at_backup_publication_preserves_concurrent_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed writer does not delete a backup published immediately after its own backup replace.

    Even if a concurrent writer B publishes a backup to ``bak_path`` immediately
    after writer A's backup replace (before writer A executes target replacement),
    writer A's ownership token captured before the replace ensures writer A
    never identifies writer B's backup as its own and leaves writer B's backup
    untouched when writer A fails.
    """
    target = tmp_path / _AGENTS_FILENAME
    original = '[agents.claude]\nmodel = "operator"\n'
    target.write_text(original, encoding="utf-8")
    bak_path = target.with_name(f"{target.name}.bak")

    writer_b_backup_identity: tuple[int, int] | None = None
    real_replace = Path.replace

    def interleaving_replace(
        self: Path, target_path: object, *args: object, **kwargs: object
    ) -> Path:
        nonlocal writer_b_backup_identity
        if threading.current_thread().name == "writer-a" and str(target_path) == str(target):
            raise OSError("simulated target replace failure")
        result = real_replace(self, target_path, *args, **kwargs)
        if threading.current_thread().name == "writer-a" and str(target_path) == str(bak_path):
            # Writer A just replaced bak_path. Run Writer B to completion on a dedicated thread.
            writer_b_thread = threading.Thread(
                target=lambda: write_agents_toml(
                    target, '[agents.claude]\nmodel = "writer-b"\n', trigger="writer-b"
                ),
                name="writer-b",
            )
            writer_b_thread.start()
            writer_b_thread.join(timeout=5)
            assert not writer_b_thread.is_alive(), "writer B failed to finish"
            writer_b_backup_identity = (bak_path.stat().st_dev, bak_path.stat().st_ino)
        return result

    monkeypatch.setattr(Path, "replace", interleaving_replace)

    outcome: dict[str, object] = {}

    def run_writer_a() -> None:
        try:
            write_agents_toml(target, '[agents.claude]\nmodel = "writer-a"\n', trigger="writer-a")
        except OSError as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run_writer_a, name="writer-a")
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), OSError)

    # Writer B succeeded and owns the file.
    assert target.read_text(encoding="utf-8") == '[agents.claude]\nmodel = "writer-b"\n'
    tomllib.loads(target.read_text(encoding="utf-8"))

    # Writer B's backup is intact and was not deleted by writer A's failure.
    assert bak_path.is_file()
    assert bak_path.read_text(encoding="utf-8") == original
    assert writer_b_backup_identity is not None
    assert (bak_path.stat().st_dev, bak_path.stat().st_ino) == writer_b_backup_identity

    # No scratch file survives the race.
    assert sorted(entry.name for entry in tmp_path.iterdir()) == sorted(
        [target.name, bak_path.name]
    )
