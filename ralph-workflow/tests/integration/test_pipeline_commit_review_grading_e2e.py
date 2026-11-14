from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from loguru import logger
from rich.console import Console

import ralph.pipeline.run_loop as run_loop_module
from ralph.agents.completion_signals import evaluate_completion, graded_phase_verdict
from ralph.agents.system_clock import SystemClock
from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.operations import create_commit, stage_all
from ralph.git.proposal_seal import seal_pending
from ralph.mcp.tools.coordination import handle_declare_complete
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline import phase_agent_handler, runner
from ralph.pipeline.effect_executor import demotion_after_finalization
from ralph.pipeline.effects import (
    CommitEffect,
    Effect,
    EmptyCommitEffect,
    InvokeAgentEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    PolicyValidationError,
)
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    Cooldown,
    Spent,
)
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.controller import (
    RecoveryController,
    RecoveryControllerOptions,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._tool_artifact_helpers import MockSession
from tests.plan_fixtures import commit_message_markdown, commit_review_markdown

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"

pytestmark = [
    pytest.mark.timeout_seconds(15),
    pytest.mark.required_auto_integrate_e2e,
]


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    return load_policy(DEFAULT_POLICY_DIR)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}})


def _resolved_integration_verdict(*_args: object, write_back: object = None) -> SimpleNamespace:
    return SimpleNamespace(dispatch_allowed=True)


@pytest.fixture(autouse=True)
def _stub_integration_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "inspect_integration_resolution", _resolved_integration_verdict)
    monkeypatch.setattr(
        run_loop_module,
        "inspect_integration_resolution",
        _resolved_integration_verdict,
    )


def _install_runner_display_context(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, force_width=120)
    monkeypatch.setattr(runner, "make_display_context", lambda **_kwargs: ctx)


def _stub_prompt_materialization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "materialize_prepared_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "materialize_prompt_for_phase", lambda *args, **kwargs: "noop")
    monkeypatch.setattr(runner, "materialize_agent_prompt_if_needed", lambda *args, **kwargs: None)


@pytest.fixture
def commit_review_repo(tmp_git_repo: Path) -> Path:
    sample_file = tmp_git_repo / "src" / "feature.py"
    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text("print('baseline')\n", encoding="utf-8")
    stage_all(tmp_git_repo)
    create_commit(tmp_git_repo, "baseline commit")

    sample_file.write_text("print('reviewed change')\n", encoding="utf-8")
    stage_all(tmp_git_repo)
    return tmp_git_repo


def _submit_review_approval(repo: Path, run_id: str, broker_secret: str) -> None:
    seal = seal_pending(repo, "run", 1)
    md = commit_review_markdown([], tree_sha=seal.tree_sha)
    ws = FsWorkspace(repo)
    session = MockSession(drain="commit")
    session.run_id = run_id
    session.broker_secret = broker_secret

    handle_submit_md_artifact(session, ws, {"artifact_type": "commit_review", "content": md})
    handle_declare_complete(session, ws, {"summary": "Review approved"})


def _submit_commit_message(repo: Path, run_id: str, broker_secret: str, subject: str) -> None:
    md = commit_message_markdown(subject)
    ws = FsWorkspace(repo)
    session = MockSession(drain="development_commit")
    session.run_id = run_id
    session.broker_secret = broker_secret
    handle_submit_md_artifact(session, ws, {"artifact_type": "commit_message", "content": md})
    handle_declare_complete(session, ws, {"summary": "Commit message ready"})


