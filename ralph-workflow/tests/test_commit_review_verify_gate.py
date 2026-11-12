from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.agents.chain import ChainManager
from ralph.config.bootstrap import auto_seed_default_git_exclude, auto_seed_default_gitignore
from ralph.git.proposal_seal import seal_pending
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import COMMIT_REVIEW_SPEC
from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.phases import PhaseContext
from ralph.phases._commit_review_catalog import (
    PROMPT_DELETE_FILE_ELIGIBLE_EXAMPLES,
    PROMPT_DELETE_FILE_INELIGIBLE_EXAMPLES,
    render_delete_decision_rules_markdown,
)
from ralph.phases.commit_review import (
    CommitReviewApproved,
    CommitReviewGateFailed,
    CommitReviewLoopback,
    CommitReviewRejected,
    handle_commit_review_phase,
    run_commit_review_stage1_preflight,
)
from ralph.pipeline.commit_executor import (
    execute_commit_effect,
    get_last_commit_error,
    set_last_commit_error,
)
from ralph.pipeline.effects import CommitEffect, InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.handoffs import resolve_next_phase
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    CommitReviewPolicy,
)
from ralph.prompts.commit_review import render_commit_review_prompt
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.recovery.classifier import FailureCategory
from ralph.test_suites import REQUIRED_AUTO_INTEGRATE_E2E_FILES
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Generator

COMMIT_REVIEW_ARTIFACT_PATH = ".agent/artifacts/commit_review.md"
_INVARIANTS_FILE = "tests/test_commit_review_invariants.py"
_VERIFY_GATE_FILE = "tests/test_commit_review_verify_gate.py"
_PIPELINE_COMMIT_E2E_FILE = "tests/integration/test_pipeline_commit_review_to_commit_e2e.py"
_DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"

pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.required_auto_integrate_e2e]

_REPO_CACHE: dict[Path, Repo] = {}


def _repo(root: Path) -> Repo:
    repo = _REPO_CACHE.get(root)
    if repo is None:
        repo = Repo(root)
        _REPO_CACHE[root] = repo
    return repo


@pytest.fixture(autouse=True)
def _close_cached_repos() -> Generator[None, None, None]:
    yield
    for repo in _REPO_CACHE.values():
        repo.close()
    _REPO_CACHE.clear()


def _resolve_verdict(content: dict[str, object]) -> str:
    verdict = content.get("verdict")
    if verdict is not None:
        return str(verdict)
    return "approve"


def _resolve_tree_sha(workspace: FsWorkspace, content: dict[str, object]) -> str | None:
    if "tree_sha" in content:
        return str(content["tree_sha"])
    repo_path = Path(workspace.root)
    if not (repo_path / ".git").exists():
        return None
    try:
        from ralph.git.commit_review import untrack_engine_internal_files
        from ralph.phases.commit_review import is_agent_internal_path

        untrack_engine_internal_files(repo_path, is_agent_internal_path)
        auto_seed_default_gitignore(repo_path)
        auto_seed_default_git_exclude(repo_path)
        return seal_pending(repo_path, "run", 1).tree_sha
    except Exception:
        return None


def _write_commit_review_artifact(
    workspace: FsWorkspace,
    content: dict[str, object],
) -> None:
    verdict = _resolve_verdict(content)
    tree_sha = _resolve_tree_sha(workspace, content)

    lines = [
        "---",
        "type: commit_review",
        f"verdict: {verdict}",
    ]
    if tree_sha is not None:
        lines.append(f"tree_sha: {tree_sha}")
    lines.append("---")

    actions = content.get("actions", [])
    if actions:
        lines.append("## Actions")
        assert isinstance(actions, list)
        for index, action in enumerate(actions, start=1):
            assert isinstance(action, dict)
            action_name = action["action"]
            value = action.get("path", action.get("pattern"))
            lines.append(f"- [A{index}] {action_name} | {value}")

    reason = content.get("reason")
    if reason:
        lines.append("## Reason")
        if isinstance(reason, list):
            for index, item in enumerate(reason, start=1):
                lines.append(f"- [R{index}] {item}")
        else:
            lines.append(f"- [R1] {reason}")

    path = Path(workspace.root) / COMMIT_REVIEW_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ctx(
    workspace: FsWorkspace,
    *,
    chain_manager: object | None = None,
    pipeline_policy: object | None = None,
) -> PhaseContext:
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object() if chain_manager is None else chain_manager,
        pipeline_policy=object() if pipeline_policy is None else pipeline_policy,
        artifacts_policy=object(),
        agents_policy=object(),
    )


