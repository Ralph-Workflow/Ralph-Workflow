from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo
from rich.console import Console

from ralph.agents.chain import ChainManager
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.operations import create_commit, stage_all
from ralph.phases import PhaseContext, handle_phase
from ralph.pipeline import runner
from ralph.pipeline.effects import (
    CommitEffect,
    Effect,
    EmptyCommitEffect,
    InvokeAgentEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests.integration._commit_review_always_loopback_invoker import (
    CommitReviewAlwaysLoopbackInvoker,
)
from tests.plan_fixtures import commit_review_markdown

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import PolicyBundle
    from ralph.workspace.memory import MemoryWorkspace

DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"

pytestmark = [pytest.mark.timeout_seconds(15), pytest.mark.subprocess_e2e]

ORIGINALLY_FAILING_PATHS: tuple[str, ...] = (
    "checkpoint.json",
    ".agent/raw/opencode.log",
    ".agent/tmp/mcp-server.log",
)

PENDING_WORK_PATH: str = "src/pending_work.py"

EXPECTED_GITIGNORE_FRAGMENTS: tuple[str, ...] = (
    ".agent/",
    "/checkpoint.json",
)

EXPECTED_GIT_EXCLUDE_FRAGMENTS: tuple[str, ...] = (
    ".agent/raw/",
    ".agent/tmp/",
    ".agent/completion_seen_*.json",
    "/checkpoint.json",
)


def _resolve_tree_sha_from_workspace(workspace: FsWorkspace) -> str | None:
    context_path = Path(workspace.root) / ".agent" / "tmp" / "commit_review_context.json"
    if context_path.exists():
        try:
            import json

            data = json.loads(context_path.read_text(encoding="utf-8"))
            tree_sha = data.get("tree_sha")
            if isinstance(tree_sha, str) and tree_sha:
                return tree_sha
        except Exception:
            pass

    repo_path = Path(workspace.root)
    if (repo_path / ".git").exists():
        try:
            from ralph.config.bootstrap import (
                auto_seed_default_git_exclude,
                auto_seed_default_gitignore,
            )
            from ralph.git.commit_review import untrack_engine_internal_files
            from ralph.git.proposal_seal import seal_pending
            from ralph.phases.commit_review import is_agent_internal_path

            untrack_engine_internal_files(repo_path, is_agent_internal_path)
            auto_seed_default_gitignore(repo_path)
            auto_seed_default_git_exclude(repo_path)
            return seal_pending(repo_path, "run", 1).tree_sha
        except Exception:
            pass
    return None


def _write_commit_review_artifact(workspace: FsWorkspace, content: dict[str, object]) -> None:
    raw_actions = content["actions"]
    actions = [
        (action["action"], action.get("path", action.get("pattern", ""))) for action in raw_actions
    ]
    tree_sha = content.get("tree_sha")
    if tree_sha is None:
        tree_sha = _resolve_tree_sha_from_workspace(workspace)
    path = Path(workspace.root) / ".agent" / "artifacts" / "commit_review.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        commit_review_markdown(actions, tree_sha=str(tree_sha) if tree_sha else None),
        encoding="utf-8",
    )


def _write_commit_message_artifacts(repo_root: Path, subject: str) -> None:
    artifact_path = repo_root / ".agent" / "artifacts" / "commit_message.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        "\n".join(
            (
                "---",
                "type: commit",
                f"subject: {subject}",
                "---",
                "## Body Summary",
                "- [BS-1] Complete the requested repository change.",
                "## Body Details",
                "- [BD-1] The scoped implementation and focused verification are complete.",
                "",
            )
        ),
        encoding="utf-8",
    )


def _track_and_commit(repo_root: Path, rel_paths: tuple[str, ...] | list[str] | str) -> None:
    paths = [rel_paths] if isinstance(rel_paths, str) else list(rel_paths)
    repo = Repo(repo_root)
    try:
        repo.index.add(paths)
        repo.index.commit("track " + ", ".join(paths))
    finally:
        repo.close()


