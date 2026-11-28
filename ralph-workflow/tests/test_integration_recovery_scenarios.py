from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.config.loader import ConfigTomlError, load_toml
from ralph.git.subprocess_runner import GitRunOptions, GitRunResult, run_git
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery_record import (
    compute_continuation_retry_gate_key,
    continue_retained_resolution,
)
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import NextAction
from ralph.pipeline.integration_diagnostic import handoff_integration_diagnostic
from ralph.pipeline.operation_evidence import identity_fingerprint
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    RetryLaterOutcome,
    is_stop_resolution_outcome,
    stop_resolution_outcome_reason,
)
from ralph.policy.loader import default_dir, load_policy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.context import WorkspaceContextError, workspace_context
from tests._fake_git_agent_adapter import RealRepoAgentAdapter
from tests._integration_ladder_fakes import _seed_terminal_rungs

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    'assert settled.phase == "integrated"': (
        "completion-in-place only reached the integrated phase with the landed feature sha by "
        "moving main onto the unrelated orphan commit and discarding main's history; a landing "
        "must only fast-forward the target, so the test now asserts main is unchanged and the "
        "record is not marked integrated, and the single terminal report is still asserted"
    ),
    "test_cooling_resolver_chain_records_the_stop_and_escalates_once (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_cooling_resolver_chain_records_exhaustion_and_escalates_once with an unchanged body"
    ),
    'assert "on cooldown" in status_reason or "conflict resolution incomplete" in status_reason': (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only "
        "name; the resolver message now says the agents are on cooldown instead of "
        "spent/unavailable, and the terminal reason CANDIDATE_EXHAUSTED is renamed "
        "NO_RUNNABLE_CANDIDATE; the checks are otherwise unchanged"
    ),
    "test_spent_resolver_chain_records_exhaustion_and_escalates_once": (
        "operator rule: an agent is either on cooldown or not; note_retry_exhaustion and "
        "all_exhausted are gone, so the chain is cooled with expiring unproductive_limit "
        "cooldowns, the test asserts a positive wait, the same single escalation, and that "
        "the first agent is selectable again after the fake clock passes the expiry"
    ),
    "test_cooling_resolver_chain_records_the_stop_and_escalates_once": (
        "operator rule: an agent is either on cooldown or not; renamed from the spent chain "
        "test, which now cools every agent instead of spending it"
    ),
    "test_unrelated_head_rejected_once_and_further_ticks_are_silent": (
        "the later-tick budget is restored from one tick to two, the observed head moves after the "
        "terminal transition so the durable terminal marker is the only one-shot authority left, and "
        "the durable complete_in_place result (terminal marker, landed feature sha, integrated phase) "
        "plus the unchanged repair-attempt counter, terminal marker and ladder evidence are asserted, "
        "so a repeated terminal warning, a second escalation or a lost completion result fails the test"
    ),
    "test_config_corrupt_agents_toml_surfaces_file_and_check_config": (
        "pass an explicit recovery_controller so the continuation retry gate can compute a key "
        "and consult the resolver; without it the attempt returns WAITING before ConfigFailureOutcome "
        "can surface the agents.toml path and ralph --check-config; verify corrupt config with "
        "load_toml so ConfigFailureOutcome is returned on syntax error; the post-repair continuation "
        "is read as the typed RetryLaterOutcome carrying NO_RUNNABLE_CANDIDATE that replaced the "
        "ExhaustedResolutionFailure string, and the terminal reason is now asserted as well as the "
        "non-WAITING message"
    ),
}

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]

_OPTIONS = GitRunOptions(timeout=5)
_PHASE = "rebase_conflict_resolution"
_CHAIN = ("primary", "fallback")
_TERMINAL_STATUS_MARKER = "Integration ladder terminal state:"


def _git(root: Path, *args: str, label: str = "recovery-scenario") -> GitRunResult:
    return run_git(args, cwd=root, label=label, options=_OPTIONS)


