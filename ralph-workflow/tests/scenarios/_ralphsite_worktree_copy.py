from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ralph.git.rebase.rebase import rebase_in_progress
from tests._scenario_git import commit_file, git

pytestmark = pytest.mark.subprocess_e2e

RALPHSITE_VENDOR_PATH: str = "vendor/Ralph-Workflow"
RALPHSITE_STAGE_1_SHA: str = "27c3d76f5e59dcf73e5abe2fc08eb98ad3fe78a8"
RALPHSITE_STAGE_2_SHA: str = "8a716a24281b4d8d5848ae4df04360d4017692fd"
RALPHSITE_STAGE_3_SHA: str = "eca87af7138ba0835c5ada2adc6d85d0c43b5c04"
RALPHSITE_GITLINK_STAGES: tuple[str, str, str] = (
    RALPHSITE_STAGE_1_SHA,
    RALPHSITE_STAGE_2_SHA,
    RALPHSITE_STAGE_3_SHA,
)


def _gitlink_stage_shas(root: Path, path: str = RALPHSITE_VENDOR_PATH) -> tuple[str, str, str]:
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


def seed_ralphsite_ledger(root: Path, variant: str = "wt-02") -> Path:
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = agent_dir / "integration_attempt_ledger.json"

    if variant in ("wt-02", "wt-02-host-management"):
        payload = {
            "schema_version": 1,
            "rung": "rebuild_on_target",
            "repeat_counts": [
                [["equal|unmerged|rebase|consistent|detached", "dispatch_resolver"], 2],
                [["ahead|unmerged|rebase|consistent|detached", "dispatch_resolver"], 2],
                [["behind|clean|none|stale|attached", "rebuild_on_target"], 1],
                [["ahead|clean|none|none|attached", "land"], 1],
            ],
            "unknown_attempts": 0,
            "unknown_budget": 3,
            "evidence": {
                "ladder:rebuild_on_target": "succeeded",
                "ladder:land": "intent",
            },
            "shared_rung_results": {},
        }
    else:
        payload = {
            "schema_version": 1,
            "rung": "continue_with_integration_pending",
            "repeat_counts": [
                [["diverged|clean|none|stale|attached", "repair"], 2],
                [["ahead|clean|none|none|attached", "land"], 3],
                [["equal|unmerged|rebase|consistent|detached", "dispatch_resolver"], 2],
                [["ahead|unmerged|rebase|consistent|detached", "dispatch_resolver"], 2],
                [["equal|tracked_changes|none|none|attached", "save_and_restore"], 1],
            ],
            "unknown_attempts": 0,
            "unknown_budget": 3,
            "evidence": {
                "rung:save_and_restore": "failed:not-owned-here",
                "salvage:fresh_worktree_rebuild": "succeeded",
                "preserved:fresh_worktree": str(root.parent / "fresh-worktree-205a6c9d"),
                "salvage:resolver_agent": (
                    "failed:operation completion proof failed: "
                    "HEAD 86a7f5e7440b5f70e0c4244d935eecb115d40b4d does not match "
                    "integrated_feature_sha, pre_target_sha, or pre_feature_sha"
                ),
            },
            "shared_rung_results": {},
        }

    ledger_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return ledger_path


