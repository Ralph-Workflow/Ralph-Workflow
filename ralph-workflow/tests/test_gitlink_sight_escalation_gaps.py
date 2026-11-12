from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from ralph.git.git_run_result import GitRunResult

from ralph.config.models import UnifiedConfig
from ralph.git.git_run_result import GitRunResult
from ralph.git.merge import conflict_stage_entries
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline.conflict_resolution.driver import run_rebase_conflict_resolution_outcome
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.sight import (
    ConflictSight,
    classify_stage_map,
)
from ralph.policy import load_policy

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
        label="test-gitlink-sight-gaps",
        options=GitRunOptions(
            env=_GIT_ENV,
            input_data=input_text,
            timeout=5,
        ),
    )
    if res.returncode != 0:
        raise RuntimeError(f"git {args} failed (code {res.returncode}): {res.stderr}")
    return res


def _setup_test_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.com")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _add_unmerged_path(repo: Path, path: str, *, content: str = "conflict\n") -> None:
    (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", path)
    sha = _git(repo, "rev-parse", f":{path}").stdout.strip()
    index_info = f"100644 {sha} 1\t{path}\n100644 {sha} 2\t{path}\n100644 {sha} 3\t{path}\n"
    _git(repo, "update-index", "--index-info", input_text=index_info)


@pytest.mark.timeout_seconds(5)
def test_unreadable_index_escalates_out_of_reach_without_gitlink_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_test_repo(tmp_path)
    _add_unmerged_path(repo, "file_p.txt", content="conflict\n")

    from ralph.git import conflict_stage_entries as stage_entries_mod

    orig_run_git = stage_entries_mod.run_git

    def fake_run_git(args: Any, *a: Any, **kw: Any) -> GitRunResult:
        if "ls-files" in args:
            return GitRunResult(args=tuple(args), returncode=1, stdout="", stderr="index corrupted")
        return orig_run_git(args, *a, **kw)

    monkeypatch.setattr(stage_entries_mod, "run_git", fake_run_git)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("file_p.txt",),
        sha="deadbeef",
        subject="test stop",
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
        root=repo,
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
    assert agent_invocations == [], "no agent should be invoked for unreadable index"
    assert outcome.exhaustion_message is not None
    assert "could not read unmerged index stages for file_p.txt" in outcome.exhaustion_message
    assert "git add -- file_p.txt" in outcome.exhaustion_message
    assert "gitlink" not in outcome.exhaustion_message.lower(), "must not use gitlink wording"


@pytest.mark.timeout_seconds(5)
def test_mixed_stop_unreadable_index_and_text_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_test_repo(tmp_path)
    _add_unmerged_path(repo, "P", content="conflict P\n")
    _add_unmerged_path(
        repo,
        "Q",
        content="<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\n",
    )

    from ralph.git import conflict_stage_entries as stage_entries_mod

    orig_run_git = stage_entries_mod.run_git

    def fake_run_git(args: Any, *a: Any, **kw: Any) -> GitRunResult:
        if "ls-files" in args:
            if "P" in args and "Q" not in args:
                return GitRunResult(
                    args=tuple(args), returncode=1, stdout="", stderr="index corrupted for P"
                )
            if "Q" in args and "P" not in args:
                return GitRunResult(
                    args=tuple(args),
                    returncode=0,
                    stdout=(
                        "100644 1111111111111111111111111111111111111111 2\tQ\0"
                        "100644 2222222222222222222222222222222222222222 3\tQ\0"
                    ),
                    stderr="",
                )
            return GitRunResult(args=tuple(args), returncode=1, stdout="", stderr="batch failed")
        return orig_run_git(args, *a, **kw)

    monkeypatch.setattr(stage_entries_mod, "run_git", fake_run_git)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("P", "Q"),
        sha="deadbeef",
        subject="mixed stop",
    )

    agent_invocations: list[str] = []

    def mock_invoker(agent_name: str, _prompt_path: Path, _round_index: int) -> bool:
        agent_invocations.append(agent_name)
        (repo / "Q").write_text("resolved Q\n", encoding="utf-8")
        return True

    repo_root = Path(__file__).resolve().parents[1]
    policy_bundle = load_policy(repo_root / "ralph" / "policy" / "defaults")
    config = UnifiedConfig.model_validate({"general": {}})
    session = ResolutionSession()

    outcome = run_rebase_conflict_resolution_outcome(
        root=repo,
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

    assert len(agent_invocations) > 0, "reachable path Q must be offered to agent chain"

    assert outcome.succeeded is False
    assert outcome.reason is ResolutionTerminationReason.OUT_OF_REACH
    assert outcome.exhaustion_message is not None
    assert "could not read unmerged index stages for P" in outcome.exhaustion_message
    assert "git add -- P" in outcome.exhaustion_message
    assert "gitlink" not in outcome.exhaustion_message.lower(), "must not use gitlink wording"


@pytest.mark.timeout_seconds(5)
def test_malformed_ls_files_record_isolates_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_test_repo(tmp_path)

    from ralph.git import conflict_stage_entries as stage_entries_mod

    output = (
        "malformed_metadata\tP\0"
        "100644 1111111111111111111111111111111111111111 2\tQ\0"
        "100755 2222222222222222222222222222222222222222 3\tQ\0"
    )

    monkeypatch.setattr(
        stage_entries_mod,
        "run_git",
        lambda *args, **kw: GitRunResult(
            args=("ls-files",), returncode=0, stdout=output, stderr=""
        ),
    )

    entries = conflict_stage_entries(repo, ["P", "Q"])
    assert "Q" in entries
    assert entries["Q"] == {
        2: ("100644", "1111111111111111111111111111111111111111"),
        3: ("100755", "2222222222222222222222222222222222222222"),
    }
    assert "P" not in entries
    failures = getattr(entries, "failures", {})
    assert "P" in failures
    assert "malformed" in failures["P"]


@pytest.mark.timeout_seconds(5)
def test_gitlink_vs_directory_escalates_out_of_reach() -> None:
    stages_gitlink_ours = {
        2: ("160000", "aaaa" * 10),
        3: ("040000", "bbbb" * 10),
    }
    sight1 = classify_stage_map(stages_gitlink_ours, binary=False, has_markers=False)
    assert sight1 is ConflictSight.OUT_OF_REACH, "gitlink vs directory must be OUT_OF_REACH"

    stages_gitlink_theirs = {
        2: ("040000", "aaaa" * 10),
        3: ("160000", "bbbb" * 10),
    }
    sight2 = classify_stage_map(stages_gitlink_theirs, binary=False, has_markers=False)
    assert sight2 is ConflictSight.OUT_OF_REACH, "directory vs gitlink must be OUT_OF_REACH"

    stages_file_vs_dir = {
        2: ("100644", "aaaa" * 10),
        3: ("040000", "bbbb" * 10),
    }
    sight3 = classify_stage_map(stages_file_vs_dir, binary=False, has_markers=False)
    assert sight3 is ConflictSight.AGENT_DECISION


@pytest.mark.timeout_seconds(5)
def test_endpoint_merge_with_diverged_gitlink_escalates_on_sight_no_agents(
    tmp_path: Path,
) -> None:
    from ralph.pipeline.auto_integrate_resolve import endpoint_merge_with_resolution
    from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_outcome

    repo = _setup_test_repo(tmp_path)
    sha_base = "1" * 40
    sha_a = "2" * 40
    sha_b = "3" * 40

    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha_base},sub")
    _git(repo, "commit", "-q", "-m", "base sub")

    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "update-index", "--cacheinfo", f"160000,{sha_b},sub")
    _git(repo, "commit", "-q", "-m", "feature sub")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "update-index", "--cacheinfo", f"160000,{sha_a},sub")
    _git(repo, "commit", "-q", "-m", "main sub")

    _git(repo, "checkout", "-q", "feature")

    agent_invocations: list[str] = []

    def mock_invoker(agent_name: str, _repo_root: Path, _round_index: int) -> bool:
        agent_invocations.append(agent_name)
        return True

    repo_root = Path(__file__).resolve().parents[1]
    policy_bundle = load_policy(repo_root / "ralph" / "policy" / "defaults")
    config = UnifiedConfig.model_validate({"general": {}})

    def resolver(repo_root_arg: Path, target_arg: str) -> Any:
        return run_conflict_resolution_outcome(
            root=repo_root_arg,
            target=target_arg,
            config=config,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=policy_bundle,
            display=None,
            display_context=None,
            invoke=mock_invoker,
        )

    merge_result = endpoint_merge_with_resolution(repo, "main", resolver)
    assert merge_result is not None
    assert merge_result.outcome != "success"
    assert "git add -- sub" in (merge_result.reason or "")
    assert agent_invocations == [], (
        "zero agents should be invoked for diverged gitlink in endpoint merge"
    )


