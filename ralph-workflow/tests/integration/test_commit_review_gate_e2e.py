"""End-to-end integration tests for commit review safety gate, rollback, and resume.

The proofs drive the REAL git boundary (proposal seals, SHA-bound commit
effects, reducer-driven rollback and resume), so the file is registered in
``ralph.test_suites.REQUIRED_AUTO_INTEGRATE_E2E_FILES``: the registry
stamps every item with ``required_auto_integrate_e2e`` (the DEFAULT
marker expression selects it) and sanctions the real-Git crossings for
the test-policy audit. A module-level ``subprocess_e2e`` marker would
instead drop the file from static discovery and deselect it from the
default ``make test`` profile, so the coverage would rot unnoticed in the
opt-in lane.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from ralph.config.bootstrap import auto_seed_default_git_exclude, auto_seed_default_gitignore
from ralph.config.models import UnifiedConfig
from ralph.git.proposal_seal import ProposalPreflightError, preflight, rollback, seal_pending
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.phases.commit_review import (
    CommitReviewApproved,
    CommitReviewLoopback,
    CommitReviewRejected,
)
from ralph.phases.required_artifacts import read_validation_retry_hint, retry_hint_path
from ralph.pipeline.commit_executor import execute_commit_effect
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import CommitEffect, CommitReviewRejectEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.run_loop import resolve_initial_state
from ralph.pipeline.runner import execute_effect_with_optional_display, execute_pending_rollback
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.prompts.commit_review import render_commit_review_prompt
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy, PolicyBundle

pytestmark = [pytest.mark.timeout_seconds(10), pytest.mark.required_auto_integrate_e2e]

_DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"


def _bundle() -> PolicyBundle:
    return load_policy(_DEFAULTS_DIR)


def _policy() -> PipelinePolicy:
    return _bundle().pipeline


def _init_repo(tmp_path: Path) -> Repo:
    repo = Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Gate Test").release()
    repo.config_writer().set_value("user", "email", "gate@example.com").release()
    actor = Actor("Gate Test", "gate@example.com")
    tracked_file = tmp_path / "app.py"
    tracked_file.write_text("print('hello')", encoding="utf-8")
    auto_seed_default_git_exclude(tmp_path)
    repo.index.add(["app.py"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    return repo


@pytest.fixture(scope="module")
def _repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the template repository once for the whole module.

    The template holds the same identity, the same tracked ``app.py`` and the
    same ``initial commit`` every proof in this file starts from, so a
    private copy of it is the starting state each proof needs.
    """
    template_root = tmp_path_factory.mktemp("commit-review-gate-template")
    _init_repo(template_root)
    return template_root


@pytest.fixture
def init_repo(_repo_template: Path) -> Callable[[Path], Repo]:
    """Return a callable that materializes a private copy of the template repo."""

    def _materialize(path: Path) -> Repo:
        shutil.copytree(_repo_template, path, dirs_exist_ok=True)
        return Repo(path)

    return _materialize


def _create_commit_message_artifact(repo_root: Path, message: str = "feat: good change") -> Path:
    artifact = repo_root / ".agent" / "artifacts" / "commit_message.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        f"---\ntype: commit\nsubject: {message}\n---\n",
        encoding="utf-8",
    )
    return artifact


def _bundle_with_retention(retention: int) -> PolicyBundle:
    """Return the bundled default policy with ``proposal_retention`` overridden."""
    bundle = _bundle()
    commit_review = bundle.pipeline.commit_review.model_copy(
        update={"proposal_retention": retention}
    )
    pipeline = bundle.pipeline.model_copy(update={"commit_review": commit_review})
    return bundle.model_copy(update={"pipeline": pipeline})