def _commit(root: Path, content: str, message: str) -> str:
    (root / "shared.txt").write_text(content, encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    assert _git(root, "commit", "-m", message).returncode == 0
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _init_repo(root: Path) -> None:
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    assert _git(root, "config", "commit.gpgsign", "false").returncode == 0


def _conflicted_repo(root: Path, *, commits: int = 3) -> tuple[str, str, str]:
    _init_repo(root)
    _commit(root, "base\n", "base")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature-1\n", "feature 1")
    for index in range(2, commits + 1):
        feature = _commit(root, f"feature-{index}\n", f"feature {index}")
    assert _git(root, "switch", "main").returncode == 0
    target = _commit(root, "target\n", "target")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "rebase", "main").returncode != 0
    stop = _git(root, "rev-parse", "REBASE_HEAD").stdout.strip()
    return feature, target, stop


def _completed_rebase(root: Path) -> tuple[str, str, str, str]:
    _init_repo(root)
    _commit(root, "base\n", "base")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature-1\n", "feature 1")
    assert _git(root, "switch", "main").returncode == 0
    target = _commit(root, "target\n", "target")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "rebase", "main").returncode != 0
    stop = _git(root, "rev-parse", "REBASE_HEAD").stdout.strip()
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == target
    (root / "shared.txt").write_text("resolved\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    tree = _git(root, "write-tree").stdout.strip()
    assert _git(root, "rebase", "--continue").returncode == 0
    return feature, target, stop, tree


def _record_for_stop(root: Path, feature: str, target: str, stop: str) -> IntegrationRecord:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=target,
        resolving_rebase=True,
        resolving_stop_sha=stop,
        owning_worktree=str(root),
    )
    write_record(root, record)
    return record


class _LadderClock:
    __test__ = False

    def __init__(self) -> None:
        self._now = 0.0

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _controller(clock: FakeClock) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=load_policy(default_dir()),
            event_bus=FailureEventBus(),
        )
    )


def _capture_lines():
    lines: list[tuple[str, str]] = []

    def sink(message: object) -> None:
        record = getattr(message, "record", {})
        level = getattr(record.get("level"), "name", "")
        lines.append((str(level), str(getattr(message, "message", message))))

    return lines, logger.add(sink, level="DEBUG")


def _repo_identity(root: Path) -> tuple[str, str, str]:
    feature = _git(root, "rev-parse", "refs/heads/feature").stdout.strip()
    target = _git(root, "rev-parse", "refs/heads/main").stdout.strip()
    stop = _git(root, "rev-parse", "REBASE_HEAD").stdout.strip()
    return feature, target, stop


def _diagnose(
    root: Path,
    *,
    ledger: AttemptLedger,
    clock: _LadderClock,
    controller: RecoveryController,
) -> tuple[bool, list[str], list[tuple[str, str]]]:
    record = read_record(root)
    assert record is not None
    leased = record.model_copy(
        update={"diagnostic_ownership": f"{identity_fingerprint(record)}:{time.time()}"}
    )
    write_record(root, leased)
    invoked: list[str] = []
    captured: list[tuple[str, str]] = []

    def _invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return False

    def _sink(message: object) -> None:
        data = getattr(message, "record", {})
        level = data.get("level")
        captured.append(
            (str(getattr(level, "name", "")), str(getattr(message, "message", message)))
        )

    handler = logger.add(_sink, level="WARNING")
    try:
        result = handoff_integration_diagnostic(
            controller=controller,
            root=root,
            record=leased,
            failure="integration retained after the completed rebase lost its head",
            agents=("test-resolver",),
            invoke=_invoke,
            ledger=ledger,
            clock=clock,
        )
    finally:
        logger.remove(handler)
    return result, invoked, captured


def _copy_repo(template: Path, destination: Path) -> Path:
    shutil.copytree(template, destination)
    return destination


