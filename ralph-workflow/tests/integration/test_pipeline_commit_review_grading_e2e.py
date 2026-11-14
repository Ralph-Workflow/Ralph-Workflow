from __future__ import annotations

import dataclasses
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final

import pytest
from loguru import logger
from rich.console import Console

import ralph.pipeline.run_loop as run_loop_module
from ralph.agents.completion_signals import evaluate_completion, graded_phase_verdict
from ralph.agents.system_clock import SystemClock
from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.operations import (
    create_commit,
    get_head_sha,
    has_uncommitted_changes,
    list_changed_paths,
    stage_all,
    write_tree,
)
from ralph.git.proposal_seal import seal_pending
from ralph.mcp.protocol.env import MCP_RUN_ID_ENV
from ralph.mcp.server import append_wire_record
from ralph.mcp.tools.coordination import handle_declare_complete
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from ralph.phases.required_artifacts import (
    RequiredArtifact,
    resolve_phase_required_artifact,
)
from ralph.pipeline import effect_executor, phase_agent_handler, runner
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner import run_agent_attempt
from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
from ralph.pipeline.effect_executor import demotion_after_finalization
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    PolicyValidationError,
)
from ralph.policy.validation.artifact_contract import ArtifactContractMismatchError
from ralph.prompts.materialize import materialize_prompt_for_phase
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
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._tool_artifact_helpers import MockSession
from tests.plan_fixtures import commit_review_markdown

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_commit_review_approve_is_graded_pass_and_advances_to_a_real_commit": (
        "the check now reads the public ledger status projection instead of the "
        "private snapshot counters"
    ),
}


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig
    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.pro_support.hooks import RecoveryControllerFactory

DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_commit_review_approve_is_graded_pass_and_advances_to_a_real_commit changed": (
        "the assertion and its message are unchanged; ruff format reflowed the long "
        "message into parentheses, so the diff shows the single-line form as removed"
    ),
}

pytestmark = [
    pytest.mark.timeout_seconds(15),
    pytest.mark.required_auto_integrate_e2e,
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "_submit_commit_message": (
        "reformatted assertion across multiple lines to conform to ruff format"
    ),
}

REVIEW_PHASES: tuple[str, ...] = (
    "development_commit_review",
    "development_final_commit_review",
)
COMMIT_PHASES: tuple[str, ...] = (
    "development_commit",
    "development_final_commit",
)

REVIEWED_FILE = "src/feature.py"
REVIEWED_CONTENT = "print('reviewed change')\n"

RALPH_TEST_CHANGE_REASONS: Final[dict[str, str]] = {
    "_submit_commit_message": "reformat line length",
}


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


@pytest.fixture
def commit_review_repo(tmp_git_repo: Path) -> Path:
    sample_file = tmp_git_repo / REVIEWED_FILE
    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text("print('baseline')\n", encoding="utf-8")
    stage_all(tmp_git_repo)
    create_commit(
        tmp_git_repo,
        "baseline commit",
        expected_head=get_head_sha(tmp_git_repo),
    )

    sample_file.write_text(REVIEWED_CONTENT, encoding="utf-8")
    stage_all(tmp_git_repo)
    return tmp_git_repo


def _commit_message_markdown(subject: str, files: Iterable[str]) -> str:
    rendered_files = "\n".join(f"- [F-{index}] {path}" for index, path in enumerate(files, start=1))
    return f"""\
---
type: commit
subject: {subject}
---
## Body Summary
- [BS-1] Complete the requested repository change.

## Body Details
- [BD-1] The scoped implementation and focused verification are complete.

## Files
{rendered_files}
"""


@dataclasses.dataclass(frozen=True)
class _TransportCall:
    phase: str
    agent: str
    run_id: str