@pytest.mark.timeout_seconds(5)
def test_index_query_exit_128_not_a_git_repo_escalates_out_of_reach_zero_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_test_repo(tmp_path)
    _add_unmerged_path(repo, "file_p.txt", content="conflict\n")

    from ralph.git import conflict_stage_entries as stage_entries_mod

    orig_run_git = stage_entries_mod.run_git

    def fake_run_git(args: Any, *a: Any, **kw: Any) -> GitRunResult:
        if "ls-files" in args:
            return GitRunResult(
                args=tuple(args),
                returncode=128,
                stdout="",
                stderr="fatal: not a git repository (or any of the parent directories): .git",
            )
        return orig_run_git(args, *a, **kw)

    monkeypatch.setattr(stage_entries_mod, "run_git", fake_run_git)

    entries = conflict_stage_entries(repo, ["file_p.txt"])
    assert "file_p.txt" not in entries
    assert "file_p.txt" in entries.failures
    assert "could not read unmerged index stages for file_p.txt" in entries.failures["file_p.txt"]

    entries_multi = conflict_stage_entries(repo, ["file_p.txt", "file_q.txt"])
    assert "file_p.txt" in entries_multi.failures
    assert "file_q.txt" in entries_multi.failures
    assert (
        "could not read unmerged index stages for file_p.txt"
        in entries_multi.failures["file_p.txt"]
    )
    assert (
        "could not read unmerged index stages for file_q.txt"
        in entries_multi.failures["file_q.txt"]
    )

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("file_p.txt",),
        sha="deadbeef",
        subject="test stop",
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
        root=repo,
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
    assert agent_invocations == [], "zero agents should be invoked on exit 128 not a git repo"
    assert outcome.exhaustion_message is not None
    assert "could not read unmerged index stages for file_p.txt" in outcome.exhaustion_message