def test_commit_review_gate_approve_creates_sha_bound_commit_and_retains_the_proposal(
    tmp_path: Path,
    init_repo: Callable[[Path], Repo],
) -> None:
    """Approval routes to commit with SHA-bound staging and creates the commit."""
    policy = _policy()
    repo = init_repo(tmp_path)

    # 1. Developer modifies code
    code_file = tmp_path / "app.py"
    code_file.write_text("print('hello, approved world!')", encoding="utf-8")

    # 2. Stage pending changes into a proposal seal
    seal = seal_pending(tmp_path, run_id="run-100", iteration="0")
    assert seal.tree_sha is not None
    preflight(tmp_path, seal.ref)

    # 3. Reviewer emits CommitReviewApproved
    state = PipelineState(
        phase="development_commit_review",
        commit_review_rejections=0,
    )
    event = CommitReviewApproved(tree_sha=seal.tree_sha)
    next_state, _ = reduce(state, event, policy)

    assert next_state.phase == "development_commit"
    assert next_state.approved_tree_sha == seal.tree_sha

    # 4. Commit effect is executed with expected_tree_sha
    msg_path = _create_commit_message_artifact(tmp_path, "feat: approved work")
    commit_effect = CommitEffect(
        message_file=str(msg_path),
        expected_tree_sha=next_state.approved_tree_sha,
    )
    commit_event = execute_commit_effect(
        commit_effect,
        tmp_path,
        branch=repo.active_branch.name,
    )
    assert commit_event == PipelineEvent.COMMIT_SUCCESS

    # Verify head moved to the new commit
    repo = Repo(tmp_path)
    assert repo.head.commit.message.splitlines()[0] == "feat: approved work"

    # The post-commit proposal sweep KEEPS the most recent sealed proposals:
    # ``commit_review.proposal_retention`` bounds how many refs the sweep
    # retains and the sweep never deletes a rejected iteration's ref (the
    # documented recovery is ``git checkout <ref> -- .``). This proposal's
    # tree became the commit, so it is not a rejected ref, and the bundled
    # retention budget is not exhausted: the sweep retains the ref and it
    # still resolves.
    preflight(tmp_path, seal.ref)


def test_commit_review_gate_prunes_the_proposals_outside_the_retention_bound(
    tmp_path: Path,
    init_repo: Callable[[Path], Repo],
) -> None:
    """A successful commit prunes the proposal refs the retention bound excludes."""
    repo = init_repo(tmp_path)
    initial_sha = repo.head.commit.hexsha

    # The developer changes the tracked file; the sealed proposal points at
    # the tree the approved commit is about to create.
    code_file = tmp_path / "app.py"
    code_file.write_text("print('hello, retained world!')", encoding="utf-8")
    seal = seal_pending(tmp_path, run_id="run-200", iteration="1")
    assert seal.tree_sha is not None

    # An older approved iteration's proposal ref points at a tree that DID
    # become a commit, so the sweep does not protect it as a rejected ref.
    older_ref = "refs/ralph/proposals/run-200/0"
    repo.git.update_ref(older_ref, initial_sha)

    msg_path = _create_commit_message_artifact(tmp_path, "feat: bounded sweep")
    commit_effect = CommitEffect(
        message_file=str(msg_path),
        expected_tree_sha=seal.tree_sha,
    )
    commit_event = execute_commit_effect(
        commit_effect,
        tmp_path,
        branch=repo.active_branch.name,
        policy_bundle=_bundle_with_retention(1),
    )
    assert commit_event == PipelineEvent.COMMIT_SUCCESS

    # The newest proposal ref is inside the retention bound and survives.
    preflight(tmp_path, seal.ref)
    # The older one is outside it and the sweep deletes it.
    with pytest.raises(ProposalPreflightError):
        preflight(tmp_path, older_ref)


def test_commit_review_gate_sha_mismatch_fails_commit(
    tmp_path: Path, init_repo: Callable[[Path], Repo]
) -> None:
    """If working tree / index has changed since approval, commit effect is rejected."""
    init_repo(tmp_path)

    code_file = tmp_path / "app.py"
    code_file.write_text("print('hello, tampered world!')", encoding="utf-8")

    msg_path = _create_commit_message_artifact(tmp_path, "feat: tampered work")
    commit_effect = CommitEffect(
        message_file=str(msg_path),
        expected_tree_sha="0123456789abcdef0123456789abcdef01234567",
    )
    commit_event = execute_commit_effect(commit_effect, tmp_path)
    assert commit_event == PipelineEvent.COMMIT_FAILURE


