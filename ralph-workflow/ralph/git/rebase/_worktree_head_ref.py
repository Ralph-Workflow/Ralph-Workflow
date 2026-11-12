from __future__ import annotations

_REF_PREFIX = "ref:"


def head_file_targets_branch(content: str, branch: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return False
    if stripped.startswith(_REF_PREFIX):
        stripped = stripped[len(_REF_PREFIX) :].strip()
    return stripped == f"refs/heads/{branch}"
