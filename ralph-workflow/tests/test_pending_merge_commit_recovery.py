from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.subprocess_runner import GitRunOptions, GitRunResult, run_git
from ralph.pipeline._pending_merge_commit import prepare_pending_merge
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(
        args,
        cwd=root,
        label="pending-recovery-test",
        options=GitRunOptions(timeout=5),
    )


def _commit(root: Path, path: str, content: str) -> str:
    (root / path).write_text(content, encoding="utf-8")
    assert _git(root, "add", path).returncode == 0
    assert _git(root, "commit", "-m", path).returncode == 0
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _prepared(root: Path) -> tuple[str, IntegrationRecord]:
    target = _git(root, "branch", "--show-current").stdout.strip()
    _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    _commit(root, "shared.txt", "feature\n")
    assert _git(root, "switch", target).returncode == 0
    _commit(root, "shared.txt", "target\n")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "merge", "--no-edit", target).returncode != 0
    (root / "shared.txt").write_text("resolved\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    assert prepare_pending_merge(root, target) is None
    record = read_record(root)
    assert record is not None
    return target, record


@pytest.mark.parametrize("change", ["index", "head", "merge_parent", "query_failure"])
def test_pending_merge_refuses_changed_or_unreadable_evidence(
    tmp_git_repo: Path,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_git_repo
    target, record = _prepared(root)
    if change == "index":
        (root / "shared.txt").write_text("new staged work\n", encoding="utf-8")
        assert _git(root, "add", "shared.txt").returncode == 0
    elif change == "head":
        assert _git(root, "update-ref", "HEAD", record.merge_commit_parent or "").returncode == 0
    elif change == "merge_parent":
        (root / ".git" / "MERGE_HEAD").write_text(
            f"{record.merge_commit_head}\n",
            encoding="utf-8",
        )
    else:
        monkeypatch.setattr("ralph.pipeline._pending_merge_commit._git_value", lambda *_a: None)
    head = _git(root, "rev-parse", "HEAD").stdout
    staged = _git(root, "write-tree").stdout
    parent = (root / ".git" / "MERGE_HEAD").read_text(encoding="utf-8")
    target_before = _git(root, "rev-parse", target).stdout
    outcome = recover_incomplete_integration(WorkspaceScope(root))
    assert outcome is not None and outcome.recovery_record_retained
    assert read_record(root) is not None
    assert _git(root, "rev-parse", "HEAD").stdout == head
    assert _git(root, "write-tree").stdout == staged
    assert (root / ".git" / "MERGE_HEAD").read_text(encoding="utf-8") == parent
    assert _git(root, "rev-parse", target).stdout == target_before


def test_crash_after_merge_commit_recovers_landing_without_recommitting(tmp_git_repo: Path) -> None:
    root = tmp_git_repo
    target, _record = _prepared(root)
    assert _git(root, "commit", "--no-edit").returncode == 0
    committed = _git(root, "rev-parse", "HEAD").stdout.strip()
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    outcome = recover_incomplete_integration(WorkspaceScope(root))
    assert outcome is not None and outcome.fast_forwarded
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == committed
    assert _git(root, "rev-parse", target).stdout.strip() == committed
    assert read_record(root) is None


def test_moving_target_preserves_completed_pending_merge(tmp_git_repo: Path) -> None:
    root = tmp_git_repo
    target, record = _prepared(root)
    advanced = _git(
        root,
        "commit-tree",
        record.merge_commit_tree or "",
        "-p",
        record.merge_commit_parent or "",
        "-m",
        "concurrent target",
    ).stdout.strip()
    assert advanced
    assert _git(root, "update-ref", f"refs/heads/{target}", advanced).returncode == 0
    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=UnifiedConfig.model_validate(
            {
                "general": {
                    "auto_integrate_target": target,
                    "auto_integrate_remote_enabled": False,
                }
            }
        ),
    )
    assert outcome is not None and outcome.fast_forwarded
    assert _git(root, "show", "HEAD:shared.txt").stdout == "resolved\n"
    assert _git(root, "rev-parse", target).stdout == _git(root, "rev-parse", "HEAD").stdout
    assert _git(root, "merge-base", "--is-ancestor", advanced, "HEAD").returncode == 0
    assert _git(root, "show", "HEAD^1:shared.txt").stdout == "resolved\n"
    assert read_record(root) is None
    assert len(_git(root, "show", "-s", "--format=%P", "HEAD").stdout.split()) == 2


def test_successful_hook_cannot_publish_changed_verified_merge(tmp_git_repo: Path) -> None:
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.rebase_state import RebaseState

    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    _commit(root, "shared.txt", "feature\n")
    assert _git(root, "switch", target).returncode == 0
    target_tip = _commit(root, "shared.txt", "target\n")
    assert _git(root, "switch", "feature").returncode == 0
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\nprintf 'hook changed source\\n' > shared.txt\ngit add shared.txt\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    calls: list[str] = []

    def resolve(repo: Path, branch: str) -> bool:
        calls.append(branch)
        (repo / "shared.txt").write_text("verified resolution\n", encoding="utf-8")
        return True

    outcome = auto_integrate_after_commit(
        UnifiedConfig.model_validate(
            {
                "general": {
                    "auto_integrate_target": target,
                    "auto_integrate_remote_enabled": False,
                }
            }
        ),
        WorkspaceScope(root),
        RebaseState(),
        conflict_resolver=resolve,
    )
    assert outcome is not None and not outcome.fast_forwarded
    assert outcome.recovery_record_retained
    assert calls == [target]
    assert _git(root, "rev-parse", target).stdout.strip() == target_tip
    retained = read_record(root)
    assert retained is not None and retained.merge_commit_tree
    assert (
        _git(root, "show", f"{retained.merge_commit_tree}:shared.txt").stdout
        == "verified resolution\n"
    )
    assert _git(root, "show", "HEAD:shared.txt").stdout == "hook changed source\n"


@pytest.mark.parametrize("record_state", ["missing", "corrupt"])
@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_unowned_clean_resolution_is_preserved(
    tmp_git_repo: Path,
    record_state: str,
    operation: str,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    _commit(root, "shared.txt", "feature\n")
    assert _git(root, "switch", target).returncode == 0
    _commit(root, "shared.txt", "target\n")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, operation, target).returncode != 0
    chosen = _git(root, "show", "HEAD:shared.txt").stdout
    (root / "shared.txt").write_text(chosen, encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    assert not _git(root, "status", "--porcelain", "--untracked-files=no").stdout
    if record_state == "corrupt":
        record_path = root / ".agent" / "auto_integrate_in_progress.json"
        record_path.parent.mkdir(exist_ok=True)
        record_path.write_text("{broken", encoding="utf-8")
    head = _git(root, "rev-parse", "HEAD").stdout
    tree = _git(root, "write-tree").stdout
    marker = root / ".git" / ("MERGE_HEAD" if operation == "merge" else "REBASE_HEAD")
    marker_value = marker.read_bytes()
    outcome = recover_incomplete_integration(WorkspaceScope(root))
    assert outcome is not None and outcome.recovery_record_retained
    assert _git(root, "rev-parse", "HEAD").stdout == head
    assert _git(root, "write-tree").stdout == tree
    assert marker.read_bytes() == marker_value
