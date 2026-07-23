"""Deterministic stand-in for the diagnostics git probe.

``SystemInfo.gather`` shells out to ``git --version``, ``git rev-parse``,
``git branch --show-current`` and ``git status --porcelain``. Against a
real working tree that costs real wall clock which scales with the size
of the repository — on a cold filesystem cache a single ``git status``
over this repository dominates the per-test budget, and the diagnostics
tests called it once per test.

Tests that assert on how ``gather`` composes its fields inject
:func:`stub_git_probe` instead of forking git. Real-git behavior belongs
in the subprocess E2E suite, not deterministic unit tests.
"""

from __future__ import annotations

#: Branch name reported by :func:`stub_git_probe`.
STUB_GIT_BRANCH = "stub-branch"
#: ``git --version`` output reported by :func:`stub_git_probe`.
STUB_GIT_VERSION = "git version 2.99.0"


def stub_git_probe(args: list[str]) -> str | None:
    """Answer a diagnostics git query without spawning a subprocess.

    Args:
        args: Git arguments, without the leading ``git``, exactly as
            ``SystemInfo.gather`` passes them.

    Returns:
        The canned stdout for a recognised query, otherwise ``None``
        (the same signal the production probe uses for a failed command).
    """
    if args == ["--version"]:
        return STUB_GIT_VERSION
    if args == ["rev-parse", "--is-inside-work-tree"]:
        return "true"
    if args == ["branch", "--show-current"]:
        return STUB_GIT_BRANCH
    if args == ["status", "--porcelain"]:
        return ""
    return None
