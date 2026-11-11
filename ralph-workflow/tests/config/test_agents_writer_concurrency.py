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

import contextlib
import os
import threading
import tomllib
from collections.abc import Callable
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

    def blocking_replace(
        self: Path, target_path: str | os.PathLike[str], *args: object, **kwargs: object
    ) -> Path:
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
    writer A's failure handling does not mutate or unlink ``bak_path``, leaving
    writer B's backup untouched when writer A fails.
    """
    target = tmp_path / _AGENTS_FILENAME
    original = '[agents.claude]\nmodel = "operator"\n'
    target.write_text(original, encoding="utf-8")
    bak_path = target.with_name(f"{target.name}.bak")

    writer_b_backup_identity: tuple[int, int] | None = None
    real_replace = Path.replace

    def interleaving_replace(
        self: Path, target_path: str | os.PathLike[str], *args: object, **kwargs: object
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


def _historical_file_identity(path: Path) -> tuple[int, int] | None:
    """Ownership token helper matching pre-0c6c519 implementation."""
    try:
        status = path.stat()
    except OSError:
        return None
    if status.st_ino == 0:
        return None
    return (status.st_dev, status.st_ino)


def _historical_release_backup(
    bak_path: Path,
    previous_backup: bytes | None,
    published_identity: tuple[int, int] | None,
    *,
    interleave_cleanup_race: Callable[[], None] | None = None,
) -> None:
    """Reconstruct pre-0c6c519 rollback logic to demonstrate historical vulnerability.

    Before commit 0c6c519, a failed write attempted to restore or delete its backup
    after checking the file identity token. The window between checking the
    identity token and executing the mutation (unlink or restore write) was a race
    window: if concurrent Writer B published its canonical backup inside this window,
    the failing Writer A proceeded to unlink or rewrite Writer B's backup.
    """
    if published_identity is None:
        return
    if _historical_file_identity(bak_path) != published_identity:
        return
    # The historical cleanup race window opens here: identity check has succeeded,
    # but cleanup mutation has not yet executed.
    if interleave_cleanup_race is not None:
        interleave_cleanup_race()
    with contextlib.suppress(OSError):
        if previous_backup is None:
            bak_path.unlink(missing_ok=True)
        else:
            bak_path.write_bytes(previous_backup)


def _write_versions_as_writer_b(target: Path, bak_path: Path) -> tuple[bytes, tuple[int, int]]:
    """Execute Writer B's two sequential writes and return backup bytes and identity."""
    write_agents_toml(target, '[agents.claude]\nmodel = "writer-b-v1"\n', trigger="writer-b-v1")
    write_agents_toml(target, '[agents.claude]\nmodel = "writer-b-v2"\n', trigger="writer-b-v2")
    return bak_path.read_bytes(), (bak_path.stat().st_dev, bak_path.stat().st_ino)


