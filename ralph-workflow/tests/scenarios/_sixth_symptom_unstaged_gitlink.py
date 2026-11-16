from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.git.rebase.rebase import rebase_in_progress
from tests._scenario_git import commit_file, git

pytestmark = pytest.mark.subprocess_e2e

_GITLINK_PATH: str = "vendor/Ralph-Workflow"


def _gitlink_stage_shas(root: Path, path: str = _GITLINK_PATH) -> tuple[str, str, str]:
    entries: dict[int, str] = {}
    for line in git(root, "ls-files", "-u", "--", path).splitlines():
        metadata, _separator, _relative = line.partition("\t")
        fields = metadata.split()
        if len(fields) == 3:
            entries[int(fields[2])] = fields[1]
    s1, s2, s3 = entries.get(1, ""), entries.get(2, ""), entries.get(3, "")
    if not (s1 and s2 and s3):
        raise AssertionError(f"expected three gitlink stages for {path}, got {(s1, s2, s3)}")
    return (s1, s2, s3)


def paused_sixth_symptom_unstaged_gitlink_rebase(root: Path) -> tuple[str, str, str]:
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    commit_file(
        root,
        "docs/product-spec.md",
        "# Product Specification\n\n## Overview\nBase specification.\n",
        "base product spec",
    )
    commit_file(
        root,
        "docs/product/21-open-decisions.md",
        "# Open Decisions\n\n| ID | Title | Status |\n|---|---|---|\n| OD-01 | Base decision | Accepted |\n",
        "base decisions log",
    )
    base_oid = git(root, "rev-parse", "HEAD")
    feature_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "feature vendor link")
    target_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "target vendor link")

    git(root, "update-index", "--add", "--cacheinfo", f"160000,{base_oid},{_GITLINK_PATH}")
    git(root, "commit", "-q", "-m", "base vendor pointer")

    git(root, "checkout", "-q", "-b", "feature")
    (root / "docs/product-spec.md").write_text(
        "# Product Specification\n\n## Overview\nFeature work specification.\n\n## Scope\nNew feature.\n",
        encoding="utf-8",
    )
    (root / "docs/product/21-open-decisions.md").write_text(
        "# Open Decisions\n\n| ID | Title | Status |\n|---|---|---|\n| OD-01 | Base decision | Accepted |\n| OD-02 | Feature item | Proposed |\n",
        encoding="utf-8",
    )
    git(root, "add", "docs/product-spec.md", "docs/product/21-open-decisions.md")
    git(root, "update-index", "--cacheinfo", f"160000,{feature_oid},{_GITLINK_PATH}")
    git(root, "commit", "-q", "-m", "feature change with vendor update")

    git(root, "checkout", "-q", "main")
    (root / "docs/product-spec.md").write_text(
        "# Product Specification\n\n## Overview\nTarget branch specification.\n\n## Goals\nTarget goals.\n",
        encoding="utf-8",
    )
    (root / "docs/product/21-open-decisions.md").write_text(
        "# Open Decisions\n\n| ID | Title | Status |\n|---|---|---|\n| OD-01 | Base decision | Accepted |\n| OD-03 | Target item | Landed |\n",
        encoding="utf-8",
    )
    git(root, "add", "docs/product-spec.md", "docs/product/21-open-decisions.md")
    git(root, "update-index", "--cacheinfo", f"160000,{target_oid},{_GITLINK_PATH}")
    git(root, "commit", "-q", "-m", "target change with vendor update")

    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)

    index_info = (
        f"160000 {base_oid} 1\t{_GITLINK_PATH}\n"
        f"160000 {target_oid} 2\t{_GITLINK_PATH}\n"
        f"160000 {feature_oid} 3\t{_GITLINK_PATH}\n"
    )
    subprocess.run(
        ("git", "update-index", "--index-info"),
        input=index_info,
        cwd=root,
        text=True,
        check=True,
        capture_output=True,
    )

    (root / "docs/product-spec.md").write_text(
        "# Product Specification\n\n## Overview\nTarget branch specification.\n\n## Goals\nTarget goals.\n\n## Scope\nNew feature.\n",
        encoding="utf-8",
    )
    (root / "docs/product/21-open-decisions.md").write_text(
        "# Open Decisions\n\n| ID | Title | Status |\n|---|---|---|\n| OD-01 | Base decision | Accepted |\n| OD-03 | Target item | Landed |\n| OD-02 | Feature item | Proposed |\n",
        encoding="utf-8",
    )

    if not rebase_in_progress(root):
        raise AssertionError("fixture did not pause in a conflicted rebase")
    from ralph.pipeline.auto_integrate_record import adopt_active_rebase_record
    adopt_active_rebase_record(root, "main")
    return _gitlink_stage_shas(root, _GITLINK_PATH)