def test_commit_review_gate_reject_executes_rollback_and_preserves_context(
    tmp_path: Path, init_repo: Callable[[Path], Repo]
) -> None:
    """Rejection rolls back working tree to HEAD, preserves proposal ref, and stores hint."""
    policy = _policy()
    repo = init_repo(tmp_path)
    head_sha = repo.head.commit.hexsha

    # Developer creates a good change and a bad file
    bad_file = tmp_path / "corrupt_data.bin"
    bad_file.write_bytes(b"\x00\xff" * 100)
    code_file = tmp_path / "app.py"
    code_file.write_text("print('modified')", encoding="utf-8")

    # Seal proposal
    seal = seal_pending(tmp_path, run_id="run-101", iteration="0")
    preflight(tmp_path, seal.ref)

    # Reviewer emits CommitReviewRejected
    state = PipelineState(
        phase="development_commit_review",
        commit_review_rejections=0,
    )
    reject_event = CommitReviewRejected(
        reason="corrupt_data.bin contains binary payload",
        ref=seal.ref,
        hard=True,
    )
    next_state, _ = reduce(state, reject_event, policy)

    # Reducer asserts
    assert next_state.phase == "development"
    assert next_state.commit_review_rejections == 1
    assert next_state.rollback_pending is True
    assert next_state.rollback_ref == seal.ref

    # Execute rollback (simulating runner step)
    assert next_state.rollback_ref is not None
    rollback(tmp_path, next_state.rollback_ref)

    # Working tree restored: bad file gone, app.py restored to initial commit
    assert not bad_file.exists()
    assert code_file.read_text(encoding="utf-8") == "print('hello')"
    repo = Repo(tmp_path)
    assert repo.head.commit.hexsha == head_sha

    # Proposal ref preserved for debugging/recovery
    preflight(tmp_path, seal.ref)

    # Store retry hint for next development prompt
    hint_file = tmp_path / retry_hint_path("development", pipeline_policy=policy)
    hint_file.parent.mkdir(parents=True, exist_ok=True)
    hint_file.write_text(
        f"Commit review rejected: {next_state.last_review_rejection_reason}\nRef: {next_state.rollback_ref}",
        encoding="utf-8",
    )

    # Verify development prompt can read the hint
    hint_content = read_validation_retry_hint(tmp_path, "development")
    assert "corrupt_data.bin contains binary payload" in hint_content
    assert seal.ref in hint_content


def test_the_rejection_reason_and_proposal_ref_reach_the_next_developer_prompt(
    tmp_path: Path,
    init_repo: Callable[[Path], Repo],
) -> None:
    """The engine's own rejection context reaches the next developer prompt.

    The rollback records the rejection context (the reason and the proposal
    ref) for the next developer iteration. The proof drives the PRODUCTION
    writer through the public rollback executor and then renders the next
    developer prompt, so the two halves cannot drift apart: a change to the
    recorded format that the prompt no longer understands fails here.
    """
    policy = _policy()
    init_repo(tmp_path)

    # The rejected iteration: a modified tracked file and an added file.
    (tmp_path / "app.py").write_text("print('modified')", encoding="utf-8")
    (tmp_path / "corrupt_data.bin").write_bytes(b"\x00\xff" * 100)

    # The rejected state is SEALED under a proposal ref before the rollback.
    seal = seal_pending(tmp_path, run_id="run-104", iteration="0")
    rejected_state, _ = reduce(
        PipelineState(phase="development_commit_review", commit_review_rejections=0),
        CommitReviewRejected(
            reason="corrupt_data.bin contains binary payload",
            ref=seal.ref,
            hard=True,
        ),
        policy,
    )
    assert rejected_state.rollback_pending is True
    assert rejected_state.rollback_ref == seal.ref

    # The engine rolls the tree back and records the rejection context.
    rolled_back = execute_pending_rollback(rejected_state, tmp_path, policy)

    # The tree is rolled back ...
    assert rolled_back.rollback_pending is False
    assert not (tmp_path / "corrupt_data.bin").exists()
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "print('hello')"
    # ... and the proposal ref is KEPT for the rejection.
    preflight(tmp_path, seal.ref)

    # The next developer prompt carries the reason and the proposal ref.
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(".agent/PLAN.md", "# Plan\n1. Do the work\n")
    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(artifacts_policy=_bundle().artifacts),
    )
    rendered = workspace.read(prompt_path)
    assert "PREVIOUS COMMIT REVIEW REJECTION" in rendered
    assert "corrupt_data.bin contains binary payload" in rendered
    assert seal.ref in rendered
    assert f"git checkout {seal.ref} -- ." in rendered


