from __future__ import annotations

import shutil
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
    _git(sub_origin, "checkout", "-q", "branch_a")
    _git(sub_origin, "commit", "--allow-empty", "-m", "descendant of a")
    return _git(sub_origin, "rev-parse", "HEAD").stdout.strip()


def _setup_superproject(tmp_path: Path, sub_origin: Path) -> Path:
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


_SUBMODULE_REMOTE_URL_FILES: tuple[str, ...] = (
    ".gitmodules",
    ".git/config",
    ".git/modules/sub/config",
)


def _repoint_submodule_remote(super_repo: Path, old_url: str, new_url: str) -> None:
    for relative in _SUBMODULE_REMOTE_URL_FILES:
        target = super_repo / relative
        text = target.read_text(encoding="utf-8")
        if old_url not in text:
            raise RuntimeError(f"superproject template records no {old_url} in {relative}")
        target.write_text(text.replace(old_url, new_url), encoding="utf-8")
    _git(super_repo, "add", ".gitmodules")
    _git(super_repo, "commit", "--amend", "--no-edit")


@pytest.fixture(scope="module")
def _remote_template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, str, str]:
    template_root = tmp_path_factory.mktemp("gitlink-remote-template")
    return _setup_submodule_remote(template_root)


@pytest.fixture(scope="module")
def _superproject_template(
    tmp_path_factory: pytest.TempPathFactory,
    _remote_template: tuple[Path, str, str, str],
) -> Path:
    template_root = tmp_path_factory.mktemp("gitlink-superproject-template")
    return _setup_superproject(template_root, _remote_template[0])


@pytest.fixture
def submodule_remote(
    tmp_path: Path,
    _remote_template: tuple[Path, str, str, str],
) -> tuple[Path, str, str, str]:
    template, sha_base, sha_a, sha_b = _remote_template
    sub_origin = tmp_path / "sub_remote"
    shutil.copytree(template, sub_origin)
    return sub_origin, sha_base, sha_a, sha_b


@pytest.fixture
def super_repo(
    tmp_path: Path,
    submodule_remote: tuple[Path, str, str, str],
    _remote_template: tuple[Path, str, str, str],
    _superproject_template: Path,
) -> Path:
    sub_origin = submodule_remote[0]
    repo_path = tmp_path / "super"
    shutil.copytree(_superproject_template, repo_path)
    _repoint_submodule_remote(repo_path, str(_remote_template[0]), str(sub_origin))
    return repo_path


@pytest.fixture
def allow_file_transport(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    cfg_dir = tmp_path_factory.mktemp("git_file_proto")
    cfg_file = cfg_dir / "gitconfig"
    cfg_file.write_text('[protocol "file"]\n\tallow = always\n', encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg_file))
    return cfg_file