def _invoke(phase: str = "development_commit_review") -> InvokeAgentEffect:
    return InvokeAgentEffect(agent_name="dev", phase=phase, prompt_file="cleanup.txt")


def _run(
    workspace: FsWorkspace,
    phase: str = "development_commit_review",
    *,
    chain_manager: object | None = None,
    pipeline_policy: object | None = None,
) -> list[object]:
    return handle_commit_review_phase(
        _invoke(phase),
        _ctx(workspace, chain_manager=chain_manager, pipeline_policy=pipeline_policy),
    )


def _stage1_soft_finding_categories(
    repo_root: Path,
    *,
    pipeline_policy: object | None = None,
    phase: str = "development_commit_review",
) -> tuple[str, ...]:
    policy = (
        pipeline_policy
        if pipeline_policy is not None
        else load_policy(_DEFAULT_POLICY_DIR).pipeline
    )
    outcome = run_commit_review_stage1_preflight(repo_root, "run", "1", policy, phase)
    assert not isinstance(outcome, (CommitReviewRejected, CommitReviewGateFailed)), (
        f"Stage 1 must not reject this diff, got: {outcome!r}"
    )
    _seal, _diff_entries, report, _base = outcome
    return tuple(finding.category for finding in report.soft)


def _render_prompt(tmp_git_repo: Path, phase: str = "development_commit_review") -> str:
    return render_commit_review_prompt(
        phase=phase,
        workspace_root=tmp_git_repo,
        worker_namespace=None,
        prompt_content="criteria",
        product_criteria_path=str(tmp_git_repo / ".agent" / "PRODUCT_CRITERIA.md"),
        template_name="commit_review.jinja",
        tmpl_ctx=TemplateContext.default(tmp_git_repo),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
    )


def test_well_formed_leftover_is_missing_on_second_handle_without_resubmit(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    junk = tmp_git_repo / "session-output.txt"
    junk.write_text("tmp\n", encoding="utf-8")
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "actions": [{"action": "delete_file", "path": "session-output.txt"}],
        },
    )
    first = _run(workspace)
    assert len(first) == 1 and isinstance(first[0], CommitReviewApproved)
    assert not junk.exists()
    second = _run(workspace)
    assert isinstance(second[0], PhaseFailureEvent)
    assert second[0].failure_category is FailureCategory.ARTIFACT_VALIDATION