@pytest.mark.timeout_seconds(5)
def test_malformed_no_tab_record_escalates_out_of_reach_zero_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_test_repo(tmp_path)
    _add_unmerged_path(repo, "corrupt.txt", content="conflict\n")

    from ralph.git import conflict_stage_entries as stage_entries_mod

    orig_run_git = stage_entries_mod.run_git

    def fake_run_git(args: Any, *a: Any, **kw: Any) -> GitRunResult:
        if "ls-files" in args:
            return GitRunResult(
                args=tuple(args),
                returncode=0,
                stdout="malformed_record_without_tab\0",
                stderr="",
            )
        return orig_run_git(args, *a, **kw)

    monkeypatch.setattr(stage_entries_mod, "run_git", fake_run_git)

    entries = conflict_stage_entries(repo, ["corrupt.txt"])
    assert "corrupt.txt" not in entries
    assert "corrupt.txt" in entries.failures
    assert "malformed index stage record for corrupt.txt" in entries.failures["corrupt.txt"]

    entries_multi = conflict_stage_entries(repo, ["corrupt1.txt", "corrupt2.txt"])
    assert "corrupt1.txt" not in entries_multi
    assert "corrupt2.txt" not in entries_multi
    assert "corrupt1.txt" in entries_multi.failures
    assert "corrupt2.txt" in entries_multi.failures
    assert "malformed index stage record for corrupt1.txt" in entries_multi.failures["corrupt1.txt"]
    assert "malformed index stage record for corrupt2.txt" in entries_multi.failures["corrupt2.txt"]

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("corrupt.txt",),
        sha="deadbeef",
        subject="test stop",
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
        root=repo,
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
    assert agent_invocations == [], "zero agents should be invoked on malformed no-tab record"
    assert outcome.exhaustion_message is not None
    assert "malformed index stage record for corrupt.txt" in outcome.exhaustion_message


