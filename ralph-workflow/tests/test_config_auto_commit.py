from __future__ import annotations

import logging
from pathlib import Path

import pytest
from git import Actor, Repo
from loguru import logger

from ralph.config.agent_detection import (
    _commit_deterministic_config_write,
    autowire_chains_to_detected_agent,
    enable_detected_agents,
)
from ralph.config.loader import load_toml
from ralph.git.commit_result import CommitCreationResult
from ralph.git.scoped_auto_commit import ScopedCommitResult, ScopedCommitStatus

pytestmark = [
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(5),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_enable_detected_agents_creates_no_auto_commit_in_repo": (
        "the retired comment-fenced agent blocks no longer activate, so the fixture "
        "promotes the detected agent through its structured [inactive_agents.<name>] "
        "table; the check still asserts that no auto-commit is created, that the "
        "promoted agent lands in [agents.<name>], and that the backup is written"
    ),
    "test_enable_detected_agents_via_symlinked_config_path_creates_no_auto_commit": (
        "the retired comment-fenced agent blocks no longer activate, so the fixture "
        "promotes the detected agent through its structured [inactive_agents.<name>] "
        "table; the check still asserts that no auto-commit is created, that the "
        "symlink is preserved, and that the promoted agent lands in [agents.<name>]"
    ),
}


def _init_repo_with_initial_commit(repo_root: Path) -> None:
    repo = Repo.init(repo_root)
    try:
        repo.config_writer().set_value("user", "name", "Test Author").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()


def _git_log_subjects(repo_root: Path) -> list[str]:
    repo = Repo(repo_root)
    try:
        return [c.message.splitlines()[0] for c in repo.iter_commits()]
    finally:
        repo.close()


def _git_status_clean(repo_root: Path) -> bool:
    repo = Repo(repo_root)
    try:
        return not repo.is_dirty(untracked_files=False)
    finally:
        repo.close()