def test_resubmitted_duplicate_batch_remains_idempotent(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    junk = tmp_git_repo / "session-output.txt"
    junk.write_text("tmp\n", encoding="utf-8")
    payload: dict[str, object] = {
        "verdict": "approve",
        "actions": [
            {"action": "delete_file", "path": "session-output.txt"},
            {"action": "add_to_gitignore", "pattern": "*.exe"},
        ],
    }
    _write_commit_review_artifact(workspace, payload)
    first = _run(workspace)
    _write_commit_review_artifact(workspace, payload)
    second = _run(workspace)
    assert len(first) == 1 and isinstance(first[0], CommitReviewApproved)
    assert len(second) == 1 and isinstance(second[0], CommitReviewApproved)
    assert not junk.exists()
    assert "*.exe" in workspace.read(".gitignore")


def test_missing_artifact_bound_survives_highest_priority_reselection(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    agents_policy = AgentsPolicy(
        agent_chains={
            "commit": AgentChainConfig(agents=["sonnet", "fallback"]),
        },
        agent_drains={
            "commit": AgentDrainConfig(chain="commit"),
        },
    )
    chain_manager = ChainManager(agents_policy)
    events: list[object] = []
    for _ in range(4):
        chain = chain_manager.chain_for_drain("commit")
        assert chain.agents[0] == "sonnet"
        events.append(_run(workspace, chain_manager=chain_manager)[0])
    assert isinstance(events[0], PhaseFailureEvent)
    assert events[0].failure_category is FailureCategory.ARTIFACT_VALIDATION
    assert isinstance(events[-1], CommitReviewGateFailed)


def test_cleanup_failure_routes_to_commit_not_failed_terminal() -> None:
    pipeline = load_policy(_DEFAULT_POLICY_DIR).pipeline
    assert resolve_next_phase("development_commit_review", "failure", pipeline) == (
        "failed_terminal"
    )
    assert resolve_next_phase("development_final_commit_review", "failure", pipeline) == (
        "failed_terminal"
    )
    phase_cr = pipeline.phases["development_commit_review"]
    assert phase_cr.transitions.on_reject == "development"
    phase_fcr = pipeline.phases["development_final_commit_review"]
    assert phase_fcr.transitions.on_reject == "development"


def test_invariants_module_is_required_auto_integrate() -> None:
    assert _INVARIANTS_FILE in REQUIRED_AUTO_INTEGRATE_E2E_FILES
    assert _VERIFY_GATE_FILE in REQUIRED_AUTO_INTEGRATE_E2E_FILES
    assert _PIPELINE_COMMIT_E2E_FILE in REQUIRED_AUTO_INTEGRATE_E2E_FILES


def test_next_prompt_contains_declined_and_apply_failed_paths(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    workspace.write("app.py", "x = 1\n")
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "actions": [
                {"action": "delete_file", "path": "app.py"},
                {"action": "add_to_gitignore", "pattern": "*.bin"},
            ],
        },
    )

    def _boom(_root: Path, _patterns: list[str]) -> None:
        raise OSError("append failed")

    monkeypatch.setattr("ralph.phases.commit_review.append_to_gitignore", _boom)
    _run(workspace, phase="development_final_commit_review")
    rendered = _render_prompt(tmp_git_repo, phase="development_final_commit_review")
    assert "app.py" in rendered
    assert "*.bin" in rendered
    lowered = rendered.lower()
    assert "declined" in lowered or "rejected" in lowered
    assert "failed" in lowered


def test_mixed_declined_delete_and_throwing_ignore_is_not_applied_work(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    workspace.write("lib.py", "pass\n")
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "actions": [
                {"action": "delete_file", "path": "lib.py"},
                {"action": "add_to_gitignore", "pattern": "*.o"},
            ],
        },
    )

    def _boom(_root: Path, _patterns: list[str]) -> None:
        raise OSError("ignore failed")

    monkeypatch.setattr("ralph.phases.commit_review.append_to_gitignore", _boom)
    result = _run(workspace)
    gitignore = tmp_git_repo / ".gitignore"
    ignore_text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    assert "*.o" not in ignore_text
    assert (tmp_git_repo / "lib.py").exists()
    assert PipelineEvent.AGENT_SUCCESS not in result
    assert isinstance(result[0], CommitReviewLoopback)
    assert "*.o" in result[0].hint


def test_untracked_completion_report_md_deletes_and_tracked_is_declined(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    report = tmp_git_repo / "completion_report.md"
    report.write_text("# generated\n", encoding="utf-8")
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "actions": [{"action": "delete_file", "path": "completion_report.md"}],
        },
    )
    first = _run(workspace)
    assert len(first) == 1 and isinstance(first[0], CommitReviewApproved)
    assert not report.exists()

    report.write_text("# tracked\n", encoding="utf-8")
    repo = _repo(tmp_git_repo)
    repo.index.add(["completion_report.md"])
    repo.index.commit("track completion report")
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "actions": [{"action": "delete_file", "path": "completion_report.md"}],
        },
    )
    second = _run(workspace)
    assert len(second) == 1 and isinstance(second[0], CommitReviewLoopback)
    assert report.exists()


