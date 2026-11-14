from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from ralph.git.git_call_policy import GitCallClass
    from ralph.git.git_run_result import GitRunResult

from ralph.config.gitlink_policy_config import GitlinkPolicyConfig, GitlinkPreference
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline.conflict_resolution.deterministic_resolution import (
    try_deterministic_resolution,
)
from ralph.pipeline.conflict_resolution.gitlink_policy import choose_gitlink
from ralph.pipeline.conflict_resolution.rebase_loop import (
    RebaseStop,
    bind_active_rebase_resolution_session,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.conflict_resolution.sight import (
    ConflictSight,
    agent_eligible_paths,
    classify_unmerged_conflicts,
    policy_gitlink_paths,
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
        label="gitlink-policy-test",
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
    _git(sub_origin, "checkout", "-q", "-b", "branch_a")
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


def _stage_gitlink_conflict(repo: Path, base: str, ours: str, theirs: str) -> None:
    index_info = f"160000 {base} 1\tsub\n160000 {ours} 2\tsub\n160000 {theirs} 3\tsub\n"
    _git(repo, "update-index", "--index-info", input_text=index_info)


def _stop() -> RebaseStop:
    return RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("sub",),
        sha="gitlink-policy-stop",
        subject="rebase stop gitlink policy",
    )


def _recorded_review(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[object, ...]]:
    from ralph.pipeline.conflict_resolution import deterministic_resolution

    recorded: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        deterministic_resolution.logger,
        "info",
        lambda _message, *args: recorded.append(args),
    )
    return recorded


