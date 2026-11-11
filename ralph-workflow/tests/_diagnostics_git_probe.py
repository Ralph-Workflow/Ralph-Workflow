from __future__ import annotations

STUB_GIT_BRANCH = "stub-branch"
STUB_GIT_VERSION = "git version 2.99.0"


def stub_git_probe(args: list[str]) -> str | None:
    if args == ["--version"]:
        return STUB_GIT_VERSION
    if args == ["rev-parse", "--is-inside-work-tree"]:
        return "true"
    if args == ["branch", "--show-current"]:
        return STUB_GIT_BRANCH
    if args == ["status", "--porcelain"]:
        return ""
    return None