def test_prompt_named_deletable_classes_match_engine_boundary(tmp_git_repo: Path) -> None:
    rules = render_delete_decision_rules_markdown()
    assert "`delete_file`" in rules
    lowered = rules.lower()
    assert "completion" in lowered or "report" in lowered
    assert "lockfile" in lowered or "package-lock.json" in rules
    package_root = Path(__file__).resolve().parents[1]
    jinja = FsWorkspace(package_root).read(
        "ralph/prompts/templates/commit_review.jinja",
    )
    assert "DELETE_DECISION_RULES" in jinja
    assert "LAST_RETRY_ERROR" in jinja
    workspace = FsWorkspace(tmp_git_repo)
    workspace.write("scratch.tmp", "x\n")
    workspace.write("app.py", "print(1)\n")
    workspace.write("package-lock.json", "{}")
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "actions": [
                {"action": "delete_file", "path": "scratch.tmp"},
                {"action": "delete_file", "path": "app.py"},
                {"action": "delete_file", "path": "package-lock.json"},
                {"action": "delete_file", "path": "../outside.txt"},
            ],
        },
    )
    result = _run(workspace)
    assert len(result) == 1 and isinstance(result[0], CommitReviewLoopback)
    assert not (tmp_git_repo / "scratch.tmp").exists()
    assert (tmp_git_repo / "app.py").exists()
    assert (tmp_git_repo / "package-lock.json").exists()
    for example in PROMPT_DELETE_FILE_ELIGIBLE_EXAMPLES:
        assert f"`{example}`" in rules
        target = tmp_git_repo / example
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("generated\n", encoding="utf-8")
        _write_commit_review_artifact(
            workspace,
            {
                "verdict": "approve",
                "actions": [{"action": "delete_file", "path": example}],
            },
        )
        eligible_result = _run(workspace)
        assert len(eligible_result) == 1 and isinstance(eligible_result[0], CommitReviewApproved)
        assert not target.exists()
    for example in PROMPT_DELETE_FILE_INELIGIBLE_EXAMPLES:
        assert f"`{example}`" in rules
        if example.startswith("../"):
            target = tmp_git_repo.parent / example.removeprefix("../")
        else:
            target = tmp_git_repo / example
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("keep\n", encoding="utf-8")
        _write_commit_review_artifact(
            workspace,
            {
                "verdict": "approve",
                "actions": [{"action": "delete_file", "path": example}],
            },
        )
        ineligible_result = _run(workspace)
        assert len(ineligible_result) == 1 and isinstance(
            ineligible_result[0], CommitReviewLoopback
        )
        assert target.exists()


def _head_sha(repo_root: Path) -> str:
    return str(_repo(repo_root).head.commit.hexsha)


def _commit_count(repo_root: Path) -> int:
    head = _repo(repo_root).head.commit
    return sum(1 for _ in head.traverse()) + 1


def _track_and_commit(repo_root: Path, paths: list[str]) -> None:
    repo = _repo(repo_root)
    repo.index.add(paths)
    repo.index.commit(f"track {', '.join(paths)}")


def _seed_engine_defaults(repo_root: Path) -> None:
    auto_seed_default_gitignore(repo_root)
    auto_seed_default_git_exclude(repo_root)


def test_hard_tripwire_rejects_without_an_agent_and_commits_nothing(
    tmp_git_repo: Path,
) -> None:
    agents_md = tmp_git_repo / "AGENTS.md"
    agents_md.write_text("# Agents Policy\n", encoding="utf-8")
    fillers = [f"filler_{index:02d}.py" for index in range(12)]
    for name in fillers:
        filler_path = tmp_git_repo / name
        filler_path.write_text("VALUE = 1\n", encoding="utf-8")
    _seed_engine_defaults(tmp_git_repo)
    _track_and_commit(tmp_git_repo, ["AGENTS.md", *fillers])

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    agents_md.unlink()

    workspace = FsWorkspace(tmp_git_repo)
    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewRejected)
    assert event.hard is True
    assert "Protected document deleted" in event.reason
    assert "AGENTS.md" in event.reason
    assert event.ref.startswith("refs/ralph/proposals/")
    assert not (tmp_git_repo / COMMIT_REVIEW_ARTIFACT_PATH).exists()
    assert not any(isinstance(item, PhaseFailureEvent) for item in result)
    assert _head_sha(tmp_git_repo) == head_before
    assert _commit_count(tmp_git_repo) == commits_before
    assert not agents_md.exists()


def test_whole_repo_deletion_is_rejected_by_the_hard_tripwire_without_an_agent(
    tmp_git_repo: Path,
) -> None:
    tracked: list[str] = []
    for index in range(60):
        name = f"module_{index:03d}.py"
        module_path = tmp_git_repo / name
        module_path.write_text(f"VALUE = {index}\n", encoding="utf-8")
        tracked.append(name)
    _seed_engine_defaults(tmp_git_repo)
    _track_and_commit(tmp_git_repo, tracked)

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    for name in tracked:
        (tmp_git_repo / name).unlink()

    workspace = FsWorkspace(tmp_git_repo)
    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewRejected)
    assert event.hard is True
    assert "Mass deletion" in event.reason
    assert event.ref.startswith("refs/ralph/proposals/")
    assert not (tmp_git_repo / COMMIT_REVIEW_ARTIFACT_PATH).exists()
    assert not any(isinstance(item, PhaseFailureEvent) for item in result)
    assert _head_sha(tmp_git_repo) == head_before
    assert _commit_count(tmp_git_repo) == commits_before