@pytest.fixture
def engine_internal_workspace(tmp_git_repo: Path) -> FsWorkspace:
    root_checkpoint = tmp_git_repo / "checkpoint.json"
    root_checkpoint.write_text('{"phase": "development"}')
    raw_log = tmp_git_repo / ".agent" / "raw" / "opencode.log"
    raw_log.parent.mkdir(parents=True, exist_ok=True)
    raw_log.write_text("log content\n")
    tmp_log = tmp_git_repo / ".agent" / "tmp" / "mcp-server.log"
    tmp_log.parent.mkdir(parents=True, exist_ok=True)
    tmp_log.write_text("mcp log\n")
    _track_and_commit(
        tmp_git_repo,
        (
            "checkpoint.json",
            ".agent/raw/opencode.log",
            ".agent/tmp/mcp-server.log",
        ),
    )

    pending_work = tmp_git_repo / PENDING_WORK_PATH
    pending_work.parent.mkdir(parents=True, exist_ok=True)
    pending_work.write_text("initial pending work\n", encoding="utf-8")
    _track_and_commit(tmp_git_repo, PENDING_WORK_PATH)
    pending_work.write_text("updated pending work\n", encoding="utf-8")

    (tmp_git_repo / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)

    return FsWorkspace(tmp_git_repo)


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    return load_policy(DEFAULT_POLICY_DIR)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}})


def _install_runner_display_context(monkeypatch: MonkeyPatch) -> None:
    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(
        console=console,
        force_width=120,
    )
    monkeypatch.setattr(runner, "make_display_context", lambda **_kwargs: ctx)