@pytest.mark.timeout_seconds(5)
def test_gitlink_conflict_completes_in_place_by_policy_without_operator_escalation(
    monkeypatch: pytest.MonkeyPatch,
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    _sub_origin, base, sha_a, sha_b = submodule_remote
    recorded = _recorded_review(monkeypatch)

    _stage_gitlink_conflict(super_repo, base, sha_a, sha_b)

    session = ResolutionSession()
    with bind_active_rebase_resolution_session(session):
        resolved = try_deterministic_resolution(super_repo, _stop())

    assert resolved is True

    unmerged = _git(super_repo, "ls-files", "-u").stdout.strip()
    assert unmerged == "", (
        f"expected the gitlink conflict to be resolved, but unmerged entries remain:\n{unmerged}"
    )

    staged = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert staged.startswith(f"160000 {sha_a} 0")

    assert recorded == [
        (
            "sub",
            GitlinkPreference.VENDOR_IMMUTABLE,
            base,
            sha_a,
            sha_b,
            "ours",
            sha_a,
            ("unchanged-from-base", "ours", "theirs"),
        )
    ]

    assert session.terminal_reason is None
    assert session.operator_action_message is None
    assert session.exhaustion_reason is None
    assert session.unresolved_paths == ()


@pytest.mark.timeout_seconds(5)
def test_gitlink_policy_keeps_the_unchanged_vendor_pointer(
    monkeypatch: pytest.MonkeyPatch,
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    _sub_origin, base, _sha_a, sha_b = submodule_remote
    recorded = _recorded_review(monkeypatch)

    _stage_gitlink_conflict(super_repo, base, base, sha_b)

    session = ResolutionSession()
    with bind_active_rebase_resolution_session(session):
        resolved = try_deterministic_resolution(super_repo, _stop())

    assert resolved is True

    staged = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert staged.startswith(f"160000 {base} 0")

    assert recorded[0][5] == "ours"
    assert recorded[0][6] == base

    assert session.terminal_reason is None
    assert session.operator_action_message is None


@pytest.mark.timeout_seconds(5)
def test_gitlink_policy_preference_theirs_selects_the_theirs_pointer(
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    _sub_origin, base, sha_a, sha_b = submodule_remote

    _stage_gitlink_conflict(super_repo, base, sha_a, sha_b)

    session = ResolutionSession()
    with bind_active_rebase_resolution_session(session):
        resolved = try_deterministic_resolution(
            super_repo,
            _stop(),
            GitlinkPolicyConfig(pointer_preference=GitlinkPreference.THEIRS),
        )

    assert resolved is True

    staged = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert staged.startswith(f"160000 {sha_b} 0")

    assert session.terminal_reason is None
    assert session.operator_action_message is None


@pytest.mark.timeout_seconds(5)
def test_gitlink_policy_review_record_carries_the_three_stage_shas() -> None:
    base = "1" * 40
    ours = "2" * 40
    theirs = "3" * 40

    record = choose_gitlink("sub", base, ours, theirs)

    assert record.path == "sub"
    assert record.policy is GitlinkPreference.VENDOR_IMMUTABLE
    assert record.base == base
    assert record.ours == ours
    assert record.theirs == theirs
    assert record.selected_side == "ours"
    assert record.selected_sha == ours
    assert record.alternatives_considered == ("unchanged-from-base", "ours", "theirs")


@pytest.mark.timeout_seconds(5)
def test_gitlink_conflict_is_policy_owned_and_outside_agent_eligible_kinds(
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    _sub_origin, base, sha_a, sha_b = submodule_remote

    _stage_gitlink_conflict(super_repo, base, sha_a, sha_b)

    kinds = classify_unmerged_conflicts(super_repo, ("sub",))

    assert kinds.get("sub") is ConflictSight.GITLINK_POLICY
    assert policy_gitlink_paths(kinds) == ("sub",)
    assert agent_eligible_paths(kinds) == ()
    assert kinds.get("sub") not in {ConflictSight.AGENT, ConflictSight.AGENT_DECISION}


@pytest.mark.timeout_seconds(5)
def test_diverged_gitlink_with_unfetchable_object_still_resolves_by_policy(
    submodule_remote: tuple[Path, str, str, str],
    super_repo: Path,
) -> None:
    _sub_origin, base, sha_a, _sha_b = submodule_remote

    unfetchable = "f" * 40
    absent_before = run_git(
        ("-C", str(super_repo / "sub"), "cat-file", "-e", unfetchable),
        cwd=super_repo,
        label="gitlink-object-absent-before",
    )
    assert absent_before.returncode != 0, "the submodule object must be absent before the resolve"

    _stage_gitlink_conflict(super_repo, base, sha_a, unfetchable)

    session = ResolutionSession()
    with bind_active_rebase_resolution_session(session):
        resolved = try_deterministic_resolution(super_repo, _stop())

    assert resolved is True

    staged = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert staged.startswith(f"160000 {sha_a} 0")

    assert session.terminal_reason is None
    assert session.operator_action_message is None
    assert session.exhaustion_reason is None


@pytest.mark.timeout_seconds(5)
def test_gitlink_staging_failure_keeps_the_index_unmerged(
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

    _stage_gitlink_conflict(super_repo, base, sha_a, sha_desc)

    orig_run_git = run_git

    def fake_run_git(
        args: Sequence[str],
        *,
        cwd: Path | None,
        label: str,
        options: GitRunOptions | None = None,
        call_class: GitCallClass | None = None,
    ) -> GitRunResult:
        if label == "git-gitlink-resolve-readd":
            return GitRunResult(
                args=("update-index",), returncode=1, stdout="", stderr="injected readd error"
            )
        return orig_run_git(args, cwd=cwd, label=label, options=options, call_class=call_class)

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.deterministic_resolution.run_git",
        fake_run_git,
    )

    session = ResolutionSession()
    with bind_active_rebase_resolution_session(session):
        resolved = try_deterministic_resolution(super_repo, _stop())

    assert resolved is False

    unmerged = _git(super_repo, "ls-files", "-u", "sub").stdout.strip()
    assert f"160000 {base} 1\tsub" in unmerged
    assert f"160000 {sha_a} 2\tsub" in unmerged
    assert f"160000 {sha_desc} 3\tsub" in unmerged
    stage0 = _git(super_repo, "ls-files", "-s", "sub").stdout.strip()
    assert " 0\tsub" not in stage0