@pytest.mark.timeout_seconds(5)
def test_binary_gitlink_vs_file_classification_and_driver_zero_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stages_gitlink_ours = {
        2: ("160000", "a" * 40),
        3: ("100644", "b" * 40),
    }
    sight1 = classify_stage_map(stages_gitlink_ours, binary=True, has_markers=False)
    assert sight1 is ConflictSight.OUT_OF_REACH, "binary gitlink-vs-file must be OUT_OF_REACH"

    stages_gitlink_theirs = {
        2: ("100644", "a" * 40),
        3: ("160000", "b" * 40),
    }
    sight2 = classify_stage_map(stages_gitlink_theirs, binary=True, has_markers=False)
    assert sight2 is ConflictSight.OUT_OF_REACH, "binary file-vs-gitlink must be OUT_OF_REACH"

    stages_diverged = {
        2: ("160000", "a" * 40),
        3: ("160000", "b" * 40),
    }
    sight3 = classify_stage_map(stages_diverged, binary=True, has_markers=False)
    assert sight3 is ConflictSight.OUT_OF_REACH, "binary diverged gitlink must be OUT_OF_REACH"

    stages_identical = {
        1: ("160000", "a" * 40),
        2: ("160000", "a" * 40),
        3: ("160000", "a" * 40),
    }
    sight4 = classify_stage_map(stages_identical, binary=True, has_markers=False)
    assert sight4 is ConflictSight.MECHANICAL, "binary identical gitlink must be MECHANICAL"

    stages_files = {
        2: ("100644", "a" * 40),
        3: ("100644", "b" * 40),
    }
    sight5 = classify_stage_map(stages_files, binary=True, has_markers=False)
    assert sight5 is ConflictSight.AGENT_DECISION

    repo = _setup_test_repo(tmp_path)
    (repo / "bin_link").write_bytes(b"\0binary_data")

    from ralph.git import conflict_stage_entries as stage_entries_mod

    orig_run_git = stage_entries_mod.run_git

    def fake_run_git(args: Any, *a: Any, **kw: Any) -> GitRunResult:
        if "ls-files" in args:
            output = f"160000 {'a' * 40} 2\tbin_link\x00100644 {'b' * 40} 3\tbin_link\x00"
            return GitRunResult(args=tuple(args), returncode=0, stdout=output, stderr="")
        return orig_run_git(args, *a, **kw)

    monkeypatch.setattr(stage_entries_mod, "run_git", fake_run_git)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("bin_link",),
        sha="deadbeef",
        subject="binary gitlink stop",
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
        root=repo,
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
    assert agent_invocations == [], "zero agents should be invoked on binary gitlink-vs-file"
    assert outcome.exhaustion_message is not None
    assert "OUT_OF_REACH" in outcome.exhaustion_message
    assert "gitlink conflict on bin_link" in outcome.exhaustion_message


@pytest.mark.timeout_seconds(5)
def test_mixed_stop_malformed_record_and_text_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_test_repo(tmp_path)
    _add_unmerged_path(repo, "P", content="conflict P\n")
    _add_unmerged_path(
        repo,
        "Q",
        content="<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\n",
    )

    from ralph.git import conflict_stage_entries as stage_entries_mod

    orig_run_git = stage_entries_mod.run_git

    def fake_run_git(args: Any, *a: Any, **kw: Any) -> GitRunResult:
        if "ls-files" in args:
            output = (
                "bad_metadata_without_proper_tokens\tP\0"
                "100644 1111111111111111111111111111111111111111 2\tQ\0"
                "100644 2222222222222222222222222222222222222222 3\tQ\0"
            )
            return GitRunResult(args=tuple(args), returncode=0, stdout=output, stderr="")
        return orig_run_git(args, *a, **kw)

    monkeypatch.setattr(stage_entries_mod, "run_git", fake_run_git)

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("P", "Q"),
        sha="deadbeef",
        subject="mixed stop with malformed P",
    )

    agent_invocations: list[str] = []

    def mock_invoker(agent_name: str, _prompt_path: Path, _round_index: int) -> bool:
        agent_invocations.append(agent_name)
        (repo / "Q").write_text("resolved Q\n", encoding="utf-8")
        return True

    repo_root = Path(__file__).resolve().parents[1]
    policy_bundle = load_policy(repo_root / "ralph" / "policy" / "defaults")
    config = UnifiedConfig.model_validate({"general": {}})
    session = ResolutionSession()

    outcome = run_rebase_conflict_resolution_outcome(
        root=repo,
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

    assert len(agent_invocations) > 0, "reachable path Q must be offered to agent chain"
    assert outcome.succeeded is False
    assert outcome.reason is ResolutionTerminationReason.OUT_OF_REACH
    assert outcome.exhaustion_message is not None
    assert "malformed index stage record for P" in outcome.exhaustion_message