@pytest.mark.timeout_seconds(5)
def test_gitlink_ancestor_descendant_objects_present_resolved(
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    sub_origin, base, sha_a, _sha_b = submodule_remote
    sha_desc = _add_descendant_commit(sub_origin)

    _git(super_repo / "sub", "fetch", "-q", "origin")

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

    unmerged = _git(super_repo, "ls-files", "-u").stdout.strip()
    assert unmerged == "", (
        f"expected conflict to be resolved, but unmerged entries remain:\n{unmerged}"
    )

    staged = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert staged.startswith(f"160000 {sha_desc} 0")


@pytest.mark.timeout_seconds(5)
def test_gitlink_submodule_absent_objects_fetchable_resolved(
    tmp_path: Path,
    allow_file_transport: Path,
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    sub_origin, base, sha_a, _sha_b = submodule_remote
    sha_desc = _add_descendant_commit(sub_origin)

    clone_repo = tmp_path / "super_clone"
    _git(tmp_path, "clone", "-q", str(super_repo), str(clone_repo))

    assert not (clone_repo / "sub" / ".git").exists()

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
def test_gitlink_denied_file_transport_escalates_on_sight_without_agent(
    tmp_path: Path,
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    sub_origin, base, sha_a, _sha_b = submodule_remote
    sha_desc = _add_descendant_commit(sub_origin)

    clone_repo = tmp_path / "super_clone"
    _git(tmp_path, "clone", "-q", str(super_repo), str(clone_repo))

    assert not (clone_repo / "sub" / ".git").exists()

    index_info = f"160000 {base} 1\tsub\n160000 {sha_a} 2\tsub\n160000 {sha_desc} 3\tsub\n"
    _git(clone_repo, "update-index", "--index-info", input_text=index_info)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("sub",),
        sha="testsha_denied",
        subject="rebase stop denied file transport",
    )

    agent_calls: list[str] = []
    session = ResolutionSession()
    with bind_active_rebase_resolution_session(session):
        resolved = try_deterministic_resolution(clone_repo, stop)

    assert resolved is False

    assert not (clone_repo / "sub" / ".git").exists()

    assert session.terminal_reason is ResolutionTerminationReason.OUT_OF_REACH
    assert session.last_failure_cause is ResolutionFailureCause.AGENT_FAILURE
    assert session.unresolved_paths == ("sub",)

    expected_cmd = operator_command_for_paths(("sub",))
    assert expected_cmd == "git add -- sub"
    assert session.exhaustion_reason is not None
    assert "sub" in session.exhaustion_reason
    assert expected_cmd in session.exhaustion_reason
    assert agent_calls == []

    unmerged = _git(clone_repo, "ls-files", "-u", "sub").stdout.strip()
    assert f"160000 {base} 1\tsub" in unmerged
    assert f"160000 {sha_a} 2\tsub" in unmerged
    assert f"160000 {sha_desc} 3\tsub" in unmerged

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution.driver import run_rebase_conflict_resolution_outcome
    from ralph.policy import load_policy

    agent_invocations: list[str] = []

    def mock_invoker(agent_name: str, _repo_root: Path, _round_index: int) -> bool:
        agent_invocations.append(agent_name)
        return True

    repo_root = Path(__file__).resolve().parents[1]
    policy_bundle = load_policy(repo_root / "ralph" / "policy" / "defaults")
    config = UnifiedConfig.model_validate({"general": {}})

    driver_session = ResolutionSession()
    outcome = run_rebase_conflict_resolution_outcome(
        root=clone_repo,
        target="main",
        stop=stop,
        config=config,
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=policy_bundle,
        display=None,
        display_context=None,
        invoke=mock_invoker,
        session=driver_session,
    )
    assert outcome.succeeded is False
    assert outcome.reason is ResolutionTerminationReason.OUT_OF_REACH
    assert outcome.exhaustion_message is not None
    assert "git add -- sub" in outcome.exhaustion_message
    assert agent_invocations == []


@pytest.mark.timeout_seconds(5)
def test_gitlink_submodule_present_missing_objects_fetched_and_resolved(
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    sub_origin, base, sha_a, _sha_b = submodule_remote
    sha_desc = _add_descendant_commit(sub_origin)

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

    cat_check_after = run_git(
        ["cat-file", "-e", sha_desc],
        cwd=super_repo / "sub",
        label="cat-file-check-after",
    )
    assert cat_check_after.returncode == 0

    staged = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert staged.startswith(f"160000 {sha_desc} 0")


@pytest.mark.timeout_seconds(5)
def test_diverged_gitlink_escalates_on_sight_no_agent_invoked(
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    _sub_origin, base, sha_a, sha_b = submodule_remote

    _git(super_repo / "sub", "fetch", "-q", "origin")

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

    assert resolved is False

    assert session.terminal_reason is ResolutionTerminationReason.OUT_OF_REACH
    assert session.last_failure_cause is ResolutionFailureCause.AGENT_FAILURE
    assert session.unresolved_paths == ("sub",)

    expected_cmd = operator_command_for_paths(("sub",))
    assert expected_cmd == "git add -- sub"
    assert session.exhaustion_reason is not None
    assert "sub" in session.exhaustion_reason
    assert expected_cmd in session.exhaustion_reason

    kinds = classify_unmerged_conflicts(super_repo, ("sub",))
    assert kinds.get("sub") is ConflictSight.OUT_OF_REACH
    assert out_of_reach_paths(kinds) == ("sub",)

    msg = gitlink_escalation_message(("sub",))
    assert "sub" in msg
    assert expected_cmd in msg

    assert agent_calls == []


@pytest.mark.timeout_seconds(5)
def test_gitlink_unfetchable_objects_escalates_on_sight(
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    _sub_origin, base, sha_a, _sha_b = submodule_remote

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
def test_diverged_gitlink_production_driver_and_rebase_seam(
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline import resolve_rebase_with_config
    from ralph.pipeline.auto_integrate_resolve import handle_unsuccessful_resolution
    from ralph.pipeline.conflict_resolution.driver import run_rebase_conflict_resolution_outcome
    from ralph.policy import load_policy

    _sub_origin, base, sha_a, sha_b = submodule_remote
    _git(super_repo / "sub", "fetch", "-q", "origin")

    rebase_dir = super_repo / ".git" / "rebase-merge"
    rebase_dir.mkdir()
    (rebase_dir / "onto").write_text(base + "\n")
    (rebase_dir / "orig-head").write_text(sha_a + "\n")
    (rebase_dir / "msgnum").write_text("1\n")
    (rebase_dir / "end").write_text("1\n")
    (rebase_dir / "head-name").write_text("refs/heads/feature\n")
    _git(super_repo, "update-ref", "REBASE_HEAD", "HEAD")

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

    merge_result = handle_unsuccessful_resolution(
        outcome,
        outcome.exhaustion_message,
        conflicted=("sub",),
    )
    assert merge_result.reason is not None
    assert "git add -- sub" in merge_result.reason

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

    index_info = f"160000 {base} 1\tsub\n160000 {sha_a} 2\tsub\n160000 {sha_desc} 3\tsub\n"
    _git(super_repo, "update-index", "--index-info", input_text=index_info)

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

    unmerged = _git(super_repo, "ls-files", "-u", "sub").stdout.strip()
    assert f"160000 {base} 1\tsub" in unmerged
    assert f"160000 {sha_a} 2\tsub" in unmerged
    assert f"160000 {sha_desc} 3\tsub" in unmerged
    stage0 = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert " 0\tsub" not in stage0
