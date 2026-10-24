"""Containment, proof levels and retry policy of git lock recovery (round-3 findings)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from ralph.git import lock_recovery as lr
from ralph.git._run_git_lock_recovery import git_subcommand, run_with_lock_recovery
from ralph.git.git_run_result import GitRunResult


def _git_dir(root: Path) -> Path:
    git_dir = root / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "objects").mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    return git_dir


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_a_known_lock_name_outside_a_git_dir_is_never_reapable(tmp_path: Path) -> None:
    victim = tmp_path / "docs" / "index.lock"
    victim.parent.mkdir()
    victim.write_text("not git's")
    _age(victim, 5000)
    err = Exception(f"fatal: Unable to create '{victim}': File exists.")
    assert not lr._lock_path_is_reapable(victim)
    assert lr._recover_stale_git_lock("x", err) is False
    assert victim.exists()


@pytest.mark.parametrize(
    "rel",
    [
        "ralph/agent-lease.lock",
        "ralph-auto-integrate.lock",
        "worktrees/w/ralph-auto-integrate.lock",
    ],
)
def test_ralph_owned_locks_are_never_reapable(tmp_path: Path, rel: str) -> None:
    lock = _git_dir(tmp_path) / rel
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("lease")
    assert not lr._lock_path_is_reapable(lock)


def test_relative_and_dotdot_lock_paths_are_refused(tmp_path: Path) -> None:
    git_dir = _git_dir(tmp_path)
    assert lr._parse_lock_path_from_error("Unable to create '.git/index.lock': File exists") is None
    assert (
        lr._parse_lock_path_from_error(
            "Unable to create '.git/index.lock': File exists", base_dir=tmp_path
        )
        == git_dir / "index.lock"
    )
    escaped = f"Unable to create '{git_dir}/../../elsewhere/index.lock': File exists"
    assert lr._parse_lock_path_from_error(escaped) is None


def test_a_symlinked_ref_directory_is_never_walked(tmp_path: Path) -> None:
    git_dir = _git_dir(tmp_path)
    outside = tmp_path / "victim"
    (outside / "d").mkdir(parents=True)
    for victim in (outside / "a.lock", outside / "d" / "b.lock"):
        victim.write_text("x")
        _age(victim, 5000)
    (git_dir / "logs").symlink_to(outside)
    assert lr.git_lock_candidates(git_dir) == []
    assert lr.reap_stale_locks(git_dir) == []
    assert (outside / "a.lock").exists()
    assert (outside / "d" / "b.lock").exists()


def test_reaping_a_ref_lock_leaves_nothing_in_the_ref_namespace(tmp_path: Path) -> None:
    git_dir = _git_dir(tmp_path)
    lock = git_dir / "refs" / "heads" / "feat.lock"
    lock.write_text("x")
    _age(lock, 5000)
    identity = lr._lock_identity(lock)
    assert lr._unlink_if_unchanged(lock, identity) is True
    assert list((git_dir / "refs").rglob("*")) == [git_dir / "refs" / "heads"]
    assert not list((git_dir / "ralph").glob("reap-*"))


def test_a_lock_replaced_after_the_probe_is_not_removed(tmp_path: Path) -> None:
    git_dir = _git_dir(tmp_path)
    lock = git_dir / "index.lock"
    lock.write_text("old")
    stale_identity = lr._lock_identity(lock)
    lock.unlink()
    lock.write_text("a live git's brand new lock")
    assert lr._unlink_if_unchanged(lock, stale_identity) is False
    assert lock.read_text() == "a live git's brand new lock"


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        (".git/logs/HEAD.lock", ".git"),
        (".git/modules/sub/index.lock", ".git"),
        (".git/worktrees/wt/index.lock", ".git/worktrees/wt"),
        (".git/refs/heads/x.lock", ".git"),
        ("modules/logs/.git/index.lock", "modules/logs/.git"),
    ],
)
def test_gitdir_for_lock_uses_the_enclosing_git_dir(
    tmp_path: Path, rel: str, expected: str
) -> None:
    assert lr._gitdir_for_lock(tmp_path / rel) == tmp_path / expected


def test_only_a_message_naming_a_lock_file_counts_as_contention() -> None:
    assert lr.names_a_lock_file("fatal: Unable to create '/r/.git/index.lock': File exists.")
    assert lr.names_a_lock_file("error: could not lock config file /r/.git/config: File exists")
    # A permanent ref-name conflict also says "cannot lock ref"; waiting never fixes it.
    assert not lr.names_a_lock_file(
        "error: cannot lock ref 'refs/heads/a/b': 'refs/heads/a' exists; cannot create it"
    )


def _fake_proc(root: Path, comm: str) -> str:
    pid_dir = root / "7"
    pid_dir.mkdir()
    (pid_dir / "comm").write_text(f"{comm}\n")
    return "7"


@pytest.mark.parametrize(
    ("comm", "age_s", "expected"),
    [
        ("python3", 100.0, "unknown"),
        ("python3", 5000.0, "dead"),
        ("systemd", 100.0, "dead"),
        ("sshd-session", 100.0, "dead"),
        ("node", 100.0, "unknown"),
    ],
)
def test_an_unreadable_process_is_ruled_out_only_by_sound_facts(
    tmp_path: Path, comm: str, age_s: float, expected: str
) -> None:
    pid = _fake_proc(tmp_path, comm)
    assert lr._rule_out_unreadable(tmp_path, pid, (1000, 1000), 1000, age_s) == expected


def test_a_foreign_uid_cannot_hold_the_lock(tmp_path: Path) -> None:
    pid = _fake_proc(tmp_path, "python3")
    assert lr._rule_out_unreadable(tmp_path, pid, (0, 0, 0, 0), 1000, 100.0) == "dead"


def test_git_subcommand_skips_global_options() -> None:
    assert git_subcommand(["-C", "/r", "-c", "a=b", "merge", "--no-edit"]) == "merge"
    assert git_subcommand(["--git-dir", "/r/.git", "update-ref", "x"]) == "update-ref"


# --- retry policy of the universal wrapper ---------------------------------

_LOCK_ERR = "fatal: Unable to create '{}': File exists."


class _Spawn:
    """Scripted ``_spawn_git``: returns the queued results in order."""

    def __init__(self, results: list[GitRunResult]) -> None:
        self.results = results
        self.calls = 0

    def __call__(
        self, args: object, *, cwd: Path | None, label: str, options: object = None
    ) -> GitRunResult:
        del args, cwd, label, options
        self.calls += 1
        return self.results.pop(0)


def _run(spawn: _Spawn, args: list[str], root: Path) -> GitRunResult:
    return run_with_lock_recovery(lambda call: call(), spawn, args, root, "t", None)


def _lock_failure(root: Path) -> GitRunResult:
    return GitRunResult(("git",), 128, "", _LOCK_ERR.format(root / ".git" / "index.lock"))


def test_a_stateful_command_is_not_re_run_and_reports_its_original_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_dir(tmp_path)
    spawn = _Spawn([_lock_failure(tmp_path), GitRunResult(("git",), 128, "", "MERGE_HEAD exists")])
    result = _run(spawn, ["merge", "--no-edit", "main"], tmp_path)
    assert spawn.calls == 1
    assert "Unable to create" in result.stderr


def test_a_repeatable_command_is_re_run_once_the_lock_is_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_dir(tmp_path)
    monkeypatch.setattr(
        "ralph.git._run_git_lock_recovery.retry_after_lock_failure", lambda *a, **k: True
    )
    spawn = _Spawn([_lock_failure(tmp_path), GitRunResult(("git",), 0, "ok", "")])
    assert _run(spawn, ["add", "-A"], tmp_path).returncode == 0
    assert spawn.calls == 2


def test_a_failed_re_run_reports_the_first_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_dir(tmp_path)
    monkeypatch.setattr(
        "ralph.git._run_git_lock_recovery.retry_after_lock_failure", lambda *a, **k: True
    )
    spawn = _Spawn([_lock_failure(tmp_path), GitRunResult(("git",), 1, "", "something else")])
    assert "Unable to create" in _run(spawn, ["reset", "--hard"], tmp_path).stderr


def test_check_true_failures_are_recovered_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_dir(tmp_path)
    monkeypatch.setattr(
        "ralph.git._run_git_lock_recovery.retry_after_lock_failure", lambda *a, **k: True
    )
    calls = {"n": 0}

    def spawn(args: object, *, cwd: object, label: str, options: object = None) -> GitRunResult:
        del args, cwd, label, options
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.CalledProcessError(
                128, ["git"], stderr=_LOCK_ERR.format(tmp_path / ".git" / "index.lock")
            )
        return GitRunResult(("git",), 0, "ok", "")

    result = run_with_lock_recovery(lambda call: call(), spawn, ["add", "-A"], tmp_path, "t", None)
    assert result.returncode == 0
    assert calls["n"] == 2