def _assert_historical_rollback_vulnerability(tmp_path: Path) -> None:
    """Prove that historical rollback deletes or rewrites Writer B's backup in the race window."""
    # Case 1A: previous_backup was None (initial write).
    # Historical cleanup unlinked bak_path, deleting Writer B's backup.
    hist_dir_a = tmp_path / "hist_a"
    hist_dir_a.mkdir()
    target_hist_a = hist_dir_a / _AGENTS_FILENAME
    target_hist_a.write_text('[agents.claude]\nmodel = "v0"\n', encoding="utf-8")
    bak_hist_a = target_hist_a.with_name(f"{target_hist_a.name}.bak")

    # Writer A publishes backup for v0
    write_agents_toml(target_hist_a, '[agents.claude]\nmodel = "writer-a"\n', trigger="prep_a")
    published_id_a = _historical_file_identity(bak_hist_a)
    assert published_id_a is not None

    # Interleave Writer B between identity check and cleanup mutation
    _historical_release_backup(
        bak_hist_a,
        previous_backup=None,
        published_identity=published_id_a,
        interleave_cleanup_race=lambda: _write_versions_as_writer_b(target_hist_a, bak_hist_a),
    )
    # Historical rollback unlinked bak_hist_a: Writer B's backup was destroyed (deleted).
    assert not bak_hist_a.exists(), "historical rollback should have deleted Writer B's backup"

    # Case 1B: previous_backup was present.
    # Historical cleanup rewrote bak_path with stale data, clobbering Writer B's backup.
    hist_dir_b = tmp_path / "hist_b"
    hist_dir_b.mkdir()
    target_hist_b = hist_dir_b / _AGENTS_FILENAME
    target_hist_b.write_text('[agents.claude]\nmodel = "v0"\n', encoding="utf-8")
    bak_hist_b = target_hist_b.with_name(f"{target_hist_b.name}.bak")

    # Writer A publishes backup for v0
    write_agents_toml(target_hist_b, '[agents.claude]\nmodel = "writer-a"\n', trigger="prep_b")
    published_id_b = _historical_file_identity(bak_hist_b)
    assert published_id_b is not None
    stale_previous_backup = b'[agents.claude]\nmodel = "stale-backup"\n'

    _historical_release_backup(
        bak_hist_b,
        previous_backup=stale_previous_backup,
        published_identity=published_id_b,
        interleave_cleanup_race=lambda: _write_versions_as_writer_b(target_hist_b, bak_hist_b),
    )
    # Historical rollback overwrote bak_hist_b with stale bytes, clobbering Writer B's backup.
    assert bak_hist_b.read_bytes() == stale_previous_backup
    assert b"writer-b-v1" not in bak_hist_b.read_bytes()


