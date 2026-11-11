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
