from __future__ import annotations

import time
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

from ralph.git.subprocess_runner import GitRunOptions, GitRunResult, run_git
from ralph.pipeline._landed_predicate import operation_landed
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_diagnostic import handoff_integration_diagnostic
from ralph.pipeline.integration_ladder import terminal_completion_message
from ralph.pipeline.landed_verdict import expected_landed_facts
from ralph.pipeline.operation_evidence import identity_fingerprint
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests._integration_ladder_fakes import _seed_terminal_rungs

pytestmark = [pytest.mark.timeout_seconds(30), pytest.mark.subprocess_e2e]

_OPTIONS = GitRunOptions(timeout=10)
_TERMINAL_MARKER = "Integration ladder terminal state:"


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(args, cwd=root, label="landed-predicate-git-facts-test", options=_OPTIONS)


def _value(root: Path, *args: str) -> str:
    result = _git(root, *args)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(root: Path, path: str, content: str, message: str) -> str:
    (root / path).write_text(content, encoding="utf-8")
    assert _git(root, "add", path).returncode == 0
    assert _git(root, "commit", "-m", message).returncode == 0
    return _value(root, "rev-parse", "HEAD")


def _init_repo(root: Path) -> None:
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    assert _git(root, "config", "commit.gpgsign", "false").returncode == 0


def _patch_id(root: Path, commit: str) -> str:
    diff = _value(root, "show", "--no-color", "-p", "--root", commit)
    result = run_git(
        ("patch-id", "--stable"),
        cwd=root,
        label="landed-predicate-git-facts-test",
        options=GitRunOptions(input_data=diff),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.split()[0]


def _clean_rebase_repo(root: Path) -> tuple[str, str, str]:
    _init_repo(root)
    _commit(root, "shared.txt", "base\n", "base commit")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    _commit(root, "feature-one.txt", "feature one\n", "feature one")
    feature = _commit(root, "feature-two.txt", "feature two\n", "feature two")
    assert _git(root, "switch", "main").returncode == 0
    target = _commit(root, "target.txt", "target\n", "target commit")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "rebase", "main").returncode == 0
    return feature, target, _value(root, "rev-parse", "HEAD")


def _lease(root: Path, record: IntegrationRecord) -> IntegrationRecord:
    leased = record.model_copy(
        update={"diagnostic_ownership": f"{identity_fingerprint(record)}:{time.time()}"}
    )
    write_record(root, leased)
    return leased


def _diagnose(
    root: Path,
    record: IntegrationRecord,
    *,
    ledger: AttemptLedger | None = None,
) -> tuple[bool, list[str], list[tuple[str, str]]]:
    invoked: list[str] = []
    captured: list[tuple[str, str]] = []

    def invoke(agent: str, prompt: Path) -> bool:
        invoked.append(agent)
        return False

    def sink(message: object) -> None:
        data: dict[str, object] = getattr(message, "record", {})
        captured.append((str(data.get("level", "")), str(getattr(message, "message", message))))

    handler = loguru_logger.add(sink, level="INFO")
    try:
        result = handoff_integration_diagnostic(
            controller=RecoveryController(options=RecoveryControllerOptions()),
            root=root,
            record=_lease(root, record),
            failure="retained integration after the completed rebase",
            agents=("test-resolver",),
            invoke=invoke,
            ledger=ledger,
        )
    finally:
        loguru_logger.remove(handler)
    return result, invoked, captured


def test_clean_rebase_with_rewritten_commits_is_proven_landed_without_an_agent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    feature, target, rebased_head = _clean_rebase_repo(root)

    assert rebased_head != feature
    assert _patch_id(root, rebased_head) == _patch_id(root, feature)
    original_commits = _value(root, "rev-list", "--reverse", f"{target}..{feature}").splitlines()
    rebased_commits = _value(
        root, "rev-list", "--reverse", f"{target}..{rebased_head}"
    ).splitlines()
    assert len(original_commits) == len(rebased_commits) == 2
    for original, rebased in zip(original_commits, rebased_commits, strict=True):
        assert original != rebased
        assert _patch_id(root, original) == _patch_id(root, rebased)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=target,
        resolving_rebase=True,
        rebase_continue_pending=True,
        owning_worktree=str(root),
    )
    write_record(root, record)

    decision = operation_landed(root, record)

    assert decision.landed is True
    assert decision.proof == "rebased range equivalence"
    assert decision.observed_head == rebased_head
    assert decision.failures == ("integrated_feature_sha is unset",)

    result, invoked, captured = _diagnose(root, record)

    assert result is True
    assert invoked == []
    assert [message for level, message in captured if level == "CRITICAL"] == []
    saved = read_record(root)
    assert saved is not None
    assert saved.phase == "integrated"
    assert saved.integrated_feature_sha == rebased_head