@pytest.fixture(scope="module")
def conflicted_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("conflicted-rebase") / "repo"
    root.mkdir()
    _conflicted_repo(root)
    return root


@pytest.fixture(scope="module")
def completed_rebase_repo(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, str, str, str, str]:
    root = tmp_path_factory.mktemp("completed-rebase") / "repo"
    root.mkdir()
    feature, target, stop, tree = _completed_rebase(root)
    return root, feature, target, stop, tree


def test_config_corrupt_agents_toml_surfaces_file_and_check_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conflicted_repo: Path,
) -> None:
    config_home = tmp_path / "xdg-config"
    config_home.mkdir()
    agents_toml = config_home / "ralph-workflow-agents.toml"
    agents_toml.write_text("agents = [\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    root = _copy_repo(conflicted_repo, tmp_path / "repo")
    feature, target, stop = _repo_identity(root)
    record = _record_for_stop(root, feature, target, stop)
    controller = _controller(FakeClock())

    consulted: list[str] = []

    def _resolver(resolver_root: Path, _target: str, _stop: RebaseStop):
        consulted.append(str(resolver_root))
        try:
            load_toml(agents_toml)
            with workspace_context(resolver_root):
                pass
        except (WorkspaceContextError, ConfigTomlError) as exc:
            file = getattr(exc, "file", agents_toml)
            return ConfigFailureOutcome(file, str(exc))
        return NextStrategyOutcome("completion_in_place")

    lines, handler = _capture_lines()
    try:
        first = continue_retained_resolution(
            root,
            record,
            None,
            None,
            _resolver,
            recovery_controller=controller,
            stop_identity=stop,
        )
        second = continue_retained_resolution(
            root,
            read_record(root) or record,
            None,
            None,
            _resolver,
            recovery_controller=controller,
            stop_identity=stop,
        )
    finally:
        logger.remove(handler)

    visible = "\n".join(message for _level, message in lines)
    assert str(agents_toml) in visible
    assert "ralph --check-config" in visible
    assert isinstance(first, str)
    assert "ralph --check-config" in first
    assert not first.startswith("WAITING:")
    assert isinstance(second, str)
    assert second.startswith("WAITING:")
    assert len(consulted) == 1

    agents_toml.write_text('[agent_chains.planning]\nagents = ["claude"]\n', encoding="utf-8")
    third = continue_retained_resolution(
        root,
        read_record(root) or record,
        None,
        None,
        _resolver,
        recovery_controller=controller,
        stop_identity=stop,
    )

    assert isinstance(third, RetryLaterOutcome)
    assert third.terminal_reason is ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE
    assert third.on_change is not None
    assert is_stop_resolution_outcome(third)
    third_reason = stop_resolution_outcome_reason(third)
    assert not third_reason.startswith("WAITING:")
    assert len(consulted) == 2


def test_cooling_resolver_chain_records_the_stop_and_escalates_once(
    tmp_path: Path,
    conflicted_repo: Path,
) -> None:
    root = _copy_repo(conflicted_repo, tmp_path / "repo")
    feature, target, stop = _repo_identity(root)
    record = _record_for_stop(root, feature, target, stop)

    clock = FakeClock()
    controller = _controller(clock)
    for agent in _CHAIN:
        controller.mark_agent_unavailable(
            _PHASE, agent, reason=UnavailabilityReason.UNPRODUCTIVE_LIMIT
        )
    assert controller.earliest_available_wait_ms(_PHASE, list(_CHAIN)) > 0
    assert controller.preferred_agent_index(_PHASE, list(_CHAIN)).agent is None

    def _exhausted_resolver(
        _root: Path,
        _target: str,
        _stop: RebaseStop,
    ) -> NextStrategyOutcome:
        return NextStrategyOutcome(NextAction.LAST_RESORT_RECOVERY.value)

    ledger = AttemptLedger(root)
    status = continue_retained_resolution(
        root,
        record,
        None,
        None,
        _exhausted_resolver,
        recovery_controller=controller,
        chain_agents=list(_CHAIN),
        stop_identity=stop,
    )

    assert isinstance(status, RetryLaterOutcome)
    assert status.terminal_reason is ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE
    assert status.on_change is not None
    assert is_stop_resolution_outcome(status)
    status_reason = stop_resolution_outcome_reason(status)
    assert "on cooldown" in status_reason or "conflict resolution incomplete" in status_reason
    charged = {action: count for (_signature, action), count in ledger.read().repeat_counts.items()}
    assert charged == {NextAction.LAST_RESORT_RECOVERY.value: 1}
    assert ledger.read().global_actions_charged == 0

    again = continue_retained_resolution(
        root,
        read_record(root) or record,
        None,
        None,
        _exhausted_resolver,
        recovery_controller=controller,
        chain_agents=list(_CHAIN),
        stop_identity=stop,
    )

    assert isinstance(again, str)
    assert again.startswith("WAITING:")
    charged_after = {
        action: count for (_signature, action), count in ledger.read().repeat_counts.items()
    }
    assert charged_after == charged

    clock.advance(controller.earliest_available_wait_ms(_PHASE, list(_CHAIN)) / 1000.0)
    assert controller.preferred_agent_index(_PHASE, list(_CHAIN)).agent == "primary"


def test_completed_rebase_landing_uses_no_resolver_and_no_critical(
    tmp_path: Path,
    completed_rebase_repo: tuple[Path, str, str, str, str],
) -> None:
    template, feature, target, stop, _tree = completed_rebase_repo
    root = _copy_repo(template, tmp_path / "repo")
    record = _record_for_stop(root, feature, target, stop)
    adapter = RealRepoAgentAdapter({}, succeeds=False)

    lines, handler = _capture_lines()
    try:
        status = continue_retained_resolution(
            root,
            record,
            None,
            None,
            adapter.resolve_stop,
            stop_identity=stop,
        )
    finally:
        logger.remove(handler)

    assert not isinstance(status, str)
    assert adapter.launch_count == 0
    assert [line for line in lines if line[0] == "CRITICAL"] == []
    saved = read_record(root)
    assert saved is not None and saved.is_integrated


def test_unrelated_head_rejected_once_and_further_ticks_are_silent(
    tmp_path: Path,
    completed_rebase_repo: tuple[Path, str, str, str, str],
) -> None:
    template, feature, target, stop, tree = completed_rebase_repo
    root = _copy_repo(template, tmp_path / "repo")
    main_tip = _git(root, "rev-parse", "refs/heads/main").stdout.strip()
    assert _git(root, "checkout", "--orphan", "unrelated").returncode == 0
    _commit(root, "unrelated\n", "unrelated")
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha=feature,
            pre_target_sha=target,
            rebase_continue_pending=True,
            rebase_continue_head=target,
            rebase_continue_stop=stop,
            rebase_continue_tree=tree,
            owning_worktree=str(root),
        ),
    )
    ledger = AttemptLedger(root, ledger_path=root / ".agent" / "ladder-under-test.json")
    _seed_terminal_rungs(ledger)
    clock = _LadderClock()
    controller = _controller(FakeClock())

    terminal: list[str] = []
    invoked: list[str] = []
    for _round in range(6):
        result, round_invoked, captured = _diagnose(
            root, ledger=ledger, clock=clock, controller=controller
        )
        invoked.extend(round_invoked)
        terminal.extend(
            message for _level, message in captured if _TERMINAL_STATUS_MARKER in message
        )
        assert result is False
        if terminal:
            break

    assert len(terminal) == 1, terminal
    assert invoked == []

    settled = read_record(root)
    assert settled is not None
    marker = settled.resolution_terminal_marker
    assert marker is not None
    assert marker[1] == "complete_in_place"
    assert _git(root, "rev-parse", "refs/heads/main").stdout.strip() == main_tip
    assert settled.phase != "integrated"
    settled_attempts = settled.merge_commit_repair_attempts
    settled_evidence = ledger.read().ladder_evidence

    _commit(root, "unrelated-again\n", "unrelated again")

    later: list[tuple[str, str]] = []
    for _tick in range(2):
        clock.advance(60)
        result, round_invoked, captured = _diagnose(
            root, ledger=ledger, clock=clock, controller=controller
        )
        invoked.extend(round_invoked)
        later.extend(captured)
        assert result is False
        settled = read_record(root)
        assert settled is not None
        assert settled.merge_commit_repair_attempts == settled_attempts
        assert settled.resolution_terminal_marker == marker

    assert invoked == []
    assert [message for level, message in later if level in {"CRITICAL", "WARNING"}] == []
    assert not any("Selected agent" in message for _level, message in later)
    assert len(terminal) == 1
    assert ledger.read().ladder_evidence == settled_evidence