def test_unverifiable_reject_citation_is_a_reviewer_malfunction_not_a_rollback(
    tmp_git_repo: Path,
) -> None:
    app_file = tmp_git_repo / "src" / "app.py"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text("print('hello')\n", encoding="utf-8")

    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "reject",
            "reason": "Cites absent_file.txt which is not part of the pending diff",
        },
    )

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewGateFailed)
    assert "citations could not be verified" in event.detail
    assert app_file.read_text(encoding="utf-8") == "print('hello')\n"
    assert _head_sha(tmp_git_repo) == head_before
    assert _commit_count(tmp_git_repo) == commits_before


def test_verifiable_reject_citation_is_a_rejection_not_a_reviewer_malfunction(
    tmp_git_repo: Path,
) -> None:
    app_file = tmp_git_repo / "src" / "app.py"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text("print('hello')\n", encoding="utf-8")

    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "reject",
            "reason": "src/app.py was corrupted by the change",
        },
    )

    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewRejected)
    assert event.hard is False
    assert "src/app.py" in event.reason


def test_stale_tree_sha_in_the_artifact_loops_back_for_rereview(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _seed_engine_defaults(tmp_git_repo)
    artifact_sha = "0" * 40
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "tree_sha": artifact_sha,
        },
    )
    current_sha = seal_pending(tmp_git_repo, "run", 1).tree_sha

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewLoopback)
    assert "stale" in event.hint.lower()
    assert artifact_sha in event.hint
    assert current_sha in event.hint
    assert not workspace.exists(COMMIT_REVIEW_ARTIFACT_PATH)
    assert _head_sha(tmp_git_repo) == head_before
    assert _commit_count(tmp_git_repo) == commits_before


def test_mismatched_tree_sha_at_commit_is_refused_without_creating_a_commit(
    tmp_git_repo: Path,
) -> None:
    readme = tmp_git_repo / "README.md"
    readme.write_text("approved change\n", encoding="utf-8")

    approved = seal_pending(tmp_git_repo, "run", 1)

    repo = Repo(tmp_git_repo)
    try:
        repo.git.add("-A")
        assert repo.index.write_tree().hexsha == approved.tree_sha
    finally:
        repo.close()

    tampered_path = tmp_git_repo / "tampered.txt"
    tampered_path.write_text("tampered after approval\n", encoding="utf-8")

    message_path = tmp_git_repo / ".agent" / "artifacts" / "commit_message.md"
    message_path.parent.mkdir(parents=True, exist_ok=True)
    message_path.write_text(
        "---\ntype: commit\nsubject: feat: tampered\n---\n",
        encoding="utf-8",
    )

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    effect = CommitEffect(
        message_file=str(message_path),
        expected_tree_sha=approved.tree_sha,
    )
    try:
        event = execute_commit_effect(effect, tmp_git_repo)

        assert event == PipelineEvent.COMMIT_FAILURE
        diagnostic = get_last_commit_error()
        assert diagnostic is not None
        assert "does not match approved tree SHA" in diagnostic
        assert _head_sha(tmp_git_repo) == head_before
        assert _commit_count(tmp_git_repo) == commits_before
    finally:
        set_last_commit_error(None)


def test_invalid_verdict_produces_the_documented_validation_diagnostic(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _seed_engine_defaults(tmp_git_repo)
    _write_commit_review_artifact(workspace, {"verdict": "maybe"})
    artifact_text = workspace.read(COMMIT_REVIEW_ARTIFACT_PATH)

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewGateFailed)
    assert "Invalid verdict" in event.detail
    assert "approve" in event.detail
    assert "reject" in event.detail
    assert _head_sha(tmp_git_repo) == head_before
    assert _commit_count(tmp_git_repo) == commits_before

    _, diagnostics = parse_and_validate(artifact_text, COMMIT_REVIEW_SPEC)
    error_rule_ids = {
        diagnostic.rule_id for diagnostic in diagnostics if diagnostic.severity == "error"
    }
    assert "SPEC010" in error_rule_ids


def test_legitimate_small_deletion_is_approved_not_rejected(
    tmp_git_repo: Path,
) -> None:
    tracked: list[str] = []
    for index in range(10):
        name = f"module_{index:02d}.py"
        module_path = tmp_git_repo / name
        module_path.write_text(f"VALUE = {index}\n", encoding="utf-8")
        tracked.append(name)
    _seed_engine_defaults(tmp_git_repo)
    _track_and_commit(tmp_git_repo, tracked)

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    (tmp_git_repo / "module_00.py").unlink()

    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(workspace, {"verdict": "approve"})

    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewApproved), (
        f"A single small deletion must be approved, got: {event!r}"
    )
    assert not (tmp_git_repo / "module_00.py").exists()
    assert _head_sha(tmp_git_repo) == head_before
    assert _commit_count(tmp_git_repo) == commits_before