def test_development_commit_review_grading_e2e_pass_advances_to_commit(
    monkeypatch: pytest.MonkeyPatch,
    commit_review_repo: Path,
) -> None:
    broker_secret = "test-secret-dev-review-42"
    monkeypatch.setenv("RALPH_BROKER_SECRET", broker_secret)

    policy_bundle = _default_policy_bundle()
    monkeypatch.setattr(
        runner, "resolve_workspace_scope", lambda: WorkspaceScope(commit_review_repo)
    )
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    _stub_prompt_materialization(monkeypatch)
    _install_runner_display_context(monkeypatch)

    captured_controllers: list[RecoveryController] = []

    def controller_factory(
        state: PipelineState,
        bundle: PolicyBundle,
        config: UnifiedConfig,
    ) -> tuple[RecoveryController, int]:
        del state, bundle, config
        ctrl = RecoveryController(
            options=RecoveryControllerOptions(
                clock=SystemClock(),
                agent_max_consecutive_unproductive=1,
            )
        )
        captured_controllers.append(ctrl)
        return ctrl, 0

    phases_executed: list[str] = []
    last_run_ids: dict[str, str] = {}

    def stub_execute_effect(
        effect: Effect,
        _config: UnifiedConfig,
        _workspace_scope: WorkspaceScope,
        **kwargs: Any,
    ) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            phases_executed.append(effect.phase)
            run_id = str(kwargs.get("run_id") or "test-run-id")
            last_run_ids[effect.phase] = run_id
            if effect.phase == "development_commit_review":
                _submit_review_approval(commit_review_repo, run_id, broker_secret)
                return PipelineEvent.AGENT_SUCCESS
            if effect.phase == "development_commit":
                _submit_commit_message(
                    commit_review_repo,
                    run_id,
                    broker_secret,
                    "feat(feature): apply reviewed change",
                )
                return PipelineEvent.AGENT_SUCCESS
            return PipelineEvent.AGENT_SUCCESS

        if isinstance(effect, CommitEffect):
            phases_executed.append("execute_commit")
            return runner.execute_commit_effect(
                effect, create_commit, stage_all, commit_review_repo
            )
        if isinstance(effect, EmptyCommitEffect):
            phases_executed.append("empty_commit")
            return PipelineEvent.AGENT_SUCCESS
        raise AssertionError(f"Unexpected effect: {type(effect)!r}")

    monkeypatch.setattr(runner, "execute_effect", stub_execute_effect)
    monkeypatch.setattr(runner, "execute_effect_with_optional_display", stub_execute_effect)

    init_state = PipelineState(phase="development_commit_review")
    rc = runner.run(
        _config(),
        initial_state=init_state,
        recovery_controller_factory=controller_factory,
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1},
    )
    assert rc == 0
    assert "development_commit_review" in phases_executed
    assert "development_commit" in phases_executed

    review_run_id = last_run_ids.get("development_commit_review")
    assert review_run_id is not None
    ra = resolve_phase_required_artifact(
        policy_bundle.pipeline,
        policy_bundle.artifacts,
        phase="development_commit_review",
    )
    assert ra is not None

    computed_label, _weakest, _detail = phase_agent_handler.compute_graded_phase_verdict(
        commit_review_repo,
        ra,
        review_run_id,
    )
    assert computed_label == "PASS"

    signals = evaluate_completion(
        commit_review_repo,
        required_artifact=ra,
        run_id=review_run_id,
        receipt_secret=broker_secret,
        sentinel_secret=broker_secret,
    )
    independent_label, _weakest, _detail = graded_phase_verdict(signals, required_artifact=ra)
    assert independent_label == "PASS"

    assert captured_controllers, "Expected a recovery controller to be instantiated"
    ledger = captured_controllers[0].ledger
    assert ledger.is_available("development_commit_review", "claude")
    assert not isinstance(ledger.status("development_commit_review", "claude"), (Cooldown, Spent))


