from __future__ import annotations

import time
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

from ralph.git.subprocess_runner import GitRunOptions, GitRunResult, run_git
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_diagnostic import handoff_integration_diagnostic
from ralph.pipeline.operation_evidence import identity_fingerprint
from tests._integration_ladder_fakes import _seed_terminal_rungs
from tests._resolver_controller import make_resolver_controller

pytestmark = [
    pytest.mark.timeout_seconds(20),
    pytest.mark.subprocess_e2e,
]

_GIT_OPTIONS = GitRunOptions(timeout=10)

_TERMINAL_STATUS_MARKER = "Integration ladder terminal state:"


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(args, cwd=root, label="completed-rebase-blackbox", options=_GIT_OPTIONS)


def _commit(root: Path, path: str, content: str) -> str:
    (root / path).write_text(content, encoding="utf-8")
    assert _git(root, "add", path).returncode == 0
    assert _git(root, "commit", "-m", f"write {path}").returncode == 0
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _completed_rebase_repo(root: Path) -> tuple[str, str, str]:
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    assert _git(root, "config", "commit.gpgsign", "false").returncode == 0
    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")
    assert _git(root, "switch", "main").returncode == 0
    target = _commit(root, "shared.txt", "main\n")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "rebase", "main").returncode != 0
    stop_anchor = _git(root, "rev-parse", "HEAD").stdout.strip()
    stop_commit = _git(root, "rev-parse", "REBASE_HEAD").stdout.strip()
    assert stop_anchor == target
    assert stop_commit == feature
    (root / "shared.txt").write_text("resolved\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    stop_tree = _git(root, "write-tree").stdout.strip()
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha=stop_commit,
            pre_target_sha=stop_anchor,
            rebase_continue_pending=True,
            rebase_continue_head=stop_anchor,
            rebase_continue_stop=stop_commit,
            rebase_continue_tree=stop_tree,
            owning_worktree=str(root),
        ),
    )
    assert _git(root, "rebase", "--continue").returncode == 0
    return base, feature, target


def _run_diagnostic(
    root: Path,
    *,
    ledger: AttemptLedger | None = None,
) -> tuple[bool, list[str], list[tuple[str, str]]]:
    record = read_record(root)
    assert record is not None
    leased = record.model_copy(
        update={"diagnostic_ownership": f"{identity_fingerprint(record)}:{time.time()}"}
    )
    write_record(root, leased)
    invoked: list[str] = []
    captured: list[tuple[str, str]] = []

    def fake_invoke(agent: str, prompt: Path) -> bool:
        invoked.append(agent)
        return False

    def sink(message: object) -> None:
        record_data: dict[str, object] = getattr(message, "record", {})
        level = record_data.get("level")
        level_name = getattr(level, "name", "")
        captured.append((str(level_name), str(getattr(message, "message", message))))

    handler_id = loguru_logger.add(sink, level="WARNING")
    try:
        result = handoff_integration_diagnostic(
            controller=make_resolver_controller(),
            root=root,
            record=leased,
            failure="integration retained after the completed rebase",
            agents=("test-resolver",),
            invoke=fake_invoke,
            ledger=ledger,
        )
    finally:
        loguru_logger.remove(handler_id)
    return result, invoked, captured


def test_completed_rebase_with_rewritten_commits_lands_through_the_diagnostic_prepass(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    base, feature, target = _completed_rebase_repo(root)
    landed_head = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert landed_head not in {base, feature, target}
    assert _git(root, "show", "-s", "--format=%P", landed_head).stdout.strip() == target
    assert (root / ".git" / "REBASE_HEAD").exists()

    result, invoked, captured = _run_diagnostic(root)

    assert result is True
    assert invoked == []
    assert [level for level, _ in captured if level == "CRITICAL"] == []
    saved = read_record(root)
    assert saved is not None
    assert saved.phase == "integrated"
    assert saved.integrated_feature_sha == landed_head
    assert saved.rebase_continue_pending is False
    assert saved.rebase_continue_head is None
    assert saved.rebase_continue_stop is None
    assert saved.rebase_continue_tree is None
    assert saved.rebase_continue_metadata == ()
    assert saved.merge_commit_repair_attempts == 0
    assert saved.diagnostic_ownership is None
    assert saved.diagnostic_evidence == ()
    assert _git(root, "merge-base", "--is-ancestor", "main", "HEAD").returncode == 0


def test_unrelated_head_with_an_identical_tree_is_rejected_with_the_mismatch_facts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _completed_rebase_repo(root)
    landed_tree = _git(root, "rev-parse", "HEAD^{tree}").stdout.strip()
    assert _git(root, "checkout", "--orphan", "unrelated").returncode == 0
    assert _git(root, "commit", "-m", "unrelated root").returncode == 0
    unrelated_head = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert _git(root, "rev-parse", "HEAD^{tree}").stdout.strip() == landed_tree
    assert _git(root, "show", "-s", "--format=%P", "HEAD").stdout.strip() == ""

    result, invoked, _captured = _run_diagnostic(root)

    assert result is False
    assert invoked == []
    saved = read_record(root)
    assert saved is not None
    assert saved.phase == "integrating"
    assert saved.integrated_feature_sha is None
    assert saved.rebase_continue_pending is True
    assert saved.merge_commit_repair_attempts == 1
    error = saved.repair_last_error or ""
    expected_feature = saved.pre_feature_sha
    expected_target = saved.pre_target_sha
    expected_anchor = saved.rebase_continue_head
    expected_tree = saved.rebase_continue_tree
    assert expected_feature is not None
    assert expected_target is not None
    assert expected_anchor is not None
    assert expected_tree is not None
    assert unrelated_head in error
    assert expected_feature in error
    assert expected_target in error
    assert expected_anchor in error
    assert expected_tree in error


def test_unrelated_head_ends_in_one_terminal_status_that_never_repeats(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _completed_rebase_repo(root)
    landed_tree = _git(root, "rev-parse", "HEAD^{tree}").stdout.strip()
    assert _git(root, "checkout", "--orphan", "unrelated").returncode == 0
    assert _git(root, "commit", "-m", "unrelated root").returncode == 0
    assert _git(root, "rev-parse", "HEAD^{tree}").stdout.strip() == landed_tree

    ledger = AttemptLedger(root, ledger_path=root / ".agent" / "ladder-under-test.json")
    _seed_terminal_rungs(ledger)

    terminal_statuses: list[str] = []
    critical: list[str] = []
    invoked: list[str] = []
    for _round in range(8):
        result, round_invoked, captured = _run_diagnostic(root, ledger=ledger)
        invoked.extend(round_invoked)
        terminal_statuses.extend(
            message for _level, message in captured if _TERMINAL_STATUS_MARKER in message
        )
        critical.extend(message for level, message in captured if level == "CRITICAL")
        assert result is False
        if terminal_statuses:
            break

    assert len(terminal_statuses) == 1, terminal_statuses
    assert critical == [], critical
    assert invoked == [], invoked

    result, round_invoked, captured = _run_diagnostic(root, ledger=ledger)
    invoked.extend(round_invoked)
    terminal_statuses.extend(
        message for _level, message in captured if _TERMINAL_STATUS_MARKER in message
    )
    critical.extend(message for level, message in captured if level == "CRITICAL")

    assert result is False
    assert len(terminal_statuses) == 1, terminal_statuses
    assert critical == [], critical
    assert invoked == [], invoked
