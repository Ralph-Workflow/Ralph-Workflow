from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.subprocess_runner import GitRunOptions, GitRunResult, run_git
from ralph.pipeline._pending_merge_commit import prepare_pending_merge
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.timeout_seconds(5)]


def _fast_branch_name(root: Path) -> str | None:
    head_file = root / ".git" / "HEAD"
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content.removeprefix("ref: refs/heads/")
    except OSError:
        pass
    from git import Repo

    repo = Repo(root)
    try:
        return repo.active_branch.name
    except Exception:
        return None
    finally:
        repo.close()


def _fast_rev_parse(root: Path, ref_name: str) -> str | None:
    try:
        if ref_name == "HEAD":
            head_file = root / ".git" / "HEAD"
            head_content = head_file.read_text(encoding="utf-8").strip()
            if head_content.startswith("ref: refs/heads/"):
                target_ref = root / ".git" / head_content.removeprefix("ref: ")
                if target_ref.is_file():
                    return target_ref.read_text(encoding="utf-8").strip()
            elif len(head_content) == 40:
                return head_content
        else:
            ref_file = root / ".git" / "refs" / "heads" / ref_name
            if ref_file.is_file():
                return ref_file.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    from git import Repo

    repo = Repo(root)
    try:
        return repo.rev_parse(ref_name).hexsha
    except Exception:
        return None
    finally:
        repo.close()


def _git(root: Path, *args: str) -> GitRunResult:
    # RALPH-TEST-REASON: GitPython fast-paths for rev-parse, show, and branch reduce test CPU overhead
    if args == ("branch", "--show-current"):
        name = _fast_branch_name(root)
        if name is not None:
            return GitRunResult(args=args, stdout=name + "\n", stderr="", returncode=0)
    if len(args) == 2 and args[0] == "rev-parse" and not args[1].startswith("-"):
        sha = _fast_rev_parse(root, args[1])
        if sha is not None:
            return GitRunResult(args=args, stdout=sha + "\n", stderr="", returncode=0)
    if len(args) == 2 and args[0] == "show" and ":" in args[1]:
        ref, path = args[1].split(":", 1)
        from git import Repo

        repo = Repo(root)
        try:
            content = repo.commit(ref).tree[path].data_stream.read().decode("utf-8")
            return GitRunResult(args=args, stdout=content, stderr="", returncode=0)
        except Exception:
            pass
        finally:
            repo.close()
    return run_git(
        args,
        cwd=root,
        label="pending-recovery-test",
        options=GitRunOptions(timeout=5),
    )


def _commit(root: Path, path: str, content: str) -> str:
    FsWorkspace(root).write(path, content)
    from git import Repo

    repo = Repo(root)
    try:
        repo.index.add([path])
        # RALPH-TEST-REASON: GitPython commit replaces subprocess git add/commit to reduce CPU overhead
        commit = repo.index.commit(path)
        assert commit.hexsha
        return commit.hexsha
    finally:
        repo.close()


_PREPARED_TEMPLATE: list[tuple[Path, str, IntegrationRecord]] = []
_COPY_IGNORE = shutil.ignore_patterns("hooks", "*.sample", "description", "branches")


def _prepared(root: Path) -> tuple[str, IntegrationRecord]:
    if not _PREPARED_TEMPLATE:
        td = Path(tempfile.mkdtemp(prefix="prepared-merge-template-"))
        from git import Repo

        # RALPH-TEST-REASON: GitPython config replaces subprocess git config to reduce CPU overhead
        repo = Repo.init(td)
        with repo.config_writer() as cfg:
            cfg.set_value("user", "name", "Test Committer")
            cfg.set_value("user", "email", "test@example.com")
            cfg.set_value("core", "logAllRefUpdates", "false")
            cfg.set_value("commit", "gpgSign", "false")
            cfg.set_value("gc", "auto", "0")
            cfg.set_value("core", "fsync", "none")
        target = repo.active_branch.name
        repo.close()
        if not target:
            target = "main"
            assert _git(td, "branch", "-M", target).returncode == 0
        _commit(td, "shared.txt", "base\n")
        assert (
            _git(td, "switch", "-c", "feature").returncode == 0
        )  # RALPH-TEST-REASON: template setup uses td instead of root to avoid repeated subprocesses
        _commit(td, "shared.txt", "feature\n")
        assert (
            _git(td, "switch", target).returncode == 0
        )  # RALPH-TEST-REASON: template setup uses td instead of root to avoid repeated subprocesses
        _commit(td, "shared.txt", "target\n")
        assert (
            _git(td, "switch", "feature").returncode == 0
        )  # RALPH-TEST-REASON: template setup uses td instead of root to avoid repeated subprocesses
        assert (
            _git(td, "merge", "--no-edit", target).returncode != 0
        )  # RALPH-TEST-REASON: template setup uses td instead of root to avoid repeated subprocesses
        FsWorkspace(td).write("shared.txt", "resolved\n")
        assert (
            _git(td, "add", "shared.txt").returncode == 0
        )  # RALPH-TEST-REASON: template setup uses td instead of root to avoid repeated subprocesses
        assert prepare_pending_merge(td, target) is None
        record = read_record(td)
        assert record is not None
        _PREPARED_TEMPLATE.append((td, target, record))

    template_dir, target, record = _PREPARED_TEMPLATE[0]
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(template_dir, root, ignore=_COPY_IGNORE)
    return target, record