def test_development_final_commit_review_grading_e2e_pass_advances_to_commit(
    monkeypatch: pytest.MonkeyPatch,
    commit_review_repo: Path,
) -> None:
    broker_secret = "test-secret-final-review-99"
    monkeypatch.setenv("RALPH_BROKER_SECRET", broker_secret)

    policy_bundle = _default_policy_bundle()
    monkeypatch.setattr(
        runner, "resolve_workspace_scope", lambda: WorkspaceScope(commit_review_repo)
    )
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    _stub_prompt_materialization(monkeypatch)
    _install_runner_display_context(monkeypatch)

    captured_controllers: list[RecoveryController] = []

    def controller_factory(
        state: PipelineState,
        bundle: PolicyBundle,
        config: UnifiedConfig,
    ) -> tuple[RecoveryController, int]:
        del state, bundle, config
        ctrl = RecoveryController(
            options=RecoveryControllerOptions(
                clock=SystemClock(),
                agent_max_consecutive_unproductive=1,
            )
        )
        captured_controllers.append(ctrl)
        return ctrl, 0

    phases_executed: list[str] = []
    last_run_ids: dict[str, str] = {}

    def stub_execute_effect(
        effect: Effect,
        _config: UnifiedConfig,
        _workspace_scope: WorkspaceScope,
        **kwargs: Any,
    ) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            phases_executed.append(effect.phase)
            run_id = str(kwargs.get("run_id") or "test-run-id")
            last_run_ids[effect.phase] = run_id
            if effect.phase == "development_final_commit_review":
                _submit_review_approval(commit_review_repo, run_id, broker_secret)
                return PipelineEvent.AGENT_SUCCESS
            if effect.phase == "development_final_commit":
                _submit_commit_message(
                    commit_review_repo,
                    run_id,
                    broker_secret,
                    "feat(feature): final reviewed commit",
                )
                return PipelineEvent.AGENT_SUCCESS
            return PipelineEvent.AGENT_SUCCESS

        if isinstance(effect, CommitEffect):
            phases_executed.append("execute_commit")
            return runner.execute_commit_effect(
                effect, create_commit, stage_all, commit_review_repo
            )
        if isinstance(effect, EmptyCommitEffect):
            phases_executed.append("empty_commit")
            return PipelineEvent.AGENT_SUCCESS
        raise AssertionError(f"Unexpected effect: {type(effect)!r}")

    monkeypatch.setattr(runner, "execute_effect", stub_execute_effect)
    monkeypatch.setattr(runner, "execute_effect_with_optional_display", stub_execute_effect)

    init_state = PipelineState(phase="development_final_commit_review")
    rc = runner.run(
        _config(),
        initial_state=init_state,
        recovery_controller_factory=controller_factory,
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1},
    )
    assert rc == 0
    assert "development_final_commit_review" in phases_executed
    assert "development_final_commit" in phases_executed

    review_run_id = last_run_ids.get("development_final_commit_review")
    assert review_run_id is not None
    ra = resolve_phase_required_artifact(
        policy_bundle.pipeline,
        policy_bundle.artifacts,
        phase="development_final_commit_review",
    )
    assert ra is not None

    computed_label, _weakest, _detail = phase_agent_handler.compute_graded_phase_verdict(
        commit_review_repo,
        ra,
        review_run_id,
    )
    assert computed_label == "PASS"

    signals = evaluate_completion(
        commit_review_repo,
        required_artifact=ra,
        run_id=review_run_id,
        receipt_secret=broker_secret,
        sentinel_secret=broker_secret,
    )
    independent_label, _weakest, _detail = graded_phase_verdict(signals, required_artifact=ra)
    assert independent_label == "PASS"

    assert captured_controllers, "Expected a recovery controller to be instantiated"
    ledger = captured_controllers[0].ledger
    assert ledger.is_available("development_final_commit_review", "claude")
    assert not isinstance(
        ledger.status("development_final_commit_review", "claude"), (Cooldown, Spent)
    )


RETIRED_PHASE_NAME = "commit_" + "cleanup"


def test_controller_backed_finalize_attempt_records_no_fact_for_configuration_failure() -> None:
    phase = "development_commit_review"
    agent = "test-agent-review"

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=SystemClock(),
            agent_max_consecutive_unproductive=1,
        )
    )

    facts = AttemptFacts(artifact_valid=False, workspace_changed=False)
    config_exc = PolicyValidationError(
        f"Contract mismatch: phase requires 'commit_review' but prompt asked for '{RETIRED_PHASE_NAME}'"
    )

    controller.finalize_attempt(
        phase,
        agent,
        facts=facts,
        attempt_failed=True,
        exc=config_exc,
    )

    assert controller.ledger.is_available(phase, agent), (
        "Controller must record no AttemptFact on engine configuration failure"
    )

    status = controller.ledger.status(phase, agent)
    assert not isinstance(status, (Cooldown, Spent)), (
        f"Agent must not be demoted or cooled down for engine configuration failure: {status!r}"
    )

    other_agent = "genuine-failure-agent"
    controller.finalize_attempt(
        phase,
        other_agent,
        facts=facts,
        attempt_failed=True,
        exc=None,
    )
    assert not controller.ledger.is_available(phase, other_agent)
    assert isinstance(controller.ledger.status(phase, other_agent), Cooldown)