def test_reviewer_malfunction_discards_no_pending_work(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    pending = tmp_git_repo / "pending_work.txt"
    pending.write_text("work in progress\n", encoding="utf-8")
    _seed_engine_defaults(tmp_git_repo)

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    events = [_run(workspace)[0] for _ in range(4)]

    assert isinstance(events[-1], CommitReviewGateFailed), (
        f"The identical no-progress bound must fail the gate, got: {events[-1]!r}"
    )
    assert pending.read_text(encoding="utf-8") == "work in progress\n"
    assert _head_sha(tmp_git_repo) == head_before
    assert _commit_count(tmp_git_repo) == commits_before


def test_staged_secret_is_offered_to_the_edit_step_instead_of_being_rejected(
    tmp_git_repo: Path,
) -> None:
    secret = tmp_git_repo / ".env"
    secret.write_text("TOKEN=staged-secret\n", encoding="utf-8")
    repo = _repo(tmp_git_repo)
    repo.index.add([".env"])
    repo.index.commit("track secret before the ignore seed")
    _seed_engine_defaults(tmp_git_repo)

    secret.write_text("TOKEN=rotated-secret\n", encoding="utf-8")

    categories = _stage1_soft_finding_categories(tmp_git_repo)
    assert "secret" in categories, (
        f"The staged secret must reach the reviewer as evidence, got: {categories!r}"
    )

    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "actions": [{"action": "add_to_git_exclude", "pattern": ".env"}],
        },
    )

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewApproved), (
        f"A staged secret must not reject the iteration, got: {event!r}"
    )
    assert secret.read_text(encoding="utf-8") == "TOKEN=rotated-secret\n"
    repo = _repo(tmp_git_repo)
    tracked = [path for path, _stage in repo.index.entries]
    assert ".env" not in tracked
    assert _head_sha(tmp_git_repo) == head_before
    assert _commit_count(tmp_git_repo) == commits_before


def test_oversized_binary_is_evidence_and_a_survivor_is_rejected(
    tmp_git_repo: Path,
) -> None:
    policy = load_policy(_DEFAULT_POLICY_DIR).pipeline.model_copy(
        update={"commit_review": CommitReviewPolicy(binary_size_limit=1024)}
    )
    _seed_engine_defaults(tmp_git_repo)
    blob = tmp_git_repo / "assets" / "payload.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"\x00" * 4096)

    categories = _stage1_soft_finding_categories(tmp_git_repo, pipeline_policy=policy)
    assert "binary_size" in categories, (
        f"The oversized binary must reach the reviewer as evidence, got: {categories!r}"
    )

    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(workspace, {"verdict": "approve"})

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    result = _run(workspace, pipeline_policy=policy)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewRejected), (
        f"An oversized binary that survives the edit step must reject, got: {event!r}"
    )
    assert "assets/payload.bin" in event.reason and "4096" in event.reason, (
        f"The engine-owned reason must name the path and the size, got: {event.reason!r}"
    )
    assert "1024" in event.reason, (
        f"The engine-owned reason must carry the policy limit, got: {event.reason!r}"
    )
    assert blob.exists()
    assert _head_sha(tmp_git_repo) == head_before
    assert _commit_count(tmp_git_repo) == commits_before


def test_final_commit_review_loops_back_on_an_unsafe_action_and_never_advances(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    tracked = tmp_git_repo / "app.py"
    tracked.write_text("print('x')\n", encoding="utf-8")
    repo = _repo(tmp_git_repo)
    repo.index.add(["app.py"])
    repo.index.commit("track app")

    _write_commit_review_artifact(
        workspace,
        {"verdict": "approve", "actions": [{"action": "delete_file", "path": "app.py"}]},
    )

    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    result = _run(workspace, phase="development_final_commit_review")

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewLoopback), (
        f"An unsafe action must loop the reviewer back, got: {event!r}"
    )
    assert tracked.exists()
    assert _head_sha(tmp_git_repo) == head_before
    assert _commit_count(tmp_git_repo) == commits_before
