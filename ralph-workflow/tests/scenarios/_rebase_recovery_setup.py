from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.rebase.rebase import rebase_in_progress
from tests._scenario_git import commit_file, git

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig

pytestmark = pytest.mark.subprocess_e2e

__all__ = [
    "corrupted_config",
    "paused_declared_decisions_and_gitlink_rebase",
    "paused_multi_commit_empty_stop_rebase",
    "paused_multi_commit_first_stop_rebase",
    "paused_text_and_gitlink_rebase",
    "paused_text_rebase",
    "scenario_config",
    "supervisor_restart",
]


def paused_text_rebase(root: Path) -> None:
    commit_file(root, "shared.txt", "base\n", "scenario base")
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "shared.txt", "feature\n", "feature change")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target\n", "target change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("scenario fixture did not pause in a conflicted rebase")


def paused_multi_commit_first_stop_rebase(root: Path, commit_count: int = 3) -> None:
    commit_file(root, "shared.txt", "base\n", "scenario base")
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "shared.txt", "feature stop 1\n", "feature commit 1 (conflicts)")
    for i in range(2, commit_count + 1):
        commit_file(root, f"feat_{i}.txt", f"feature {i}\n", f"feature commit {i}")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target\n", "target change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("multi-commit fixture did not pause in a conflicted rebase")


def _gitlink_stage_shas(root: Path, path: str = "vendor/link") -> tuple[str, str, str]:
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


def paused_text_and_gitlink_rebase(root: Path) -> tuple[str, str, str]:
    base_oid = git(root, "rev-parse", "HEAD")
    feature_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "feature link")
    target_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "target link")
    commit_file(root, "shared.txt", "base\n", "mixed base text")
    commit_file(root, "second.txt", "base second\n", "mixed second base text")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{base_oid},vendor/link")
    git(root, "commit", "-q", "-m", "mixed base link")
    git(root, "checkout", "-q", "-b", "feature")
    (root / "shared.txt").write_text("feature\n", encoding="utf-8")
    (root / "second.txt").write_text("feature second\n", encoding="utf-8")
    git(root, "add", "shared.txt", "second.txt")
    git(root, "update-index", "--cacheinfo", f"160000,{feature_oid},vendor/link")
    git(root, "commit", "-q", "-m", "feature mixed change")
    git(root, "checkout", "-q", "main")
    (root / "shared.txt").write_text("target\n", encoding="utf-8")
    (root / "second.txt").write_text("target second\n", encoding="utf-8")
    git(root, "add", "shared.txt", "second.txt")
    git(root, "update-index", "--cacheinfo", f"160000,{target_oid},vendor/link")
    git(root, "commit", "-q", "-m", "target mixed change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    index_info = (
        f"160000 {base_oid} 1\tvendor/link\n"
        f"160000 {target_oid} 2\tvendor/link\n"
        f"160000 {feature_oid} 3\tvendor/link\n"
    )
    subprocess.run(
        ("git", "update-index", "--index-info"),
        input=index_info,
        cwd=root,
        text=True,
        check=True,
        capture_output=True,
    )
    if not rebase_in_progress(root):
        raise AssertionError("mixed scenario fixture did not pause in a conflicted rebase")
    return _gitlink_stage_shas(root)


def paused_declared_decisions_and_gitlink_rebase(root: Path) -> tuple[str, str, str]:
    base_oid = git(root, "rev-parse", "HEAD")
    feature_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "feature link")
    target_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "target link")
    commit_file(root, "deleted.txt", "base delete/modify\n", "declared base")
    commit_file(root, "docs/decision.md", "base decision\n", "decision base")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{base_oid},vendor/link")
    git(root, "commit", "-q", "-m", "declared gitlink base")
    git(root, "checkout", "-q", "-b", "feature")
    (root / "deleted.txt").write_text("feature keeps file\n", encoding="utf-8")
    (root / "docs/decision.md").write_text("feature decision\n", encoding="utf-8")
    git(root, "add", "deleted.txt", "docs/decision.md")
    git(root, "update-index", "--cacheinfo", f"160000,{feature_oid},vendor/link")
    git(root, "commit", "-q", "-m", "feature declared set")
    git(root, "checkout", "-q", "main")
    git(root, "rm", "-q", "deleted.txt")
    (root / "docs/decision.md").write_text("target decision\n", encoding="utf-8")
    git(root, "add", "docs/decision.md")
    git(root, "update-index", "--cacheinfo", f"160000,{target_oid},vendor/link")
    git(root, "commit", "-q", "-m", "target declared set")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    (root / "docs/decision.md").write_text("declared decision\n", encoding="utf-8")
    index_info = (
        f"160000 {base_oid} 1\tvendor/link\n"
        f"160000 {target_oid} 2\tvendor/link\n"
        f"160000 {feature_oid} 3\tvendor/link\n"
    )
    subprocess.run(
        ("git", "update-index", "--index-info"),
        input=index_info,
        cwd=root,
        text=True,
        check=True,
        capture_output=True,
    )
    if not rebase_in_progress(root):
        raise AssertionError("declared-decision fixture did not pause")
    return _gitlink_stage_shas(root)


def paused_multi_commit_empty_stop_rebase(root: Path) -> None:
    commit_file(root, "shared.txt", "base\n", "base")
    commit_file(root, "series.txt", "seed\n", "seed")
    git(root, "checkout", "-q", "-b", "feature")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target value\n", "target moves")
    commit_file(root, "target-only.txt", "target moved\n", "target extra")
    git(root, "checkout", "-q", "feature")
    commit_file(root, "shared.txt", "feature value\n", "feature stop that can resolve empty")
    (root / "series.txt").write_text("seed\nsecond\n", encoding="utf-8")
    git(root, "add", "series.txt")
    git(root, "commit", "-q", "-m", "feature second commit")
    (root / "series.txt").write_text("seed\nsecond\nthird\n", encoding="utf-8")
    git(root, "add", "series.txt")
    git(root, "commit", "-q", "-m", "feature third commit")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("multi-commit fixture did not pause in a conflicted rebase")


def scenario_config() -> UnifiedConfig:
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": False,
            },
            "conflict_resolution": {
                "max_rounds_per_stop": 2,
                "max_rebase_conflict_stops": 4,
            },
        }
    )


def corrupted_config(root: Path) -> Path:
    config = root / ".agent" / "ralph-workflow.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("[general\nauto_integrate_enabled = true\n", encoding="utf-8")
    return config


def supervisor_restart(root: Path) -> dict[str, object]:
    script = (
        "import json,pathlib,subprocess,sys; "
        "p=pathlib.Path(sys.argv[1]);sys.path.insert(0,sys.argv[2]); "
        "from ralph.pipeline.auto_integrate_planning import integrate_before_planning; "
        "from ralph.pipeline.rebase_state import RebaseState; "
        "from ralph.workspace.scope import WorkspaceScope; "
        "from tests.scenarios._rebase_recovery_setup import scenario_config; "
        "s=integrate_before_planning(scenario_config(),WorkspaceScope(p),RebaseState()); "
        "g=lambda *a: subprocess.run(('git',*a),cwd=p,text=True,capture_output=True,check=True).stdout.strip(); "
        "print(json.dumps({'head':g('rev-parse','HEAD'),'status':g('status','--porcelain'),"
        "'resumed':s is not None}))"
    )
    package_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        (sys.executable, "-I", "-c", script, str(root), str(package_root)),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
        env={"HOME": str(root / ".scenario-home"), "PATH": "/usr/bin:/bin"},
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise AssertionError("restart probe returned a non-object payload")
    return payload