def test_commit_review_gate_counter_exhaustion_routes_to_failure(
    tmp_path: Path, init_repo: Callable[[Path], Repo]
) -> None:
    """When rejection limit is reached, route to failed_terminal and preserve proposal ref."""
    policy = _policy()
    init_repo(tmp_path)

    bad_file = tmp_path / "bad.txt"
    bad_file.write_text("bad", encoding="utf-8")
    seal = seal_pending(tmp_path, run_id="run-102", iteration="1")

    # State already at 2 rejections (policy default max is 2)
    state = PipelineState(
        phase="development_commit_review",
        commit_review_rejections=2,
    )
    reject_event = CommitReviewRejected(
        reason="Repeated damage after retry",
        ref=seal.ref,
    )
    next_state, _ = reduce(state, reject_event, policy)

    assert next_state.phase == policy.recovery.failed_route
    assert next_state.commit_review_rejections == 3
    assert next_state.rollback_pending is False
    assert "limit exceeded" in (next_state.last_error or "")

    # Proposal ref must still exist (preserved for inspection)
    preflight(tmp_path, seal.ref)


def test_stale_review_loopback_preserves_work_and_rejection_budget(
    tmp_path: Path, init_repo: Callable[[Path], Repo]
) -> None:
    """Stale review reroutes to review without rollback or rejection accounting."""
    policy = _policy()
    repo = init_repo(tmp_path)
    head_sha = repo.head.commit.hexsha
    pending = tmp_path / "app.py"
    pending.write_text("print('pending')", encoding="utf-8")
    seal = seal_pending(tmp_path, run_id="run-stale", iteration="0")
    artifact_sha = "0" * 40
    hint = (
        f"Stale commit review: artifact tree SHA {artifact_sha!r} does not match "
        f"current sealed proposal tree SHA {seal.tree_sha!r}; re-review the current tree."
    )

    next_state, _ = reduce(
        PipelineState(
            phase="development_commit_review",
            commit_review_rejections=1,
        ),
        CommitReviewLoopback(hint=hint),
        policy,
    )

    assert next_state.phase == "development_commit_review"
    assert next_state.last_error == hint
    assert artifact_sha in (next_state.last_error or "")
    assert seal.tree_sha in (next_state.last_error or "")
    assert next_state.commit_review_rejections == 1
    assert next_state.rollback_pending is False
    assert next_state.rollback_ref is None
    assert next_state.get_loop_iteration("commit_review_iteration") == 1
    assert repo.head.commit.hexsha == head_sha
    assert pending.read_text(encoding="utf-8") == "print('pending')"


def test_commit_review_resume_handles_pending_rollback(
    tmp_path: Path, init_repo: Callable[[Path], Repo]
) -> None:
    """On resume, if rollback_pending is True, rollback is executed first and flag cleared."""
    _ = init_repo(tmp_path)

    stray_file = tmp_path / "unwanted.tmp"
    stray_file.write_text("should be rolled back", encoding="utf-8")

    seal = seal_pending(tmp_path, run_id="run-103", iteration="0")
    preflight(tmp_path, seal.ref)

    # Simulated checkpoint before crash: rollback_pending=True
    crashed_state = PipelineState(
        phase="development",
        rollback_pending=True,
        rollback_ref=seal.ref,
        commit_review_rejections=1,
    )

    # Resume via _resolve_initial_state
    config = UnifiedConfig()
    bundle = _bundle()

    resumed = resolve_initial_state(
        config=config,
        policy_bundle=bundle,
        initial_state=crashed_state,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
        workspace_root=tmp_path,
    )

    # Rollback must have completed: unwanted.tmp removed
    assert not stray_file.exists()
    assert resumed.rollback_pending is False
    assert resumed.rollback_ref is None


