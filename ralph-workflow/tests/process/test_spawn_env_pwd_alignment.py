"""A spawned child's ``$PWD`` must name the directory it was actually started in.

``cwd=`` changes the child's working directory but leaves the inherited
``PWD`` untouched, and a surprising number of tools trust ``PWD`` over
``getcwd()``. OpenCode is one: its ``run`` handler resolves the project root
as ``resolve(process.env.PWD ?? process.cwd())``, so a stale ``PWD`` makes it
read and write a different tree than Ralph believes it is driving -- silently,
and only when Ralph is launched from outside the workspace (a linked worktree,
``--workspace`` pointing elsewhere, a wrapper script). Ralph already aligns
``PWD`` for the exec tool (``ralph/mcp/tools/exec.py``); the agent spawn path
did not.
"""

from __future__ import annotations

from ralph.process._spawn_env import child_env_for_spawn


def test_spawn_env_regression_pwd_names_the_child_working_directory() -> None:
    """A stale inherited ``PWD`` is replaced by the directory the child gets."""
    child = child_env_for_spawn({"PWD": "/somewhere/else", "HOME": "/home/x"}, cwd="/work/repo")

    assert child is not None
    assert child["PWD"] == "/work/repo"
    assert child["HOME"] == "/home/x"


def test_spawn_env_regression_oldpwd_is_dropped_when_pwd_is_realigned() -> None:
    """``OLDPWD`` describes a shell history the child never had."""
    child = child_env_for_spawn({"PWD": "/a", "OLDPWD": "/b"}, cwd="/work/repo")

    assert child is not None
    assert "OLDPWD" not in child


def test_spawn_env_leaves_pwd_alone_when_the_child_inherits_the_directory() -> None:
    """Without a ``cwd`` the child really does start where the parent is."""
    child = child_env_for_spawn({"PWD": "/a"}, cwd=None)

    assert child is not None
    assert child["PWD"] == "/a"
