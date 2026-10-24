"""Killing a tree that contains git must give git time to drop its lock files."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ralph.process.manager import SpawnOptions, get_process_manager
from ralph.process.manager._git_termination import git_aware_grace, is_git_pid
from ralph.timeout_defaults import GIT_TERMINATE_GRACE_SECONDS

pytestmark = [
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc/<pid>/comm"),
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(30),
]

#: A stand-in git: takes the lock, and on SIGTERM needs half a second to clean up
#: before releasing it -- exactly the window a zero-grace SIGKILL steals.
_FAKE_GIT = textwrap.dedent(
    """\
    #!{python}
    import os, signal, sys, time
    lock = sys.argv[1]
    open(lock, "w").close()
    def on_term(signum, frame):
        time.sleep(0.5)
        os.unlink(lock)
        os._exit(143)
    signal.signal(signal.SIGTERM, on_term)
    print("ready", flush=True)
    time.sleep(60)
    """
)


def _fake_git(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "git"
    script.write_text(_FAKE_GIT.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.mark.parametrize("grace_period_s", [0.0, 0.1])
def test_terminate_with_a_tiny_grace_still_lets_git_remove_its_lock(
    tmp_path: Path, grace_period_s: float
) -> None:
    lock = tmp_path / "index.lock"
    proc = get_process_manager().spawn(
        [str(_fake_git(tmp_path)), str(lock)],
        SpawnOptions(stdout=subprocess.PIPE),
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == b"ready"
    assert is_git_pid(proc.pid)

    started = time.monotonic()
    proc.terminate(grace_period_s=grace_period_s)

    assert not lock.exists(), "git was SIGKILLed before it could release its lock"
    assert time.monotonic() - started < GIT_TERMINATE_GRACE_SECONDS


def test_non_git_processes_keep_the_callers_grace(tmp_path: Path) -> None:
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        assert not is_git_pid(sleeper.pid)
        assert git_aware_grace(0.0, [sleeper.pid]) == 0.0
        assert git_aware_grace(0.0, [os.getpid()]) == 0.0
    finally:
        sleeper.kill()
        sleeper.wait()


def test_git_in_the_tree_raises_a_short_grace_to_the_cleanup_window(tmp_path: Path) -> None:
    lock = tmp_path / "index.lock"
    proc = get_process_manager().spawn(
        [str(_fake_git(tmp_path)), str(lock)], SpawnOptions(stdout=subprocess.PIPE)
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == b"ready"
        assert git_aware_grace(0.0, [proc.pid]) == GIT_TERMINATE_GRACE_SECONDS
        assert git_aware_grace(GIT_TERMINATE_GRACE_SECONDS + 1, [proc.pid]) == (
            GIT_TERMINATE_GRACE_SECONDS + 1
        )
    finally:
        proc.terminate(grace_period_s=2.0)


_STUBBORN_GIT = textwrap.dedent(
    """\
    #!{python}
    import signal, sys, time
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("ready", flush=True)
    time.sleep(60)
    """
)


def test_shutdown_all_gives_several_stuck_gits_one_shared_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N trees that ignore SIGTERM cost one cleanup window in total, not N."""
    import ralph.process.manager._git_termination as git_termination
    import ralph.process.manager._process_manager as process_manager

    window_s = 1.0
    for module in (git_termination, process_manager):
        monkeypatch.setattr(module, "GIT_TERMINATE_GRACE_SECONDS", window_s)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "git"
    script.write_text(_STUBBORN_GIT.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    manager = get_process_manager()
    procs = [manager.spawn([str(script)], SpawnOptions(stdout=subprocess.PIPE)) for _ in range(4)]
    for proc in procs:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == b"ready"

    started = time.monotonic()
    manager.shutdown_all(grace_period_s=0.0)

    # Sequentially this is 4 windows (>= 4s); together it is one plus slack.
    assert time.monotonic() - started < 3 * window_s


def test_git_children_get_sigterm_as_their_parent_death_signal() -> None:
    from ralph.process.manager import death_preexec

    assert death_preexec(["/usr/bin/git", "add"]).args == (15,)
    assert death_preexec(["/usr/bin/sleep", "1"]).args == (9,)


def test_look_alikes_of_git_do_not_trigger_the_floor() -> None:
    from ralph.process.manager._git_termination import is_git_name

    assert is_git_name("git")
    assert is_git_name("git-remote-https")
    assert not is_git_name("gitlab-runner")
    assert not is_git_name("gitstatusd")
