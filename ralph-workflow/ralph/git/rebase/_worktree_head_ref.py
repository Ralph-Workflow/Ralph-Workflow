"""Exact HEAD-ref matching for the worktree-conflict precondition.

``git`` refuses to rebase a branch that is checked out in a second
worktree, so :func:`ralph.git.rebase.rebase_preconditions._check_worktree_conflicts`
inspects every sibling worktree's ``HEAD`` file. That check used to ask
whether ``"refs/heads/<branch>"`` appeared ANYWHERE in the file, which is
a prefix test rather than an identity test: a sibling worktree on
``wt-040-fix-autorebase`` matched the branch ``wt-040``. In a fleet of
linked worktrees with a shared name prefix -- this repository's own
``wt-0NN-*`` topology -- that raised a false ``RebasePreconditionError``,
which auto-integration records as a "preconditions not met" skip, so the
rebase silently never ran for the whole run.

The comparison must therefore be exact: a worktree conflicts only when
its HEAD resolves to precisely ``refs/heads/<branch>``.
"""

from __future__ import annotations

_REF_PREFIX = "ref:"


def head_file_targets_branch(content: str, branch: str) -> bool:
    """Whether a worktree HEAD file's content checks out ``branch``.

    Args:
        content: Raw text of a worktree's ``HEAD`` file.
        branch: Short branch name, without the ``refs/heads/`` prefix.

    Returns:
        ``True`` only when the HEAD file names exactly this branch. A
        detached HEAD (a bare SHA), an empty file and a branch whose name
        merely shares a prefix all return ``False``.
    """
    stripped = content.strip()
    if not stripped:
        return False
    if stripped.startswith(_REF_PREFIX):
        stripped = stripped[len(_REF_PREFIX) :].strip()
    return stripped == f"refs/heads/{branch}"