def setup_ralphsite_wt02_worktree(root: Path, *, seed_ledger: bool = True) -> tuple[str, str, str]:
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    commit_file(
        root,
        "docs/product-spec.md",
        "# Product Specification\n\n## Overview\nRalph-Site host management specification.\n",
        "spec: initial host management specification",
    )
    commit_file(
        root,
        "docs/product/21-open-decisions.md",
        "# Open Decisions\n\n| ID | Topic | Status |\n|---|---|---|\n| OD-01 | Host lifecycle model | Accepted |\n",
        "docs: seed open decisions",
    )
    commit_file(
        root,
        "spec/requests/machine_surface_cross_account_negative_spec.rb",
        "RSpec.describe 'Machine surface cross account negative' do\n  it 'denies foreign access' do\n    expect(true).to be(true)\n  end\nend\n",
        "spec: add machine surface cross account negative test",
    )
    base_oid = git(root, "rev-parse", "HEAD")
    feature_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "feature pointer")
    target_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "target pointer")

    git(root, "update-index", "--add", "--cacheinfo", f"160000,{base_oid},{RALPHSITE_VENDOR_PATH}")
    git(root, "commit", "-q", "-m", "vendor: point to base Ralph-Workflow")

    git(root, "checkout", "-q", "-b", "wt-02-host-management")
    (root / "docs/product-spec.md").write_text(
        "# Product Specification\n\n## Overview\nRalph-Site host management specification.\n\n## Host API\nEndpoint additions.\n",
        encoding="utf-8",
    )
    (root / "docs/product/21-open-decisions.md").write_text(
        "# Open Decisions\n\n| ID | Topic | Status |\n|---|---|---|\n| OD-01 | Host lifecycle model | Accepted |\n| OD-02 | Cross-account boundary | Proposed |\n",
        encoding="utf-8",
    )
    (root / "spec/requests/machine_surface_cross_account_negative_spec.rb").write_text(
        "RSpec.describe 'Machine surface cross account negative' do\n  it 'denies foreign access' do\n    expect(true).to be(true)\n  end\n  it 'audits rejection' do\n    expect(true).to be(true)\n  end\nend\n",
        encoding="utf-8",
    )
    git(
        root,
        "add",
        "docs/product-spec.md",
        "docs/product/21-open-decisions.md",
        "spec/requests/machine_surface_cross_account_negative_spec.rb",
    )
    git(root, "update-index", "--cacheinfo", f"160000,{feature_oid},{RALPHSITE_VENDOR_PATH}")
    git(root, "commit", "-q", "-m", "feat(hosts): expand host management lifecycle")

    git(root, "checkout", "-q", "main")
    (root / "docs/product-spec.md").write_text(
        "# Product Specification\n\n## Overview\nRalph-Site host management specification.\n\n## Global Policy\nCore account security.\n",
        encoding="utf-8",
    )
    (root / "docs/product/21-open-decisions.md").write_text(
        "# Open Decisions\n\n| ID | Topic | Status |\n|---|---|---|\n| OD-01 | Host lifecycle model | Accepted |\n| OD-03 | Quota governance | Landed |\n",
        encoding="utf-8",
    )
    (root / "spec/requests/machine_surface_cross_account_negative_spec.rb").write_text(
        "RSpec.describe 'Machine surface cross account negative' do\n  it 'denies foreign access' do\n    expect(true).to be(true)\n  end\n  it 'enforces rate limit' do\n    expect(true).to be(true)\n  end\nend\n",
        encoding="utf-8",
    )
    git(
        root,
        "add",
        "docs/product-spec.md",
        "docs/product/21-open-decisions.md",
        "spec/requests/machine_surface_cross_account_negative_spec.rb",
    )
    git(root, "update-index", "--cacheinfo", f"160000,{target_oid},{RALPHSITE_VENDOR_PATH}")
    git(root, "commit", "-q", "-m", "feat(security): update policy and vendor pointer")

    git(root, "checkout", "-q", "wt-02-host-management")
    git(root, "rebase", "main", check=False)

    index_info = (
        f"160000 {RALPHSITE_STAGE_1_SHA} 1\t{RALPHSITE_VENDOR_PATH}\n"
        f"160000 {RALPHSITE_STAGE_2_SHA} 2\t{RALPHSITE_VENDOR_PATH}\n"
        f"160000 {RALPHSITE_STAGE_3_SHA} 3\t{RALPHSITE_VENDOR_PATH}\n"
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
        "# Product Specification\n\n## Overview\nRalph-Site host management specification.\n\n## Global Policy\nCore account security.\n\n## Host API\nEndpoint additions.\n",
        encoding="utf-8",
    )
    (root / "docs/product/21-open-decisions.md").write_text(
        "# Open Decisions\n\n| ID | Topic | Status |\n|---|---|---|\n| OD-01 | Host lifecycle model | Accepted |\n| OD-03 | Quota governance | Landed |\n| OD-02 | Cross-account boundary | Proposed |\n",
        encoding="utf-8",
    )
    (root / "spec/requests/machine_surface_cross_account_negative_spec.rb").write_text(
        "RSpec.describe 'Machine surface cross account negative' do\n  it 'denies foreign access' do\n    expect(true).to be(true)\n  end\n  it 'enforces rate limit' do\n    expect(true).to be(true)\n  end\n  it 'audits rejection' do\n    expect(true).to be(true)\n  end\nend\n",
        encoding="utf-8",
    )

    if seed_ledger:
        seed_ralphsite_ledger(root, variant="wt-02")

    if not rebase_in_progress(root):
        raise AssertionError("wt-02 fixture did not pause in a conflicted rebase")
    from ralph.pipeline._stop_resolution_entry import adopt_active_rebase_record
    adopt_active_rebase_record(root, "main")
    return _gitlink_stage_shas(root, RALPHSITE_VENDOR_PATH)


