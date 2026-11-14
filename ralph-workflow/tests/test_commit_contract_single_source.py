from __future__ import annotations

import os
from pathlib import Path

from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_ARTIFACT, COMMIT_MESSAGE_TYPE
from ralph.phases.required_artifacts import resolve_required_artifact
from ralph.policy.loader import default_dir, load_policy

_COMMIT_DRAIN = "development_commit"


def test_policy_and_writer_constant_agree_on_commit_path() -> None:
    bundle = load_policy(default_dir())
    ra = resolve_required_artifact(bundle.artifacts, drain=_COMMIT_DRAIN)
    assert ra is not None, f"no artifact contract for drain {_COMMIT_DRAIN!r}"
    assert ra.artifact_type == COMMIT_MESSAGE_TYPE
    assert ra.artifact_path == COMMIT_MESSAGE_ARTIFACT


def test_repo_wide_guard_no_lingering_commit_cleanup_identifiers() -> None:
    pkg_root = Path(__file__).resolve().parent.parent
    this_file = Path(__file__).resolve()
    target_token = "commit_" + "cleanup"
    violations: list[str] = []
    ignored_dirs = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".hypothesis",
        ".ruff_cache",
        "_build",
        "dist",
        "build",
    }
    for root, dirs, files in os.walk(pkg_root):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for f in files:
            if f.endswith((".jsonl", ".pyc", ".png", ".jpg", ".jpeg", ".db")):
                continue
            file_path = Path(root) / f
            if file_path == this_file:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if target_token in content:
                violations.append(str(file_path.relative_to(pkg_root)))

    assert not violations, f"{target_token} identifier found in active files: {violations}"