def test_load_toml_migration_does_not_create_a_deterministic_chore_commit(
    tmp_path: Path,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    initial_content = (
        '[agents.claude]\ncmd = "claude"\ncan_commit = true\ndisplay_name = "Claude Code"\n'
    )
    config_path.write_text(initial_content, encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit("seed retired key", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        initial_head = repo.head.commit.hexsha
    finally:
        repo.close()

    data = load_toml(config_path)

    assert "can_commit" not in data.get("agents", {}).get("claude", {})
    assert config_path.read_text(encoding="utf-8") == initial_content
    repo = Repo(tmp_path)
    try:
        assert repo.head.commit.hexsha == initial_head
    finally:
        repo.close()
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "seed retired key"
    assert _git_status_clean(tmp_path), "tree must be clean after load_toml"


def test_load_toml_migration_via_symlinked_config_path_creates_no_commit(
    tmp_path: Path,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    target = tmp_path / "target.toml"
    initial_content = (
        '[agents.claude]\ncmd = "claude"\ncan_commit = true\ndisplay_name = "Claude Code"\n'
    )
    target.write_text(initial_content, encoding="utf-8")
    config_link = tmp_path / "config.toml"
    config_link.symlink_to(target)
    repo = Repo(tmp_path)
    try:
        repo.index.add(["target.toml"])
        repo.index.commit("seed target", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        repo.index.add(["config.toml"])
        repo.index.commit("seed symlink", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        initial_head = repo.head.commit.hexsha
    finally:
        repo.close()

    assert config_link.is_symlink(), "config.toml must be a symlink at HEAD"
    tracked_mode = repo_head_tree_mode(tmp_path, "config.toml")
    assert tracked_mode == 0o120000, (
        f"config.toml must be tracked as a symlink at HEAD; mode={tracked_mode!r}"
    )

    data = load_toml(config_link)

    assert "can_commit" not in data.get("agents", {}).get("claude", {})
    assert config_link.is_symlink(), "config.toml must remain a symlink"
    assert config_link.resolve() == target.resolve()
    assert target.read_text(encoding="utf-8") == initial_content
    repo = Repo(tmp_path)
    try:
        assert repo.head.commit.hexsha == initial_head
    finally:
        repo.close()
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "seed symlink"
    assert _git_status_clean(tmp_path)


def repo_head_tree_mode(repo_root: Path, path: str) -> str:
    from git import Repo as _Repo

    repo = _Repo(repo_root)
    try:
        try:
            return repo.head.commit.tree[path].mode
        except KeyError:
            return ""
    finally:
        repo.close()


def test_autowire_chains_commits_in_repo_with_fixed_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    defaults = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    main_config = tmp_path / "ralph-workflow.toml"
    main_config.write_text(
        (defaults / "ralph-workflow.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit("seed main config", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()

    monkeypatch.setattr("ralph.config.agent_detection.shutil.which", lambda _: None)

    result = autowire_chains_to_detected_agent(main_config, detected=["codex"])

    assert sorted(result or []) == ["claude"], (
        f"autowire must report the replaced default agents; got: {result!r}"
    )
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "chore(config): update agent configuration", (
        f"autowire must commit with the fixed subject; got: {subjects[0]!r}"
    )
    assert _git_status_clean(tmp_path), (
        "autowire commit must leave the tree clean for the chore commit"
    )


def test_enable_detected_agents_creates_no_auto_commit_in_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow-agents.toml"
    initial_content = (
        '[agents.claude]\ncmd = "claude"\n\n[inactive_agents.codex]\ncmd = "codex exec"\n'
    )
    config_path.write_text(initial_content, encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow-agents.toml"])
        repo.index.commit(
            "seed agents config", author=Actor("t", "t@t"), committer=Actor("t", "t@t")
        )
    finally:
        repo.close()

    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: ["codex"])

    with caplog.at_level(logging.INFO, logger="ralph.config.agents_file_writer"):
        enabled = enable_detected_agents(config_path)

    assert enabled == ["codex"]
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "seed agents config", (
        f"enable must NOT create an auto-commit; got: {subjects[0]!r}"
    )
    assert "chore(config): update agent configuration" not in subjects

    updated_text = config_path.read_text(encoding="utf-8")
    assert "[agents.codex]" in updated_text
    assert "[inactive_agents.codex]" not in updated_text

    bak_path = config_path.with_name("ralph-workflow-agents.toml.bak")
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == initial_content

    assert any(
        "trigger: ralph --init: enable detected agents" in record.message
        and str(bak_path) in record.message
        for record in caplog.records
    )


def test_enable_detected_agents_via_symlinked_config_path_creates_no_auto_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    target = tmp_path / "target.toml"
    initial_content = (
        '[agents.claude]\ncmd = "claude"\n\n[inactive_agents.codex]\ncmd = "codex exec"\n'
    )
    target.write_text(initial_content, encoding="utf-8")
    config_link = tmp_path / "config.toml"
    config_link.symlink_to(target)
    repo = Repo(tmp_path)
    try:
        repo.index.add(["target.toml"])
        repo.index.commit("seed target", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        repo.index.add(["config.toml"])
        repo.index.commit("seed symlink", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()

    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: ["codex"])

    with caplog.at_level(logging.INFO, logger="ralph.config.agents_file_writer"):
        enabled = enable_detected_agents(config_link)

    assert enabled == ["codex"]
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "seed symlink", (
        f"enable must NOT create an auto-commit; got: {subjects[0]!r}"
    )
    assert "chore(config): update agent configuration" not in subjects

    updated_text = config_link.read_text(encoding="utf-8")
    assert "[agents.codex]" in updated_text
    assert "[inactive_agents.codex]" not in updated_text

    assert config_link.is_symlink(), (
        "enable_detected_agents must not replace a symlinked agents config with a regular file"
    )
    assert config_link.resolve() == target.resolve(), (
        "the symlink must still point at the original target file"
    )

    target_text = target.read_text(encoding="utf-8")
    assert "[agents.codex]" in target_text, (
        "the real file behind the symlink must hold the activated agents block"
    )
    assert "[inactive_agents.codex]" not in target_text

    bak_path = config_link.with_name(f"{config_link.name}.bak")
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == initial_content

    backups = sorted(tmp_path.glob("*.bak"))
    assert backups, "the writer must back up the original agents content"
    assert any(path.read_text(encoding="utf-8") == initial_content for path in backups), (
        f"no backup holds the original content; got: {[p.name for p in backups]!r}"
    )

    assert any(
        "trigger: ralph --init: enable detected agents" in record.message
        and str(bak_path) in record.message
        for record in caplog.records
    )


def test_global_config_path_writes_file_but_creates_no_commit(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "subdir" / "no-repo" / "ralph-workflow.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel: dict[str, object] = {"called": False}

    def write_fn() -> object:
        sentinel["called"] = True
        config_path.write_text("[general]\nverbosity = 1\n", encoding="utf-8")
        return None

    result = _commit_deterministic_config_write(
        config_path,
        subject="chore(config): update agent configuration",
        write_fn=write_fn,
    )

    assert sentinel["called"] is True, "write_fn MUST run even on the NOT_REPO branch"
    assert result is None, f"NOT_REPO branch must return None; got: {result!r}"
    assert config_path.exists(), "config file must land on disk"
    assert not (tmp_path / ".git").exists(), (
        "the helper MUST NOT create a git repo on the NOT_REPO branch"
    )


def test_load_toml_unmigrated_file_produces_no_commit(
    tmp_path: Path,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        "[general]\nverbosity = 1\n",
        encoding="utf-8",
    )

    data = load_toml(config_path)

    assert data == {"general": {"verbosity": 1}}
    subjects = _git_log_subjects(tmp_path)
    assert subjects == ["initial"], (
        f"unmigrated file must NOT trigger a chore commit; got: {subjects!r}"
    )
    assert _git_status_clean(tmp_path)


def test_unrelated_prestaged_file_is_preserved_across_config_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "main.py"
    src_file.write_text("print('v1')\n", encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add(["src/main.py"])
        repo.index.commit("seed src", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()

    config_path = tmp_path / ".agent" / "ralph-workflow.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '[agents.claude]\ncmd = "claude"\n',
        encoding="utf-8",
    )
    repo = Repo(tmp_path)
    try:
        repo.index.add([".agent/ralph-workflow.toml"])
        repo.index.commit(
            "seed config",
            author=Actor("t", "t@t"),
            committer=Actor("t", "t@t"),
        )
    finally:
        repo.close()

    src_file.write_text("print('v2')\n", encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add(["src/main.py"])
        staged_before = sorted(repo.git.diff("--cached", "--name-only").splitlines())
        assert staged_before == ["src/main.py"], (
            "Setup invariant: src/main.py MUST be pre-staged before the auto-commit"
        )
    finally:
        repo.close()

    _commit_deterministic_config_write(
        config_path,
        subject="chore(config): update agent configuration",
        write_fn=lambda: config_path.write_text(
            '[agents.claude]\ncmd = "claude-updated"\n',
            encoding="utf-8",
        ),
    )

    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "chore(config): update agent configuration"

    repo = Repo(tmp_path)
    try:
        head_commit = repo.head.commit
        committed_paths = {diff.a_path for diff in head_commit.diff(head_commit.parents[0])}
        assert ".agent/ralph-workflow.toml" in committed_paths
        assert "src/main.py" not in committed_paths
        staged_after = sorted(repo.git.diff("--cached", "--name-only").splitlines())
        assert staged_after == ["src/main.py"]
    finally:
        repo.close()


def test_failed_create_commit_injection_preserves_head_and_index(
    tmp_path: Path,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        '[agents.claude]\ncmd = "claude"\ncan_commit = true\n',
        encoding="utf-8",
    )
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit(
            "seed config with retired key",
            author=Actor("t", "t@t"),
            committer=Actor("t", "t@t"),
        )
    finally:
        repo.close()
    src_file = tmp_path / "src" / "main.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("print('wip')\n", encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add(["src/main.py"])
        repo.index.commit("seed src", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        src_file.write_text("print('wip v2')\n", encoding="utf-8")
        repo.index.add(["src/main.py"])
    finally:
        repo.close()

    head_before = Repo(tmp_path).head.commit.hexsha

    def _failing_create_commit(
        _repo_root: Path, _message: str, *, expected_head: str
    ) -> CommitCreationResult:
        del expected_head
        return CommitCreationResult.failed("simulated commit failure")

    error_records: list[str] = []
    sink_id = logger.add(error_records.append, level="ERROR", format="{message}")
    try:
        result = _commit_deterministic_config_write(
            config_path,
            subject="chore(config): migrate retired agent_can_commit assignments",
            write_fn=lambda: config_path.write_text(
                '[agents.claude]\ncmd = "claude"\n',
                encoding="utf-8",
            ),
            create_commit_fn=_failing_create_commit,
        )
    finally:
        logger.remove(sink_id)

    assert result is not None
    assert result.status is ScopedCommitStatus.FAILED, (
        f"injected failure must report FAILED; got: {result!r}"
    )

    assert Repo(tmp_path).head.commit.hexsha == head_before
    repo = Repo(tmp_path)
    try:
        staged_after = sorted(repo.git.diff("--cached", "--name-only").splitlines())
        assert staged_after == ["src/main.py"], (
            "pre-staged src/main.py MUST be restored after the FAILED rollback"
        )
        committed_paths = {
            diff.a_path for diff in repo.head.commit.diff(repo.head.commit.parents[0])
        }
        assert "ralph-workflow.toml" not in committed_paths
    finally:
        repo.close()
    assert any("FAILED" in line for line in error_records), (
        f"FAILED status must surface as an ERROR log; got: {error_records!r}"
    )


def test_repeat_config_write_is_a_noop(tmp_path: Path) -> None:
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        '[agents.claude]\ncmd = "claude"\n',
        encoding="utf-8",
    )
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit(
            "seed config",
            author=Actor("t", "t@t"),
            committer=Actor("t", "t@t"),
        )
    finally:
        repo.close()

    result_first = _commit_deterministic_config_write(
        config_path,
        subject="chore(config): update agent configuration",
        write_fn=lambda: config_path.write_text(
            '[agents.claude]\ncmd = "claude-updated"\n',
            encoding="utf-8",
        ),
    )
    assert result_first is not None and result_first.status is ScopedCommitStatus.CREATED
    head_after_first = Repo(tmp_path).head.commit.hexsha
    subjects_after_first = _git_log_subjects(tmp_path)
    assert subjects_after_first[0] == "chore(config): update agent configuration"

    result_second = _commit_deterministic_config_write(
        config_path,
        subject="chore(config): update agent configuration",
        write_fn=lambda: config_path.write_text(
            '[agents.claude]\ncmd = "claude-updated"\n',
            encoding="utf-8",
        ),
    )
    assert result_second is not None and result_second.status is ScopedCommitStatus.NOOP
    head_after_second = Repo(tmp_path).head.commit.hexsha
    assert head_after_second == head_after_first, (
        "second identical write MUST NOT produce a new chore commit"
    )
    assert _git_status_clean(tmp_path)


def test_helper_keeps_write_fn_pure_even_when_commit_doesnt_apply(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "fresh" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    writes: list[int] = []

    def counting_write_fn() -> object:
        writes.append(1)
        config_path.write_text("[general]\n", encoding="utf-8")
        return None

    result = _commit_deterministic_config_write(
        config_path,
        subject="chore(config): update agent configuration",
        write_fn=counting_write_fn,
    )
    assert result is None
    assert writes == [1]
    assert config_path.exists()


def test_helper_returns_scoped_commit_result_on_cre_commit_outcome(
    tmp_path: Path,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        "new = true\n",
        encoding="utf-8",
    )
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit(
            "seed config",
            author=Actor("t", "t@t"),
            committer=Actor("t", "t@t"),
        )
    finally:
        repo.close()

    result = _commit_deterministic_config_write(
        config_path,
        subject="chore(config): update agent configuration",
        write_fn=lambda: config_path.write_text("new = true\n# updated\n", encoding="utf-8"),
    )
    assert isinstance(result, ScopedCommitResult)
    assert result.status is ScopedCommitStatus.CREATED
    assert result.sha is not None
    assert Repo(tmp_path).head.commit.hexsha == result.sha


def test_helper_logs_deterministic_subject_at_debug_on_created(
    tmp_path: Path,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        "v = 1\n",
        encoding="utf-8",
    )

    debug_records: list[str] = []
    sink_id = logger.add(debug_records.append, level="DEBUG", format="{message}")
    try:
        _commit_deterministic_config_write(
            config_path,
            subject="chore(config): update agent configuration",
            write_fn=lambda: config_path.write_text("v = 2\n", encoding="utf-8"),
        )
    finally:
        logger.remove(sink_id)

    assert any(str(config_path) in line for line in debug_records), (
        f"DEBUG log must name the committed path; got: {debug_records!r}"
    )
