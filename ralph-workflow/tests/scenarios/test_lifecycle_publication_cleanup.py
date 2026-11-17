from __future__ import annotations

import functools
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from git import Repo

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate_record import read_record
from tests._scenario_harness import git
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(12),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "feature_topology_template_reuse": (
        "publication cases materialize a cached feature-ahead topology via hardlink copytree"
    ),
    "FakeClock_on_integrate_drives": (
        "publication integrate drives use FakeClock; landing and cleanup asserts unchanged"
    ),
    "GitPython_sha_and_remote_reads": (
        "tip and origin/main presence checks use GitPython instead of git subprocess"
    ),
}


def _link_git_objects_or_copy(src: str, dst: str) -> None:
    src_path = Path(src)
    parts = src_path.parts
    if ".git" in parts:
        git_index = parts.index(".git")
        rest = parts[git_index + 1 :]
        if rest and rest[0] == "objects" and len(rest) >= 2:
            try:
                os.link(src, dst)
            except OSError:
                pass
            else:
                return
    shutil.copy2(src, dst)


def _write_commit(repo: Repo, relative: str, content: str, message: str) -> str:
    root = Path(repo.working_tree_dir or ".")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relative])
    return repo.index.commit(message).hexsha


@functools.lru_cache(maxsize=1)
def _feature_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-pub-feature-"))
    readme = base / "README.md"
    readme.write_text("seed\n", encoding="utf-8")
    repo = Repo.init(base, initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("commit", "gpgsign", "false")
    repo.index.add(["README.md"])
    repo.index.commit("seed")
    _write_commit(repo, "base.txt", "base\n", "base")
    repo.git.checkout("-q", "-b", "feature")
    _write_commit(repo, "feature.txt", "feature\n", "feature")
    repo.close()
    return base


def _materialize_feature(dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _feature_template(),
        dest,
        copy_function=_link_git_objects_or_copy,
        ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
    )
    return dest


def _sha(root: Path, ref: str) -> str:
    repo = Repo(root)
    try:
        return repo.commit(ref).hexsha
    finally:
        repo.close()


def _remote_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": True,
                "auto_integrate_remote": "origin",
            }
        }
    )


def _drive(root: Path, *, config: UnifiedConfig | None = None) -> object:
    clock = FakeClock()
    return drive_integration(root, config=config, clock=clock)


def test_local_landing_survives_publication_failure(tmp_path: Path) -> None:
    root = _materialize_feature(tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    repo = Repo(root)
    try:
        repo.create_remote("origin", str(root / "unreachable"))
    finally:
        repo.close()
    run = _drive(root, config=_remote_config())
    assert run.error is None, (
        f"publication failure must be a lifecycle state, not a crash: {run.error}"
    )
    assert_content_equivalence(
        root,
        contract="local landing must remain intact when publication fails",
        feature=feature_sha,
        source_refs=("main",),
    )
    assert run.state is not None and not run.state.recovery_record_retained, (
        "publication failure must not turn a verified local landing back into unfinished integration"
    )


def test_publication_success_with_cleanup_failure_keeps_remote_landing(tmp_path: Path) -> None:
    root = _materialize_feature(tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    record_dir = root / ".agent"
    record_dir.mkdir(exist_ok=True)
    record_dir.chmod(0o500)
    try:
        run = _drive(root, config=_remote_config())
    finally:
        record_dir.chmod(0o700)
    assert run.error is None, f"cleanup failure after publication must be recoverable: {run.error}"
    assert git(root, "rev-parse", "origin/main", check=False), (
        "publication success must remain true even when cleanup fails"
    )
    assert_content_equivalence(
        root,
        contract="cleanup failure must not undo publication",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_cleanup_crash_after_landing_is_finished_on_the_next_tick(tmp_path: Path) -> None:
    root = _materialize_feature(tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    first = _drive(root)
    target_after_landing = _sha(root, "main")
    second = _drive(root)
    assert first.error is None and second.error is None, (
        "cleanup restart must converge without a crash"
    )
    assert _sha(root, "main") == target_after_landing, (
        "the next tick after a cleanup crash must treat an already-landed operation as finished"
    )
    assert read_record(root) is None, (
        "a landed operation must not retain a record that can retrigger it"
    )
    assert_content_equivalence(
        root,
        contract="cleanup recovery must preserve the landed content",
        feature=feature_sha,
        source_refs=(target_after_landing,),
    )