def _seed_git_repo(repo_path: Path, file_count: int = 60) -> list[str]:
    repo = Repo.init(repo_path)
    auto_seed_default_gitignore(repo_path)
    auto_seed_default_git_exclude(repo_path)
    tracked: list[str] = []
    for i in range(file_count):
        name = f"mod_{i:03d}.py"
        (repo_path / name).write_text(f"X = {i}\n", encoding="utf-8")
        tracked.append(name)
    repo.git.add(A=True)
    repo.index.commit("initial commit")
    return tracked


def test_whole_repo_deletion_pipeline_preflight_rejects_without_agent(tmp_path: Path) -> None:
    """Stage 1 mass deletion emits CommitReviewRejectEffect directly before agent review."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    tracked = _seed_git_repo(repo_root, 60)

    for name in tracked:
        (repo_root / name).unlink()

    policy_bundle = load_policy(_DEFAULTS_DIR)
    state = PipelineState(phase="development_commit_review")
    scope = WorkspaceScope(root=repo_root, allowed_roots=(repo_root,))

    effect = determine_effect_from_policy(state, policy_bundle, workspace_scope=scope)

    assert isinstance(effect, CommitReviewRejectEffect)
    assert effect.hard is True
    assert "Mass deletion" in effect.reason
    assert effect.ref.startswith("refs/ralph/proposals/")

    config = UnifiedConfig()
    event = execute_effect_with_optional_display(effect, config, scope)
    assert isinstance(event, CommitReviewRejected)
    assert event.hard is True
    assert event.ref == effect.ref

    next_state, _ = reduce(state, event, policy_bundle.pipeline)
    assert next_state.rollback_pending is True
    assert next_state.rollback_ref == effect.ref
    assert next_state.phase == "development"
    assert next_state.commit_review_rejections == 1


def test_prompt_materialization_first_pass_carries_stage1_evidence(tmp_path: Path) -> None:
    """Stage 1 context is persisted before prompt materialization so first pass has real evidence."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_git_repo(repo_root, 5)

    (repo_root / "mod_000.py").write_text("X = 999\n", encoding="utf-8")

    policy_bundle = load_policy(_DEFAULTS_DIR)
    state = PipelineState(phase="development_commit_review")
    scope = WorkspaceScope(root=repo_root, allowed_roots=(repo_root,))

    effect = determine_effect_from_policy(state, policy_bundle, workspace_scope=scope)
    assert isinstance(effect, InvokeAgentEffect)

    context_file = repo_root / ".agent" / "tmp" / "commit_review_context.json"
    assert context_file.is_file()

    raw_data: object = json.loads(context_file.read_text(encoding="utf-8"))
    assert isinstance(raw_data, dict)
    assert "proposal_ref" in raw_data
    proposal_ref = raw_data["proposal_ref"]
    assert isinstance(proposal_ref, str)
    assert proposal_ref.startswith("refs/ralph/proposals/")
    assert "tree_sha" in raw_data
    tree_sha = raw_data["tree_sha"]
    assert isinstance(tree_sha, str)
    assert len(tree_sha) == 40

    tmpl_ctx = TemplateContext.default(repo_root)
    prompt = render_commit_review_prompt(
        workspace_root=repo_root,
        phase="development_commit_review",
        worker_namespace=None,
        prompt_content=None,
        product_criteria_path=str(repo_root / ".agent" / "PRODUCT_CRITERIA.md"),
        template_name="commit_review.jinja",
        tmpl_ctx=tmpl_ctx,
        session_caps=SessionCapabilities.defaults_for_drain(
            SessionDrain.COMMIT, tool_name_prefix="ralph"
        ),
        pipeline_policy=policy_bundle.pipeline,
    )

    assert proposal_ref in prompt
    assert tree_sha in prompt