class _ApprovingTransport:
    def __init__(self, repo: Path, broker_secret: str) -> None:
        self._repo = repo
        self._broker_secret = broker_secret
        self.calls: list[_TransportCall] = []
        self.reviewed_tree_shas: dict[str, str] = {}

    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str]:
        del prompt_file
        phase = str(getattr(options, "phase", "") or "")
        agent = str(getattr(options, "agent_name", "") or config.cmd)
        run_id = self._run_id(options)
        self.calls.append(_TransportCall(phase=phase, agent=agent, run_id=run_id))
        if phase in REVIEW_PHASES:
            self._submit_review(run_id)
        elif phase in COMMIT_PHASES:
            self._submit_commit_message(run_id)
        return iter(("Task declared complete: fake transport",))

    def _run_id(self, options: InvokeOptions | None) -> str:
        env = getattr(options, "extra_env", None)
        if not isinstance(env, dict):
            return ""
        raw = env.get(str(MCP_RUN_ID_ENV))
        return raw if isinstance(raw, str) else ""

    def _session(self, run_id: str, drain: str) -> MockSession:
        session = MockSession(drain=drain)
        session.run_id = run_id
        session.broker_secret = self._broker_secret
        return session

    def _submit_review(self, run_id: str) -> None:
        seal = seal_pending(self._repo, "run", 1)
        self.reviewed_tree_shas[run_id] = seal.tree_sha
        markdown = commit_review_markdown([], tree_sha=seal.tree_sha)
        workspace = FsWorkspace(self._repo)
        session = self._session(run_id, drain="commit")
        submitted = handle_submit_md_artifact(
            session,
            workspace,
            {"artifact_type": "commit_review", "content": markdown},
        )
        assert not submitted.is_error, f"commit_review submission rejected: {submitted.content}"
        append_wire_record(
            self._repo,
            method="tools/call",
            tool_name="ralph_submit_md_artifact",
            params={"artifact_type": "commit_review"},
            run_id=run_id,
            secret=self._broker_secret,
        )
        completed = handle_declare_complete(
            session,
            workspace,
            {"summary": "Review approved"},
        )
        assert not completed.is_error, f"declare_complete rejected: {completed.content}"
        append_wire_record(
            self._repo,
            method="tools/call",
            tool_name="declare_complete",
            params={"summary": "Review approved"},
            run_id=run_id,
            secret=self._broker_secret,
        )

    def _submit_commit_message(self, run_id: str) -> None:
        changed = [path for path in list_changed_paths(self._repo) if path == REVIEWED_FILE]
        assert changed, (
            "the reviewed change must still be pending when the commit message is written"
        )
        markdown = _commit_message_markdown(
            "feat(feature): apply reviewed change",
            changed,
        )
        workspace = FsWorkspace(self._repo)
        session = self._session(run_id, drain="development_commit")
        submitted = handle_submit_md_artifact(
            session,
            workspace,
            {"artifact_type": "commit_message", "content": markdown},
        )
        assert not submitted.is_error, f"commit_message submission rejected: {submitted.content}"
        append_wire_record(
            self._repo,
            method="tools/call",
            tool_name="ralph_submit_md_artifact",
            params={"artifact_type": "commit_message"},
            run_id=run_id,
            secret=self._broker_secret,
        )
        completed = handle_declare_complete(
            session,
            workspace,
            {"summary": "Commit message ready"},
        )
        assert not completed.is_error, f"declare_complete rejected: {completed.content}"
        append_wire_record(
            self._repo,
            method="tools/call",
            tool_name="declare_complete",
            params={"summary": "Commit message ready"},
            run_id=run_id,
            secret=self._broker_secret,
        )


def _selected_agent_for_phase(policy_bundle: PolicyBundle, phase: str) -> str:
    phase_def = policy_bundle.pipeline.phases[phase]
    drain_binding = policy_bundle.agents.agent_drains[phase_def.drain]
    chain_config = policy_bundle.agents.agent_chains[drain_binding.chain]
    assert chain_config.agents, f"no agent chain bound for phase {phase!r}"
    return chain_config.agents[0]