def _run_writer_a_failure_with_concurrent_writer_b(
    target: Path,
    bak_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, tuple[int, int], list[str]]:
    """Drive Writer A to failure while Writer B publishes concurrent updates."""
    writer_b_backup_bytes: bytes | None = None
    writer_b_backup_identity: tuple[int, int] | None = None
    writer_a_mutations_on_bak: list[str] = []
    writer_a_published_backup = threading.Event()
    real_replace = Path.replace
    real_unlink = Path.unlink
    real_write_bytes = Path.write_bytes

    def tracking_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if threading.current_thread().name == "writer-a" and _same_path(self, bak_path):
            writer_a_mutations_on_bak.append("unlink")
        real_unlink(self, *args, **kwargs)

    def tracking_write_bytes(self: Path, *args: object, **kwargs: object) -> int:
        if threading.current_thread().name == "writer-a" and _same_path(self, bak_path):
            writer_a_mutations_on_bak.append("write_bytes")
        return real_write_bytes(self, *args, **kwargs)

    def interleaving_replace(
        self: Path, target_path: str | os.PathLike[str], *args: object, **kwargs: object
    ) -> Path:
        nonlocal writer_b_backup_bytes, writer_b_backup_identity
        if threading.current_thread().name == "writer-a":
            if _same_path(target_path, bak_path):
                if writer_a_published_backup.is_set():
                    writer_a_mutations_on_bak.append("replace")
                else:
                    writer_a_published_backup.set()
            elif _same_path(target_path, target):

                def run_writer_b() -> None:
                    nonlocal writer_b_backup_bytes, writer_b_backup_identity
                    writer_b_backup_bytes, writer_b_backup_identity = _write_versions_as_writer_b(
                        target, bak_path
                    )

                thread_b = threading.Thread(target=run_writer_b, name="writer-b")
                thread_b.start()
                thread_b.join(timeout=5)
                assert not thread_b.is_alive(), "writer B failed to complete"
                raise OSError("simulated target replace failure for writer A")

        return real_replace(self, target_path, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", interleaving_replace)
    monkeypatch.setattr(Path, "unlink", tracking_unlink)
    monkeypatch.setattr(Path, "write_bytes", tracking_write_bytes)

    outcome: dict[str, object] = {}

    def run_writer_a() -> None:
        try:
            write_agents_toml(target, '[agents.claude]\nmodel = "writer-a"\n', trigger="writer-a")
        except OSError as exc:
            outcome["error"] = exc

    thread_a = threading.Thread(target=run_writer_a, name="writer-a")
    thread_a.start()
    thread_a.join(timeout=5)
    assert not thread_a.is_alive()
    assert isinstance(outcome.get("error"), OSError)
    assert writer_a_published_backup.is_set()
    assert writer_b_backup_bytes is not None
    assert writer_b_backup_identity is not None
    return writer_b_backup_bytes, writer_b_backup_identity, writer_a_mutations_on_bak


def test_replacement_failure_interleave_preserves_concurrent_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Writer A fails during target replacement, Writer B's backup is preserved.

    Deterministic proof against the historical cleanup race window (between identity
    check and cleanup mutation) that a failed writer never deletes or rewrites a
    concurrent Writer B's canonical backup.

    Historical vulnerability proof:
    Under pre-0c6c519 rollback logic, a failing writer verified that the canonical
    backup matched its published identity token before unlinking or restoring it:
      T1: Writer A verifies identity token matches its own backup.
      T2: [Historical Race Window] Concurrent Writer B publishes its new backup.
      T3: Writer A executes cleanup mutation (unlink or write_bytes), destroying
          Writer B's backup (deleted when previous_backup is None, or overwritten
          with stale data when previous_backup is present).
    This test executes this exact race against a minimal reconstruction of the prior
    behavior and confirms the destructive deletion and overwrite.

    Current implementation proof:
    Under current ``write_agents_toml`` (since commit 0c6c519), backups published
    before replacement are retained as safe recovery data on failure, and failure
    cleanup only removes writer-specific temporary files. The canonical backup is
    never mutated or unlinked on failure. When Writer A fails and Writer B publishes
    its new backup concurrently during Writer A's replacement failure and cleanup
    handling, Writer B's backup is preserved intact:
      - Valid TOML structure
      - Backup bytes match Writer B's backup
      - Backup inode/identity matches Writer B's published backup
      - Target bytes match Writer B's successful write
      - Zero scratch files survive
      - Zero mutations on the canonical backup by Writer A during failure cleanup.
    """
    _assert_historical_rollback_vulnerability(tmp_path)

    live_dir = tmp_path / "live"
    live_dir.mkdir()
    target = live_dir / _AGENTS_FILENAME
    target.write_text('[agents.claude]\nmodel = "v0"\n', encoding="utf-8")
    bak_path = target.with_name(f"{target.name}.bak")

    b_bytes, b_identity, a_mutations = _run_writer_a_failure_with_concurrent_writer_b(
        target, bak_path, monkeypatch
    )

    # RALPH-TEST-REASON: Writer A's failure cleanup removes only its writer-specific
    # temporary file and never deletes or overwrites Writer B's backup.
    assert a_mutations == [], f"Writer A mutated or deleted bak_path on failure: {a_mutations}"

    # Backup assertions: valid TOML, bytes intact, identity intact
    assert bak_path.is_file()
    assert bak_path.read_bytes() == b_bytes
    assert tomllib.loads(bak_path.read_text(encoding="utf-8")) == {
        "agents": {"claude": {"model": "writer-b-v1"}}
    }
    assert (bak_path.stat().st_dev, bak_path.stat().st_ino) == b_identity

    # Target assertions: Writer B succeeded and owns the target file
    expected_target_text = '[agents.claude]\nmodel = "writer-b-v2"\n'
    assert target.read_text(encoding="utf-8") == expected_target_text
    assert tomllib.loads(target.read_text(encoding="utf-8")) == {
        "agents": {"claude": {"model": "writer-b-v2"}}
    }

    # Only target and backup remain; no scratch temp file survived.
    assert sorted(entry.name for entry in live_dir.iterdir()) == sorted(
        [target.name, bak_path.name]
    )
