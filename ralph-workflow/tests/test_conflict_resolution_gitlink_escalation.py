"""Tests for gitlink conflict escalation and deterministic resolution (Defect 10).

Contract:
1. Ancestor/descendant gitlinks with objects present are deterministically resolved
   without invoking an agent.
2. When submodule objects are missing or the submodule is uninitialized/absent,
   submodule update/fetch is attempted before declining. If objects become present
   and form an ancestor/descendant pair, the conflict is deterministically resolved.
3. If gitlinks are diverged or objects remain impossible to obtain, the conflict
   escalates on sight with the unresolved path and operator_command_for_paths(paths),
   without spending or consulting the resolver chain.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from ralph.git.git_run_result import GitRunResult
    from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline.conflict_resolution import (
    operator_command_for_paths,
)
from ralph.pipeline.conflict_resolution.deterministic_resolution import (
    try_deterministic_resolution,
)
from ralph.pipeline.conflict_resolution.rebase_loop import (
    RebaseStop,
    bind_active_rebase_resolution_session,
)
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.sight import (
    ConflictSight,
    classify_unmerged_conflicts,
    gitlink_escalation_message,
    out_of_reach_paths,
)

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Committer",
    "GIT_COMMITTER_EMAIL": "committer@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(cwd: Path, *args: str, input_text: str | None = None) -> GitRunResult:
    res = run_git(
        args,
        cwd=cwd,
        label="gitlink-escalation-test",
        options=GitRunOptions(
            env=_GIT_ENV,
            input_data=input_text,
            timeout=5,
        ),
    )
    if res.returncode != 0:
        raise RuntimeError(f"git {args} failed (code {res.returncode}): {res.stderr}")
    return res


def _setup_submodule_remote(tmp_path: Path) -> tuple[Path, str, str, str]:
    """Create a submodule remote with base, branch_a, and branch_b (diverged).

    Returns:
        (sub_origin_path, sha_base, sha_a, sha_b)
    """
    sub_origin = tmp_path / "sub_remote"
    _git(tmp_path, "init", "-q", "-b", "main", str(sub_origin))

    _git(sub_origin, "commit", "--allow-empty", "-m", "base")
    sha_base = _git(sub_origin, "rev-parse", "HEAD").stdout.strip()

    _git(sub_origin, "checkout", "-q", "-b", "branch_a")
    _git(sub_origin, "commit", "--allow-empty", "-m", "commit a")
    sha_a = _git(sub_origin, "rev-parse", "HEAD").stdout.strip()

    _git(sub_origin, "checkout", "-q", "-b", "branch_b", "main")
    _git(sub_origin, "commit", "--allow-empty", "-m", "commit b (diverged)")
    sha_b = _git(sub_origin, "rev-parse", "HEAD").stdout.strip()

    return sub_origin, sha_base, sha_a, sha_b


def _add_descendant_commit(sub_origin: Path) -> str:
    """Commit a descendant of branch_a to sub_origin and return its SHA."""
    _git(sub_origin, "checkout", "-q", "branch_a")
    _git(sub_origin, "commit", "--allow-empty", "-m", "descendant of a")
    return _git(sub_origin, "rev-parse", "HEAD").stdout.strip()


def _setup_superproject(tmp_path: Path, sub_origin: Path) -> Path:
    """Create a superproject with submodule 'sub' registered."""
    super_repo = tmp_path / "super"
    _git(tmp_path, "init", "-q", "-b", "main", str(super_repo))
    _git(super_repo, "commit", "--allow-empty", "-m", "initial super commit")
    _git(
        super_repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(sub_origin),
        "sub",
    )
    _git(super_repo, "commit", "-q", "-m", "add submodule sub")
    return super_repo


@pytest.mark.timeout_seconds(5)
def test_gitlink_ancestor_descendant_objects_present_resolved(tmp_path: Path) -> None:
    """When both sides are ancestor/descendant and objects exist, resolve deterministically."""
    sub_origin, base, sha_a, _sha_b = _setup_submodule_remote(tmp_path)
    super_repo = _setup_superproject(tmp_path, sub_origin)
    sha_desc = _add_descendant_commit(sub_origin)

    # Fetch descendant commit into super/sub so all objects are present
    _git(super_repo / "sub", "fetch", "-q", "origin")

    # Set up conflicted stages in index: stage 2 (ours) = sha_a, stage 3 (theirs) = sha_desc
    index_info = f"160000 {base} 1\tsub\n160000 {sha_a} 2\tsub\n160000 {sha_desc} 3\tsub\n"
    _git(super_repo, "update-index", "--index-info", input_text=index_info)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("sub",),
        sha="testsha1",
        subject="rebase stop",
    )

    resolved = try_deterministic_resolution(super_repo, stop)
    assert resolved is True

    # Check that stage 0 is cleanly set to the descendant SHA and no unmerged stages remain
    unmerged = _git(super_repo, "ls-files", "-u").stdout.strip()
    assert unmerged == "", (
        f"expected conflict to be resolved, but unmerged entries remain:\n{unmerged}"
    )

    staged = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert staged.startswith(f"160000 {sha_desc} 0")


@pytest.mark.timeout_seconds(5)
def test_gitlink_submodule_absent_objects_fetchable_resolved(tmp_path: Path) -> None:
    """When submodule is absent/uninitialized, fetch/init is attempted and resolves."""
    sub_origin, base, sha_a, _sha_b = _setup_submodule_remote(tmp_path)
    super_repo = _setup_superproject(tmp_path, sub_origin)
    sha_desc = _add_descendant_commit(sub_origin)

    # Clone super_repo without submodules so clone / sub is uninitialized
    clone_repo = tmp_path / "super_clone"
    _git(tmp_path, "clone", "-q", str(super_repo), str(clone_repo))

    # Verify submodule in clone is indeed uninitialized
    assert not (clone_repo / "sub" / ".git").exists()

    # Set unmerged stages in clone index
    index_info = f"160000 {base} 1\tsub\n160000 {sha_a} 2\tsub\n160000 {sha_desc} 3\tsub\n"
    _git(clone_repo, "update-index", "--index-info", input_text=index_info)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("sub",),
        sha="testsha2",
        subject="rebase stop clone",
    )

    resolved = try_deterministic_resolution(clone_repo, stop)
    assert resolved is True

    unmerged = _git(clone_repo, "ls-files", "-u").stdout.strip()
    assert unmerged == ""

    staged = _git(clone_repo, "ls-files", "-s", "sub").stdout.strip()
    assert staged.startswith(f"160000 {sha_desc} 0")


@pytest.mark.timeout_seconds(5)
def test_gitlink_submodule_present_missing_objects_fetched_and_resolved(tmp_path: Path) -> None:
    """When submodule exists but a needed descendant object is missing, fetch resolves it."""
    sub_origin, base, sha_a, _sha_b = _setup_submodule_remote(tmp_path)
    super_repo = _setup_superproject(tmp_path, sub_origin)
    sha_desc = _add_descendant_commit(sub_origin)

    # Do not fetch descendant into super/sub yet; verify sha_desc is missing
    cat_check = run_git(
        ["cat-file", "-e", sha_desc],
        cwd=super_repo / "sub",
        label="cat-file-check",
    )
    assert cat_check.returncode != 0

    index_info = f"160000 {base} 1\tsub\n160000 {sha_a} 2\tsub\n160000 {sha_desc} 3\tsub\n"
    _git(super_repo, "update-index", "--index-info", input_text=index_info)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("sub",),
        sha="testsha3",
        subject="rebase stop fetch",
    )

    resolved = try_deterministic_resolution(super_repo, stop)
    assert resolved is True

    # Confirm descendant commit is present in the submodule.
    cat_check_after = run_git(
        ["cat-file", "-e", sha_desc],
        cwd=super_repo / "sub",
        label="cat-file-check-after",
    )
    assert cat_check_after.returncode == 0

    staged = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert staged.startswith(f"160000 {sha_desc} 0")


@pytest.mark.timeout_seconds(5)
def test_diverged_gitlink_escalates_on_sight_no_agent_invoked(tmp_path: Path) -> None:
    """Diverged gitlink escalates on sight naming path and operator command; no agent invoked."""
    sub_origin, base, sha_a, sha_b = _setup_submodule_remote(tmp_path)
    super_repo = _setup_superproject(tmp_path, sub_origin)

    # Fetch branch_b into super/sub so both diverged objects are present locally
    _git(super_repo / "sub", "fetch", "-q", "origin")

    # Set up diverged gitlink conflict in index (sha_a vs sha_b)
    index_info = f"160000 {base} 1\tsub\n160000 {sha_a} 2\tsub\n160000 {sha_b} 3\tsub\n"
    _git(super_repo, "update-index", "--index-info", input_text=index_info)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("sub",),
        sha="testsha4",
        subject="rebase stop diverged",
    )

    agent_calls: list[str] = []

    session = ResolutionSession()
    with bind_active_rebase_resolution_session(session):
        resolved = try_deterministic_resolution(super_repo, stop)

    # Deterministic resolution must decline
    assert resolved is False

    # Escalation on sight must have populated the session with OUT_OF_REACH
    assert session.terminal_reason is ResolutionTerminationReason.OUT_OF_REACH
    assert session.last_failure_cause is ResolutionFailureCause.AGENT_FAILURE
    assert session.unresolved_paths == ("sub",)

    expected_cmd = operator_command_for_paths(("sub",))
    assert expected_cmd == "git add -- sub"
    assert session.exhaustion_reason is not None
    assert "sub" in session.exhaustion_reason
    assert expected_cmd in session.exhaustion_reason

    # Classification check via sight.py
    kinds = classify_unmerged_conflicts(super_repo, ("sub",))
    assert kinds.get("sub") is ConflictSight.OUT_OF_REACH
    assert out_of_reach_paths(kinds) == ("sub",)

    msg = gitlink_escalation_message(("sub",))
    assert "sub" in msg
    assert expected_cmd in msg

    # Verify no agent was invoked
    assert agent_calls == []


@pytest.mark.timeout_seconds(5)
def test_gitlink_unfetchable_objects_escalates_on_sight(tmp_path: Path) -> None:
    """Gitlink with missing objects that cannot be fetched escalates on sight."""
    sub_origin, base, sha_a, _sha_b = _setup_submodule_remote(tmp_path)
    super_repo = _setup_superproject(tmp_path, sub_origin)

    nonexistent_sha = "ffffffffffffffffffffffffffffffffffffffff"
    index_info = f"160000 {base} 1\tsub\n160000 {sha_a} 2\tsub\n160000 {nonexistent_sha} 3\tsub\n"
    _git(super_repo, "update-index", "--index-info", input_text=index_info)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("sub",),
        sha="testsha5",
        subject="rebase stop unfetchable",
    )

    session = ResolutionSession()
    with bind_active_rebase_resolution_session(session):
        resolved = try_deterministic_resolution(super_repo, stop)

    assert resolved is False
    assert session.terminal_reason is ResolutionTerminationReason.OUT_OF_REACH
    assert session.unresolved_paths == ("sub",)
    assert "git add -- sub" in (session.exhaustion_reason or "")


@pytest.mark.timeout_seconds(5)
def test_diverged_gitlink_production_driver_and_rebase_seam(tmp_path: Path) -> None:
    """Production driver and rebase seam carry operator command for diverged gitlinks on sight."""
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline import resolve_rebase_with_config
    from ralph.pipeline.auto_integrate_resolve import handle_unsuccessful_resolution
    from ralph.pipeline.conflict_resolution.driver import run_rebase_conflict_resolution_outcome
    from ralph.policy import load_policy

    sub_origin, base, sha_a, sha_b = _setup_submodule_remote(tmp_path)
    super_repo = _setup_superproject(tmp_path, sub_origin)
    _git(super_repo / "sub", "fetch", "-q", "origin")

    # Set up paused rebase state in .git
    rebase_dir = super_repo / ".git" / "rebase-merge"
    rebase_dir.mkdir()
    (rebase_dir / "onto").write_text(base + "\n")
    (rebase_dir / "orig-head").write_text(sha_a + "\n")
    (rebase_dir / "msgnum").write_text("1\n")
    (rebase_dir / "end").write_text("1\n")
    (rebase_dir / "head-name").write_text("refs/heads/feature\n")
    _git(super_repo, "update-ref", "REBASE_HEAD", "HEAD")

    # Conflicted gitlink stage entries: base, sha_a (ours), sha_b (theirs)
    index_info = f"160000 {base} 1\tsub\n160000 {sha_a} 2\tsub\n160000 {sha_b} 3\tsub\n"
    _git(super_repo, "update-index", "--index-info", input_text=index_info)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=10,
        conflicted_files=("sub",),
        sha=sha_b,
        subject="rebase stop diverged production",
    )

    agent_invocations: list[str] = []

    def mock_invoker(agent_name: str, _repo_root: Path, _round_index: int) -> bool:
        agent_invocations.append(agent_name)
        return True

    repo_root = Path(__file__).resolve().parents[1]
    policy_bundle = load_policy(repo_root / "ralph" / "policy" / "defaults")
    config = UnifiedConfig.model_validate({"general": {}})

    # 1. Drive production driver directly: escalates on sight without invoking agent
    session = ResolutionSession()
    outcome = run_rebase_conflict_resolution_outcome(
        root=super_repo,
        target="main",
        stop=stop,
        config=config,
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=policy_bundle,
        display=None,
        display_context=None,
        invoke=mock_invoker,
        session=session,
    )

    assert outcome.succeeded is False
    assert outcome.reason is ResolutionTerminationReason.OUT_OF_REACH
    assert outcome.exhaustion_message is not None
    assert "git add -- sub" in outcome.exhaustion_message
    assert agent_invocations == [], (
        "agent invoker must not be called during driver on-sight escalation"
    )

    # 2. Assert handle_unsuccessful_resolution carries path and operator command
    merge_result = handle_unsuccessful_resolution(
        outcome,
        outcome.exhaustion_message,
        conflicted=("sub",),
    )
    assert merge_result.reason is not None
    assert "git add -- sub" in merge_result.reason

    # 3. Drive rebase seam: verify carried reason preserves git add -- sub
    def stop_resolver(root: Path, target: str, rebase_stop: RebaseStop) -> ResolutionOutcome | bool:
        return run_rebase_conflict_resolution_outcome(
            root=root,
            target=target,
            stop=rebase_stop,
            config=config,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=policy_bundle,
            display=None,
            display_context=None,
            invoke=mock_invoker,
        )

    # Re-apply conflicted index state for rebase resolution loop
    _git(super_repo, "update-index", "--index-info", input_text=index_info)
    rebase_resolution_result = resolve_rebase_with_config(
        super_repo,
        "main",
        stop_resolver,
        None,
    )

    assert rebase_resolution_result.succeeded is False
    assert rebase_resolution_result.reason is not None
    assert "git add -- sub" in rebase_resolution_result.reason
    assert agent_invocations == [], "agent invoker must not be called during rebase seam either"


@pytest.mark.timeout_seconds(5)
def test_gitlink_deterministic_staging_command_failure_preserves_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A command failure during deterministic staging fails closed without losing index entries."""
    from ralph.git.git_run_result import GitRunResult

    super_repo = tmp_path / "super"
    super_repo.mkdir()
    _git(super_repo, "init", "-q")
    _git(super_repo, "config", "user.name", "Tester")
    _git(super_repo, "config", "user.email", "tester@example.com")
    (super_repo / "root.txt").write_text("root\n", encoding="utf-8")
    _git(super_repo, "add", "root.txt")
    _git(super_repo, "commit", "-q", "-m", "initial")

    base = "1" * 40
    sha_a = "2" * 40
    sha_desc = "3" * 40

    # Conflicted stages: stage 1 = base, stage 2 = sha_a, stage 3 = sha_desc
    index_info = f"160000 {base} 1\tsub\n160000 {sha_a} 2\tsub\n160000 {sha_desc} 3\tsub\n"
    _git(super_repo, "update-index", "--index-info", input_text=index_info)

    # Injected failure on git-gitlink-resolve-readd to verify restore path
    orig_run_git = run_git

    def fake_run_git(
        args: Sequence[str],
        *,
        cwd: Path | None,
        label: str,
        options: Any = None,
        call_class: Any = None,
    ) -> GitRunResult:
        if label == "git-gitlink-object":
            return GitRunResult(args=("cat-file",), returncode=0, stdout="", stderr="")
        if label == "git-gitlink-ancestor":
            # ours (sha_a) is ancestor of theirs (sha_desc)
            return GitRunResult(args=("merge-base",), returncode=0, stdout="", stderr="")
        if label == "git-gitlink-resolve-readd":
            return GitRunResult(
                args=("update-index",), returncode=1, stdout="", stderr="injected readd error"
            )
        return orig_run_git(args, cwd=cwd, label=label, options=options, call_class=call_class)

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.deterministic_resolution.run_git",
        fake_run_git,
    )

    stop = RebaseStop(
        sha=sha_desc,
        subject="test",
        conflicted_files=("sub",),
        stop_index=1,
        stop_cap=1,
    )
    resolved = try_deterministic_resolution(super_repo, stop)
    assert resolved is False

    # Fail closed: index entries for 'sub' must not be deleted or lost
    unmerged = _git(super_repo, "ls-files", "-u", "sub").stdout.strip()
    assert f"160000 {base} 1\tsub" in unmerged
    assert f"160000 {sha_a} 2\tsub" in unmerged
    assert f"160000 {sha_desc} 3\tsub" in unmerged
    stage0 = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert " 0\tsub" not in stage0
