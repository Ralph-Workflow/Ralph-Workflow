from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate_record import read_record
from tests._scenario_harness import commit_file, git
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.required_auto_integrate_e2e, pytest.mark.timeout_seconds(12)]


def _remote_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True, "auto_integrate_target": "main", "auto_integrate_remote_enabled": True, "auto_integrate_remote": "origin"}})


def _feature(root: Path) -> str:
    commit_file(root, "base.txt", "base\n", "base")
    git(root, "checkout", "-q", "-b", "feature")
    return commit_file(root, "feature.txt", "feature\n", "feature")


def test_local_landing_survives_publication_failure(tmp_git_repo: Path) -> None:
    feature_sha = _feature(tmp_git_repo)
    git(tmp_git_repo, "remote", "add", "origin", str(tmp_git_repo / "unreachable"))
    run = drive_integration(tmp_git_repo, config=_remote_config())
    assert run.error is None, f"publication failure must be a lifecycle state, not a crash: {run.error}"
    assert_content_equivalence(tmp_git_repo, contract="local landing must remain intact when publication fails", feature=feature_sha, source_refs=("main",))
    assert run.state is not None and not run.state.recovery_record_retained, (
        "publication failure must not turn a verified local landing back into unfinished integration"
    )


def test_publication_success_with_cleanup_failure_keeps_remote_landing(tmp_git_repo: Path) -> None:
    feature_sha = _feature(tmp_git_repo)
    remote = tmp_git_repo.parent / "remote.git"
    git(tmp_git_repo.parent, "init", "-q", "--bare", str(remote))
    git(tmp_git_repo, "remote", "add", "origin", str(remote))
    record_dir = tmp_git_repo / ".agent"
    record_dir.mkdir(exist_ok=True)
    record_dir.chmod(0o500)
    try:
        run = drive_integration(tmp_git_repo, config=_remote_config())
    finally:
        record_dir.chmod(0o700)
    assert run.error is None, f"cleanup failure after publication must be recoverable: {run.error}"
    assert git(tmp_git_repo, "rev-parse", "origin/main", check=False), (
        "publication success must remain true even when cleanup fails"
    )
    assert_content_equivalence(tmp_git_repo, contract="cleanup failure must not undo publication", feature=feature_sha, source_refs=("main",))


def test_cleanup_crash_after_landing_is_finished_on_the_next_tick(tmp_git_repo: Path) -> None:
    feature_sha = _feature(tmp_git_repo)
    first = drive_integration(tmp_git_repo)
    target_after_landing = git(tmp_git_repo, "rev-parse", "main")
    second = drive_integration(tmp_git_repo)
    assert first.error is None and second.error is None, "cleanup restart must converge without a crash"
    assert git(tmp_git_repo, "rev-parse", "main") == target_after_landing, (
        "the next tick after a cleanup crash must treat an already-landed operation as finished"
    )
    assert read_record(tmp_git_repo) is None, "a landed operation must not retain a record that can retrigger it"
    assert_content_equivalence(tmp_git_repo, contract="cleanup recovery must preserve the landed content", feature=feature_sha, source_refs=(target_after_landing,))