def test_unrelated_head_is_rejected_with_one_terminal_status_that_never_repeats(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    feature, target, rebased_head = _clean_rebase_repo(root)
    assert _git(root, "checkout", "--orphan", "unrelated").returncode == 0
    assert _git(root, "commit", "-m", "unrelated root").returncode == 0
    unrelated_head = _value(root, "rev-parse", "HEAD")
    assert unrelated_head != rebased_head

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=target,
        resolving_rebase=True,
        rebase_continue_pending=True,
        owning_worktree=str(root),
    )
    write_record(root, record)

    decision = operation_landed(root, record)

    assert decision.landed is False
    assert decision.proof == "none"
    assert decision.observed_head == unrelated_head
    status = decision.operator_status("all recorded Git state")
    assert status.count("proof failures:") == 1

    ledger = AttemptLedger(root, ledger_path=root / ".agent" / "landed-git-facts.json")
    _seed_terminal_rungs(ledger)

    terminal: list[str] = []
    invoked: list[str] = []
    captured: list[tuple[str, str]] = []
    for _round in range(3):
        result, round_invoked, captured = _diagnose(root, record, ledger=ledger)
        assert result is False
        invoked.extend(round_invoked)
        terminal.extend(message for _level, message in captured if _TERMINAL_MARKER in message)
        if terminal:
            break

    assert len(terminal) == 1
    assert unrelated_head in terminal[0]
    assert invoked == []
    assert not any("Selected agent" in message for _level, message in captured)


def test_stale_recorded_anchors_do_not_prove_landing(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    base = _commit(root, "shared.txt", "base\n", "base commit")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature\n", "feature commit")
    assert _git(root, "switch", "main").returncode == 0
    target = _commit(root, "target.txt", "target\n", "target commit")
    stale_tree = _value(root, "rev-parse", f"{base}^{{tree}}")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=target,
        resolving_rebase=True,
        rebase_continue_pending=True,
        rebase_continue_head=base,
        rebase_continue_stop=feature,
        rebase_continue_tree=stale_tree,
        owning_worktree=str(root),
    )
    write_record(root, record)

    decision = operation_landed(root, record)

    assert decision.landed is False
    assert decision.proof == "none"
    assert decision.observed_head == target
    assert any("outside the recorded operation" in failure for failure in decision.failures)
    assert any(
        "neither replayed nor upstream-equivalent" in failure for failure in decision.failures
    )


def test_ladder_terminal_message_and_predicate_status_share_one_wording(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    base = _commit(root, "shared.txt", "base\n", "base commit")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature\n", "feature commit")
    assert _git(root, "switch", "main").returncode == 0
    target = _commit(root, "target.txt", "target\n", "target commit")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=target,
        resolving_rebase=True,
        rebase_continue_pending=True,
        rebase_continue_stop=feature,
        rebase_continue_head=target,
        rebase_continue_tree=_value(root, "rev-parse", f"{base}^{{tree}}"),
        owning_worktree=str(root),
    )
    write_record(root, record)

    decision = operation_landed(root, record)
    assert decision.landed is False
    status = decision.operator_status("refs/ralph-backup/fake")

    message = terminal_completion_message(
        record,
        reason="diagnostic budget spent",
        observed_head=decision.observed_head,
        preserved_copy="refs/ralph-backup/fake",
        proof_failures=decision.failures,
    )

    assert message.endswith(status)
    assert message.count(f"expected {'; '.join(expected_landed_facts(record))}; ") == 1
    assert status.count(f"expected {'; '.join(expected_landed_facts(record))}; ") == 1
    assert "land the expected commit; resume point: ralph --resume" in message
    assert "land the expected commit; resume point: ralph --resume" in status
    for label in (
        f"integrated_feature_sha={record.integrated_feature_sha or 'unset'}",
        f"pre_target_sha={record.pre_target_sha or 'unset'}",
        f"pre_feature_sha={record.pre_feature_sha or 'unset'}",
        f"rebase_continue_stop={record.rebase_continue_stop or 'unset'}",
        f"rebase_continue_head={record.rebase_continue_head or 'unset'}",
        f"rebase_continue_tree={record.rebase_continue_tree or 'unset'}",
    ):
        assert label in message
        assert label in status