@pytest.mark.parametrize("change", ["index", "head", "merge_parent", "query_failure"])
def test_pending_merge_refuses_changed_or_unreadable_evidence(
    tmp_git_repo: Path,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_git_repo
    workspace = FsWorkspace(root)
    target, record = _prepared(root)
    if change == "index":
        workspace.write("shared.txt", "new staged work\n")
        assert _git(root, "add", "shared.txt").returncode == 0
    elif change == "head":
        assert _git(root, "update-ref", "HEAD", record.merge_commit_parent or "").returncode == 0
    elif change == "merge_parent":
        workspace.write(
            ".git/MERGE_HEAD",
            f"{record.merge_commit_head}\n",
        )
    else:
        # The pending-merge identity query fails: the public git runner
        # answers with a non-zero status, so no identity value is read.
        monkeypatch.setattr(
            "ralph.git.subprocess_runner.run_git",
            lambda *_args, **_kwargs: GitRunResult(
                args=(),
                returncode=1,
                stdout="",
                stderr="query failed",
            ),
        )
    head = _git(root, "rev-parse", "HEAD").stdout
    staged = _git(root, "write-tree").stdout
    parent = workspace.read(".git/MERGE_HEAD")
    target_before = _git(root, "rev-parse", target).stdout
    outcome = recover_incomplete_integration(WorkspaceScope(root))
    assert outcome is not None and outcome.recovery_record_retained
    assert read_record(root) is not None
    assert _git(root, "rev-parse", "HEAD").stdout == head
    assert _git(root, "write-tree").stdout == staged
    assert workspace.read(".git/MERGE_HEAD") == parent
    assert _git(root, "rev-parse", target).stdout == target_before


def test_crash_after_merge_commit_recovers_landing_without_recommitting(tmp_git_repo: Path) -> None:
    root = tmp_git_repo
    target, _record = _prepared(root)
    assert _git(root, "commit", "--no-edit").returncode == 0
    committed = _git(root, "rev-parse", "HEAD").stdout.strip()
    hook = root / ".git" / "hooks" / "pre-commit"
    FsWorkspace(root).write(".git/hooks/pre-commit", "#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    outcome = recover_incomplete_integration(WorkspaceScope(root))
    assert outcome is not None and outcome.fast_forwarded
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == committed
    assert _git(root, "rev-parse", target).stdout.strip() == committed
    assert read_record(root) is None


@pytest.mark.timeout_seconds(15)  # Headroom under concurrent 9-shard CPU load
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
    FsWorkspace(root).write(
        ".git/hooks/pre-commit",
        "#!/bin/sh\nprintf 'hook changed source\\n' > shared.txt\ngit add shared.txt\n",
    )
    hook.chmod(0o755)
    calls: list[str] = []

    def resolve(repo: Path, branch: str) -> bool:
        calls.append(branch)
        FsWorkspace(repo).write("shared.txt", "verified resolution\n")
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
    workspace = FsWorkspace(root)
    target = _git(root, "branch", "--show-current").stdout.strip()
    _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    _commit(root, "shared.txt", "feature\n")
    assert _git(root, "switch", target).returncode == 0
    _commit(root, "shared.txt", "target\n")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, operation, target).returncode != 0
    chosen = _git(root, "show", "HEAD:shared.txt").stdout
    workspace.write("shared.txt", chosen)
    assert _git(root, "add", "shared.txt").returncode == 0
    assert not _git(root, "status", "--porcelain", "--untracked-files=no").stdout
    if record_state == "corrupt":
        workspace.write(".agent/auto_integrate_in_progress.json", "{broken")
    head = _git(root, "rev-parse", "HEAD").stdout
    tree = _git(root, "write-tree").stdout
    marker_name = "MERGE_HEAD" if operation == "merge" else "REBASE_HEAD"
    marker_value = workspace.read_bytes(f".git/{marker_name}")[0]
    outcome = recover_incomplete_integration(WorkspaceScope(root))
    assert outcome is not None and outcome.recovery_record_retained
    assert _git(root, "rev-parse", "HEAD").stdout == head
    assert _git(root, "write-tree").stdout == tree
    assert workspace.read_bytes(f".git/{marker_name}")[0] == marker_value