def test_retry_gate_blocks_launch_for_unchanged_identity(
    tmp_path: Path,
    conflicted_repo: Path,
) -> None:
    root = _copy_repo(conflicted_repo, tmp_path / "repo")
    feature, target, stop = _repo_identity(root)
    record = _record_for_stop(root, feature, target, stop)
    clock = FakeClock()
    controller = _controller(clock)
    key = compute_continuation_retry_gate_key(
        root,
        record,
        recovery_controller=controller,
        chain_agents=["primary"],
        stop_identity=stop,
    )
    record = record.with_resolution_gate_key(key)
    write_record(root, record)
    adapter = RealRepoAgentAdapter({}, succeeds=False)
    lines, handler = _capture_lines()
    try:
        status = continue_retained_resolution(
            root,
            record,
            None,
            None,
            adapter.resolve_stop,
            recovery_controller=controller,
            chain_agents=["primary"],
            stop_identity=stop,
        )
    finally:
        logger.remove(handler)

    assert isinstance(status, str) and "retry gate active" in status
    assert "unchanged" in status
    assert adapter.launch_count == 0
    assert any("continuation resolution deferred" in message for _level, message in lines)


def test_benign_probe_not_warning_and_exit_two_still_warns(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    first = _commit(root, "one\n", "one")
    second = _commit(root, "two\n", "two")
    assert _git(root, "checkout", "--detach", first).returncode == 0
    lines, handler = _capture_lines()
    try:
        assert (
            _git(root, "symbolic-ref", "--quiet", "HEAD", label="git-symbolic-ref-head").returncode
            == 1
        )
        assert (
            _git(
                root,
                "merge-base",
                "--is-ancestor",
                second,
                first,
                label="git-is-ancestor",
            ).returncode
            == 1
        )
        assert (
            _git(
                root,
                "rev-parse",
                "--verify",
                "--quiet",
                "MERGE_HEAD",
                label="integration_diagnostic:check_merge_head",
            ).returncode
            == 1
        )
        assert _git(root, "--definitely-invalid", label="probe-exit-two").returncode >= 2
    finally:
        logger.remove(handler)

    warnings = [message for level, message in lines if level == "WARNING"]
    assert not any("git-symbolic-ref-head" in message for message in warnings)
    assert not any("git-is-ancestor" in message for message in warnings)
    assert not any("integration_diagnostic:check_merge_head" in message for message in warnings)
    assert any("probe-exit-two" in message for message in warnings)


def test_diagnostic_probe_check_merge_head_rc_one_is_not_warning(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    lines, handler = _capture_lines()
    try:
        result = _git(
            root,
            "rev-parse",
            "--verify",
            "--quiet",
            "MERGE_HEAD",
            label="integration_diagnostic:check_merge_head",
        )
    finally:
        logger.remove(handler)
    assert result.returncode == 1
    assert not any(
        level == "WARNING" and "integration_diagnostic:check_merge_head" in message
        for level, message in lines
    )