def setup_ralphsite_wt04_worktree(root: Path, *, seed_ledger: bool = True) -> tuple[str, str, str]:
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    commit_file(
        root,
        "docs/product-spec.md",
        "# Product Specification\n\n## Overview\nRalph-Site run management specification.\n",
        "spec: initial run management specification",
    )
    commit_file(
        root,
        "docs/product/21-open-decisions.md",
        "# Open Decisions\n\n| ID | Topic | Status |\n|---|---|---|\n| OD-01 | Run lifecycle model | Accepted |\n",
        "docs: seed open decisions",
    )
    base_oid = git(root, "rev-parse", "HEAD")
    feature_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "feature pointer")
    target_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "target pointer")

    git(root, "update-index", "--add", "--cacheinfo", f"160000,{base_oid},{RALPHSITE_VENDOR_PATH}")
    git(root, "commit", "-q", "-m", "vendor: point to base Ralph-Workflow")

    git(root, "checkout", "-q", "-b", "wt-04-run-management")
    (root / "docs/product-spec.md").write_text(
        "# Product Specification\n\n## Overview\nRalph-Site run management specification.\n\n## Run API\nDispatch additions.\n",
        encoding="utf-8",
    )
    (root / "docs/product/21-open-decisions.md").write_text(
        "# Open Decisions\n\n| ID | Topic | Status |\n|---|---|---|\n| OD-01 | Run lifecycle model | Accepted |\n| OD-04 | Dispatch arbitration | Proposed |\n",
        encoding="utf-8",
    )
    git(root, "add", "docs/product-spec.md", "docs/product/21-open-decisions.md")
    git(root, "update-index", "--cacheinfo", f"160000,{feature_oid},{RALPHSITE_VENDOR_PATH}")
    git(root, "commit", "-q", "-m", "feat(runs): expand run management lifecycle")

    git(root, "checkout", "-q", "main")
    (root / "docs/product-spec.md").write_text(
        "# Product Specification\n\n## Overview\nRalph-Site run management specification.\n\n## Core Engine\nExecution coordinator.\n",
        encoding="utf-8",
    )
    (root / "docs/product/21-open-decisions.md").write_text(
        "# Open Decisions\n\n| ID | Topic | Status |\n|---|---|---|\n| OD-01 | Run lifecycle model | Accepted |\n| OD-05 | Heartbeat monitoring | Landed |\n",
        encoding="utf-8",
    )
    git(root, "add", "docs/product-spec.md", "docs/product/21-open-decisions.md")
    git(root, "update-index", "--cacheinfo", f"160000,{target_oid},{RALPHSITE_VENDOR_PATH}")
    git(root, "commit", "-q", "-m", "feat(engine): update execution coordinator and vendor pointer")

    git(root, "checkout", "-q", "wt-04-run-management")
    git(root, "rebase", "main", check=False)

    index_info = (
        f"160000 {RALPHSITE_STAGE_1_SHA} 1\t{RALPHSITE_VENDOR_PATH}\n"
        f"160000 {RALPHSITE_STAGE_2_SHA} 2\t{RALPHSITE_VENDOR_PATH}\n"
        f"160000 {RALPHSITE_STAGE_3_SHA} 3\t{RALPHSITE_VENDOR_PATH}\n"
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
        "# Product Specification\n\n## Overview\nRalph-Site run management specification.\n\n## Core Engine\nExecution coordinator.\n\n## Run API\nDispatch additions.\n",
        encoding="utf-8",
    )
    (root / "docs/product/21-open-decisions.md").write_text(
        "# Open Decisions\n\n| ID | Topic | Status |\n|---|---|---|\n| OD-01 | Run lifecycle model | Accepted |\n| OD-05 | Heartbeat monitoring | Landed |\n| OD-04 | Dispatch arbitration | Proposed |\n",
        encoding="utf-8",
    )

    if seed_ledger:
        seed_ralphsite_ledger(root, variant="wt-04")

    if not rebase_in_progress(root):
        raise AssertionError("wt-04 fixture did not pause in a conflicted rebase")
    from ralph.pipeline._stop_resolution_entry import adopt_active_rebase_record
    adopt_active_rebase_record(root, "main")
    return _gitlink_stage_shas(root, RALPHSITE_VENDOR_PATH)


def reconstruct_ralphsite_worktree(root: Path, variant: str = "wt-02") -> tuple[str, str, str]:
    if variant in ("wt-04", "wt-04-run-management"):
        return setup_ralphsite_wt04_worktree(root)
    return setup_ralphsite_wt02_worktree(root)