def _build_pipeline_deps(
    *,
    repo: Path,
    policy_bundle: PolicyBundle,
    transport: _ApprovingTransport,
    controller_factory: RecoveryControllerFactory,
    display_context: DisplayContext,
) -> PipelineDeps:
    deps = make_test_pipeline_deps(
        display_context,
        phase_prompt_materializer=materialize_prompt_for_phase,
        artifact_requirements_resolver=resolve_phase_required_artifact,
        policy_bundle=policy_bundle,
        recovery_controller_factory=controller_factory,
        invoke_agent=transport,
        resolve_workspace_scope_fn=lambda _root: WorkspaceScope(repo),
        write_start_commit_fn=lambda _root: None,
        validate_custom_mcp_servers_fn=lambda _root: 0,
    )
    return dataclasses.replace(
        deps,
        commit_effect_executor=None,
        has_uncommitted_changes=has_uncommitted_changes,
    )


@pytest.mark.parametrize(
    ("review_phase", "commit_phase"),
    [
        ("development_commit_review", "development_commit"),
        ("development_final_commit_review", "development_final_commit"),
    ],
    ids=["development_commit_review", "development_final_commit_review"],
)
def test_commit_review_approve_is_graded_pass_and_advances_to_a_real_commit(
    monkeypatch: pytest.MonkeyPatch,
    commit_review_repo: Path,
    review_phase: str,
    commit_phase: str,
) -> None:
    broker_secret = "test-secret-commit-review-grade"
    monkeypatch.setenv("RALPH_BROKER_SECRET", broker_secret)

    policy_bundle = _default_policy_bundle()
    _install_runner_display_context(monkeypatch)

    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    display_context = make_display_context(console=console, force_width=120)

    transport = _ApprovingTransport(commit_review_repo, broker_secret)
    captured_controllers: list[RecoveryController] = []

    def controller_factory(
        state: PipelineState,
        bundle: PolicyBundle,
        config: UnifiedConfig,
    ) -> tuple[RecoveryController, int]:
        del state, bundle, config
        controller = RecoveryController(
            options=RecoveryControllerOptions(
                clock=SystemClock(),
                agent_max_consecutive_unproductive=1,
            )
        )
        captured_controllers.append(controller)
        return controller, 0

    deps = _build_pipeline_deps(
        repo=commit_review_repo,
        policy_bundle=policy_bundle,
        transport=transport,
        controller_factory=controller_factory,
        display_context=display_context,
    )

    production_execute_effect = runner.execute_effect
    head_before = get_head_sha(commit_review_repo)

    rc = runner.run(
        _config(),
        initial_state=PipelineState(phase=review_phase),
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1},
        pipeline_deps=deps,
    )

    assert rc == 0
    assert runner.execute_effect is production_execute_effect, (
        "the production executor must not be replaced by the test"
    )

    review_calls = [call for call in transport.calls if call.phase == review_phase]
    commit_calls = [call for call in transport.calls if call.phase == commit_phase]
    assert review_calls, f"the production executor never invoked the agent for {review_phase}"
    assert commit_calls, f"the pipeline did not advance from {review_phase} to {commit_phase}"

    prompt_path = commit_review_repo / ".agent" / "tmp" / f"{review_phase}_prompt.md"
    assert prompt_path.is_file(), (
        "the production prompt materializer must render the phase prompt on disk"
    )
    prompt_text = prompt_path.read_text(encoding="utf-8")
    assert "commit_review" in prompt_text, (
        "the rendered commit-review prompt must name the required artifact type"
    )

    review_run_id = review_calls[0].run_id
    assert review_run_id, "the production invocation must carry a run id"
    required_artifact = resolve_phase_required_artifact(
        policy_bundle.pipeline,
        policy_bundle.artifacts,
        phase=review_phase,
    )
    assert required_artifact is not None

    computed_label, _weakest, _detail = phase_agent_handler.compute_graded_phase_verdict(
        commit_review_repo,
        required_artifact,
        review_run_id,
    )
    assert computed_label == "PASS", (
        f"the production graded verdict for {review_phase} must be PASS"
    )

    signals = evaluate_completion(
        commit_review_repo,
        required_artifact=required_artifact,
        run_id=review_run_id,
        receipt_secret=broker_secret,
        sentinel_secret=broker_secret,
    )
    independent_label, _weakest, _detail = graded_phase_verdict(
        signals,
        required_artifact=required_artifact,
    )
    assert independent_label == "PASS"

    head_after = get_head_sha(commit_review_repo)
    assert head_after != head_before, "the pipeline must create a real commit"
    assert write_tree(commit_review_repo) == transport.reviewed_tree_shas[review_run_id], (
        "the committed workspace tree must equal the tree approved by commit review"
    )
    assert FsWorkspace(commit_review_repo).read(REVIEWED_FILE) == REVIEWED_CONTENT, (
        "the committed workspace must contain the reviewed file content"
    )

    assert captured_controllers, "Expected a recovery controller to be instantiated"
    ledger = captured_controllers[0].ledger
    selected_agent = _selected_agent_for_phase(policy_bundle, review_phase)
    assert selected_agent == review_calls[0].agent, (
        "the ledger must be inspected for the agent the policy chain actually selected"
    )
    assert ledger.is_available(review_phase, selected_agent)
    assert not isinstance(ledger.status(review_phase, selected_agent), (Cooldown, Spent))
    assert ledger.status(review_phase, selected_agent).is_eligible, (
        "a graded PASS must not charge the agent failure ledger"
    )

    calls_before_mismatch = len(transport.calls)
    mismatched = RequiredArtifact(
        phase=review_phase,
        artifact_type="commit_message",
        artifact_path=required_artifact.artifact_path,
        markdown_path=required_artifact.markdown_path,
        normalizer=None,
    )
    mismatch_effect = InvokeAgentEffect(
        agent_name=selected_agent,
        phase=review_phase,
        prompt_file=str(prompt_path.relative_to(commit_review_repo)),
        drain="commit",
    )
    runner_deps = AttemptRunnerDeps(
        clock=SystemClock(),
        invoke=lambda target, **_kwargs: effect_executor.execute_agent_effect(
            target,
            _config(),
            deps,
            WorkspaceScope(commit_review_repo),
            display_context=display_context,
            invoke_agent=transport,
            required_artifact=mismatched,
        ),
        probe_runner=lambda *_args: None,
        workspace_probe=None,
        connectivity=None,
    )
    with pytest.raises(ArtifactContractMismatchError):
        run_agent_attempt(
            AgentEntryPoint.MAIN_PHASE_LOOP,
            effect=mismatch_effect,
            config=_config(),
            controller=captured_controllers[0],
            deps=runner_deps,
        )
    assert len(transport.calls) == calls_before_mismatch, (
        "the mismatching required-artifact type must be rejected before the agent is invoked"
    )
    assert ledger.is_available(review_phase, selected_agent), (
        "an engine-side contract mismatch must not bench or demote the agent"
    )
    assert not isinstance(ledger.status(review_phase, selected_agent), (Cooldown, Spent)), (
        "an engine-side contract mismatch must not place the agent in cooldown or spent"
    )
    assert ledger.status(review_phase, selected_agent).is_eligible, (
        "an engine-side contract mismatch must not charge the consecutive_unproductive counter"
    )


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
        append_wire_record(
            commit_review_repo,
            method="tools/call",
            tool_name="ralph_submit_md_artifact",
            params={"artifact_type": "commit_review"},
            run_id=run_id,
            secret=secret,
        )
        append_wire_record(
            commit_review_repo,
            method="tools/call",
            tool_name="declare_complete",
            params={"summary": "Approved review"},
            run_id=run_id,
            secret=secret,
        )

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