def test_controller_backed_finalize_attempt_checkpoint_mismatch_records_no_fact() -> None:
    phase = "development_commit_review"
    agent = "test-agent-checkpoint"

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=SystemClock(),
            agent_max_consecutive_unproductive=1,
        )
    )

    facts = AttemptFacts(artifact_valid=False, workspace_changed=False)
    checkpoint_exc = CheckpointPolicyMismatchError(
        RETIRED_PHASE_NAME,
        {"development_commit_review", "planning"},
    )

    controller.finalize_attempt(
        phase,
        agent,
        facts=facts,
        attempt_failed=True,
        exc=checkpoint_exc,
    )

    assert controller.ledger.is_available(phase, agent), (
        "Controller must record no AttemptFact on CheckpointPolicyMismatchError"
    )


def test_ledger_only_fallback_demotion_records_no_fact_for_configuration_failure() -> None:
    phase = "development_commit_review"
    agent = "fallback-agent-config"

    ledger = AgentFailureLedger(
        clock=SystemClock(),
        consecutive_limit=1,
    )

    config_exc = PolicyValidationError(
        f"Contract mismatch: phase requires 'commit_review' but prompt asked for '{RETIRED_PHASE_NAME}'"
    )

    demotion_after_finalization(
        None,
        ledger,
        phase=phase,
        agent=agent,
        workspace_changed=False,
        exc=config_exc,
    )

    assert ledger.is_available(phase, agent), (
        "Ledger fallback must not record AttemptFact on engine configuration failure"
    )
    assert not isinstance(ledger.status(phase, agent), (Cooldown, Spent))


def test_commit_review_grading_diagnostic_log_ordering(
    commit_review_repo: Path,
) -> None:
    captured_messages: list[str] = []
    handler_id = logger.add(
        lambda msg: captured_messages.append(msg.record["message"]),
        level="DEBUG",
    )

    try:
        run_id = "diagnostic-run-order-01"
        secret = "diag-secret"
        seal = seal_pending(commit_review_repo, "run", 1)
        md = commit_review_markdown([], tree_sha=seal.tree_sha)
        ws = FsWorkspace(commit_review_repo)
        session = MockSession(drain="commit")
        session.run_id = run_id
        session.broker_secret = secret

        logger.info("TEST_STEP: submit_artifact")
        handle_submit_md_artifact(
            session,
            ws,
            {"artifact_type": "commit_review", "content": md},
        )

        logger.info("TEST_STEP: declare_complete")
        handle_declare_complete(session, ws, {"summary": "Approved review"})

        logger.info("TEST_STEP: compute_verdict")
        ra = resolve_phase_required_artifact(
            _default_policy_bundle().pipeline,
            _default_policy_bundle().artifacts,
            phase="development_commit_review",
        )
        assert ra is not None
        signals = evaluate_completion(
            commit_review_repo,
            required_artifact=ra,
            run_id=run_id,
            receipt_secret=secret,
            sentinel_secret=secret,
        )
        label, _weakest, _detail = graded_phase_verdict(signals, required_artifact=ra)
        assert label == "PASS"

    finally:
        logger.remove(handler_id)

    submit_idx = next(
        (i for i, m in enumerate(captured_messages) if "TEST_STEP: submit_artifact" in m),
        None,
    )
    complete_idx = next(
        (i for i, m in enumerate(captured_messages) if "TEST_STEP: declare_complete" in m),
        None,
    )
    verdict_idx = next(
        (i for i, m in enumerate(captured_messages) if "TEST_STEP: compute_verdict" in m),
        None,
    )

    assert submit_idx is not None
    assert complete_idx is not None
    assert verdict_idx is not None
    assert submit_idx < complete_idx < verdict_idx

    for msg in captured_messages[:submit_idx]:
        assert "auto-integrate skipped: worktree not clean" not in msg
        assert "catch-up publication deferred" not in msg