def _stub_prompt_materialization(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "materialize_prepared_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "materialize_prompt_for_phase", lambda *args, **kwargs: "noop")
    monkeypatch.setattr(runner, "materialize_agent_prompt_if_needed", lambda *args, **kwargs: None)


def test_pipeline_final_cleanup_to_final_commit_end_to_end(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
    engine_internal_workspace: FsWorkspace,
) -> None:
    repo_root = Path(engine_internal_workspace.root)
    policy_bundle = _default_policy_bundle()

    initial_reflog_shas = _head_log_unique_shas(repo_root)

    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert (repo_root / rel_path).exists(), (
            f"Pre-condition failed: {rel_path!r} must exist before cleanup runs"
        )

    _write_commit_review_artifact(
        engine_internal_workspace,
        {
            "verdict": "approve",
            "actions": [
                {"action": "delete_file", "path": path} for path in ORIGINALLY_FAILING_PATHS
            ],
        },
    )
    invoker = CommitReviewAlwaysLoopbackInvoker(memory_workspace)

    saved_states: list[object] = []

    def fake_execute_effect(
        effect: Effect,
        _config: UnifiedConfig,
        _workspace_scope: WorkspaceScope,
    ) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invoker.invoke(effect.agent_name, effect.phase)
            return PipelineEvent.AGENT_SUCCESS
        if isinstance(effect, CommitEffect):
            return runner.execute_commit_effect(
                effect,
                create_commit,
                stage_all,
                repo_root,
            )
        if isinstance(effect, EmptyCommitEffect):
            return (
                PipelineEvent.AGENT_SUCCESS
                if effect.phase_role == "commit_review"
                else PipelineEvent.COMMIT_SUCCESS
            )
        msg = f"Unexpected effect type: {type(effect)!r}"
        raise AssertionError(msg)

    cleanup_phases = {
        "development_commit_review",
        "development_final_commit_review",
    }

    def fake_phase_event_after_agent_run(
        *,
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        policy_bundle: PolicyBundle,
        workspace: FsWorkspace,
        **_kwargs: object,
    ) -> PipelineEvent:
        if effect.phase in cleanup_phases:
            ctx = PhaseContext.model_construct(
                workspace=workspace,
                registry=AgentRegistry.from_config(config),
                chain_manager=ChainManager(policy_bundle.agents),
                pipeline_policy=policy_bundle.pipeline,
                agents_policy=policy_bundle.agents,
                artifacts_policy=policy_bundle.artifacts,
                config=config,
            )
            events = handle_phase(effect, ctx)
            return events[0] if events else PipelineEvent.AGENT_SUCCESS
        if effect.phase in ("development_commit", "development_final_commit"):
            _write_commit_message_artifacts(
                repo_root,
                "fix(commit): harden final-cleanup -> final-commit transition",
            )
            ctx = PhaseContext.model_construct(
                workspace=workspace,
                registry=AgentRegistry.from_config(config),
                chain_manager=ChainManager(policy_bundle.agents),
                pipeline_policy=policy_bundle.pipeline,
                agents_policy=policy_bundle.agents,
                artifacts_policy=policy_bundle.artifacts,
                config=config,
            )
            events = handle_phase(effect, ctx)
            return events[0] if events else PipelineEvent.AGENT_SUCCESS
        commit_event_for = getattr(invoker, "commit_event_for", None)
        if callable(commit_event_for):
            return commit_event_for(effect.phase)
        return PipelineEvent.AGENT_SUCCESS

    def capture_saved_state(state: object, *_args: object, **_kwargs: object) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: WorkspaceScope(repo_root))
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    _stub_prompt_materialization(monkeypatch)
    monkeypatch.setattr(runner, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(
        runner,
        "execute_effect_with_optional_display",
        lambda effect, config, workspace_scope, **_kwargs: fake_execute_effect(
            effect, config, workspace_scope
        ),
    )
    monkeypatch.setattr(runner, "phase_event_after_agent_run", fake_phase_event_after_agent_run)
    monkeypatch.setattr(runner.ckpt, "save", capture_saved_state)
    _install_runner_display_context(monkeypatch)

    result = runner.run(
        _config(),
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1},
    )

    assert result == 0, (
        f"Pipeline did not exit cleanly (rc={result}); the originally-failing "
        "paths must clean up via the runtime-artifact allowlist end-to-end."
    )

    assert invoker.count_for("development_final_commit_review") == 0
    assert invoker.count_for("development_final_commit") == 0

    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert not (repo_root / rel_path).exists(), (
            f"{rel_path!r} should have been deleted by the cleanup phase during the pipeline run"
        )

    gitignore_text = (repo_root / ".gitignore").read_text()
    for fragment in EXPECTED_GITIGNORE_FRAGMENTS:
        assert fragment in gitignore_text, (
            f"Expected {fragment!r} in auto-seeded .gitignore, got:\n{gitignore_text}"
        )

    exclude_text = (repo_root / ".git" / "info" / "exclude").read_text()
    for fragment in EXPECTED_GIT_EXCLUDE_FRAGMENTS:
        assert fragment in exclude_text, (
            f"Expected {fragment!r} in auto-seeded .git/info/exclude, got:\n{exclude_text}"
        )

    final_reflog_shas = _head_log_unique_shas(repo_root)
    new_reflog_shas = final_reflog_shas - initial_reflog_shas
    assert new_reflog_shas, (
        f"Repo.head.log shows NO new commit was created: pre-run SHAs="
        f"{sorted(initial_reflog_shas)}, post-run SHAs={sorted(final_reflog_shas)}. "
        "The execute_commit_effect call must actually stage and commit "
        "changes via real git so the final-cleanup -> final-commit "
        "transition is observed by the reflog."
    )

    new_commit_tree_paths = _newest_commit_tree_paths(repo_root)
    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert rel_path not in new_commit_tree_paths, (
            f"Deleted tracked file {rel_path!r} must NOT appear in the new "
            f"commit's tree; got paths: {sorted(new_commit_tree_paths)}"
        )

    final_state = saved_states[-1]
    assert getattr(final_state, "phase", None) == "complete", (
        f"Pipeline must terminate at 'complete', got {getattr(final_state, 'phase', None)!r}. "
        "Entering 'failed_terminal' would mean a PhaseFailureEvent was emitted."
    )
    observed_phases = [getattr(state, "phase", None) for state in saved_states]
    assert "development_final_commit_review" in observed_phases
    assert "development_final_commit" in observed_phases
    assert "failed_terminal" not in observed_phases
    assert getattr(final_state, "outer_progress", {}).get("iteration") == 1


def _newest_commit_tree_paths(repo_root: Path) -> set[str]:
    repo = Repo(repo_root)
    try:
        newest = repo.head.commit
        paths: set[str] = set()
        for blob in newest.tree.traverse():
            if blob.type == "blob":
                paths.add(blob.path)
        return paths
    finally:
        repo.close()


def _head_log_unique_shas(repo_root: Path) -> set[str]:
    repo = Repo(repo_root)
    try:
        return {entry.newhexsha for entry in repo.head.log()}
    finally:
        repo.close()
