from __future__ import annotations

import dataclasses
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final

import pytest
from rich.console import Console

import ralph.pipeline.run_loop as run_loop_module
from ralph.agents.system_clock import SystemClock
from ralph.config.bootstrap import auto_seed_default_git_exclude, auto_seed_default_gitignore
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
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline import phase_agent_handler, runner
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import Cooldown, Spent
from ralph.recovery.controller import (
    RecoveryController,
    RecoveryControllerOptions,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._tool_artifact_helpers import MockSession
from tests.plan_fixtures import commit_review_markdown

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig
    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.pro_support.hooks import RecoveryControllerFactory

DEFAULT_POLICY_DIR: Final[Path] = (
    Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"
)
TOTAL_MODULES: Final[int] = 10
DELETED_MODULE: Final[str] = "module_00.py"

pytestmark = [
    pytest.mark.timeout_seconds(15),
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


class _TripwireApproveTransport:
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
        if phase == "development_commit_review":
            self._submit_review(run_id)
        elif phase == "development_commit":
            self._submit_commit_message(run_id)
        return iter(("Task declared complete: fake tripwire approve transport",))

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
        changed = [path for path in list_changed_paths(self._repo) if not path.startswith(".agent")]
        assert changed, (
            "the reviewed deletion must still be pending when the commit message is written"
        )
        markdown = _commit_message_markdown(
            "refactor(modules): remove obsolete module_00",
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
    transport: _TripwireApproveTransport,
    controller_factory: RecoveryControllerFactory,
    display_context: DisplayContext,
) -> PipelineDeps:
    deps = make_test_pipeline_deps(
        display_context,
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


def test_commit_review_tripwire_legitimate_small_deletion_approved_advances_to_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_git_repo: Path,
) -> None:
    broker_secret = "test-secret-tripwire-approve"
    monkeypatch.setenv("RALPH_BROKER_SECRET", broker_secret)

    for index in range(TOTAL_MODULES):
        module_path = tmp_git_repo / f"module_{index:02d}.py"
        module_path.write_text(f"VALUE = {index}\n", encoding="utf-8")

    auto_seed_default_gitignore(tmp_git_repo)
    auto_seed_default_git_exclude(tmp_git_repo)
    stage_all(tmp_git_repo)
    create_commit(
        tmp_git_repo,
        "baseline commit: seed 10 modules",
        expected_head=get_head_sha(tmp_git_repo),
    )
    head_before = get_head_sha(tmp_git_repo)

    deleted_path = tmp_git_repo / DELETED_MODULE
    assert deleted_path.exists()
    deleted_path.unlink()
    assert not deleted_path.exists()

    changed_before = [
        path for path in list_changed_paths(tmp_git_repo) if not path.startswith(".agent")
    ]
    assert DELETED_MODULE in changed_before
    assert len(changed_before) == 1

    policy_bundle = _default_policy_bundle()
    _install_runner_display_context(monkeypatch)

    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    display_context = make_display_context(console=console, force_width=120)

    transport = _TripwireApproveTransport(tmp_git_repo, broker_secret)
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
        repo=tmp_git_repo,
        policy_bundle=policy_bundle,
        transport=transport,
        controller_factory=controller_factory,
        display_context=display_context,
    )

    review_phase = "development_commit_review"
    commit_phase = "development_commit"

    rc = runner.run(
        _config(),
        initial_state=PipelineState(phase=review_phase),
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1},
        pipeline_deps=deps,
    )

    assert rc == 0

    review_calls = [call for call in transport.calls if call.phase == review_phase]
    commit_calls = [call for call in transport.calls if call.phase == commit_phase]
    assert len(review_calls) == 1, (
        f"expected exactly one invocation for {review_phase}, got {len(review_calls)}"
    )
    assert len(commit_calls) == 1, (
        f"expected pipeline to advance to {commit_phase}, got {len(commit_calls)}"
    )

    review_run_id = review_calls[0].run_id
    assert review_run_id, "the review invocation must carry a valid run id"

    required_artifact = resolve_phase_required_artifact(
        policy_bundle.pipeline,
        policy_bundle.artifacts,
        phase=review_phase,
    )
    assert required_artifact is not None
    computed_verdict, _weakest, _detail = phase_agent_handler.compute_graded_phase_verdict(
        tmp_git_repo,
        required_artifact,
        review_run_id,
    )
    assert computed_verdict == "PASS", (
        f"commit review for legitimate deletion must be graded PASS, got: {computed_verdict}"
    )

    assert captured_controllers, "expected a recovery controller to be instantiated"
    controller = captured_controllers[0]
    ledger = controller.ledger
    selected_agent = _selected_agent_for_phase(policy_bundle, review_phase)
    assert selected_agent == review_calls[0].agent
    assert ledger.is_available(review_phase, selected_agent), (
        "approved reviewer must remain available"
    )
    assert ledger.status(review_phase, selected_agent).is_eligible, (
        "approved reviewer must remain eligible"
    )
    assert not isinstance(ledger.status(review_phase, selected_agent), (Cooldown, Spent)), (
        "approved reviewer must not be in cooldown or spent"
    )
    assert selected_agent not in ledger.registered_agents(review_phase), (
        "approved reviewer must record no failure attempt fact"
    )
    assert controller.last_demoted_reason is None, (
        f"no demotion reason must be recorded, got: {controller.last_demoted_reason}"
    )

    head_after = get_head_sha(tmp_git_repo)
    assert head_after != head_before, "pipeline must create a real git commit"
    approved_tree_sha = transport.reviewed_tree_shas[review_run_id]
    assert write_tree(tmp_git_repo) == approved_tree_sha, (
        "the committed workspace tree must exactly match the approved proposal tree"
    )

    assert not (tmp_git_repo / DELETED_MODULE).exists(), (
        f"{DELETED_MODULE} was legitimately deleted and must not exist in committed workspace"
    )
    for index in range(1, TOTAL_MODULES):
        remaining_file = tmp_git_repo / f"module_{index:02d}.py"
        assert remaining_file.exists(), f"expected {remaining_file.name} to be retained"
        assert remaining_file.read_text(encoding="utf-8") == f"VALUE = {index}\n"

    assert not has_uncommitted_changes(tmp_git_repo), (
        "the repository must have no remaining uncommitted changes after commit"
    )
