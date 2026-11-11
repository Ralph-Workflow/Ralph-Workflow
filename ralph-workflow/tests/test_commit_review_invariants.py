from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo
from loguru import logger

from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.phases import PhaseContext
from ralph.phases._commit_review_catalog import (
    GENERATED_TEXT_MARKERS,
    LOCKFILE_BASENAMES,
    render_delete_decision_rules_markdown,
)
from ralph.phases.commit_review import (
    CommitReviewApproved,
    CommitReviewGateFailed,
    CommitReviewLoopback,
    handle_commit_review_phase,
    seal_pending,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.prompts.commit_review import render_commit_review_prompt
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.recovery.classifier import FailureCategory
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pytest import LogCaptureFixture

COMMIT_REVIEW_ARTIFACT_PATH = ".agent/artifacts/commit_review.md"

pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.required_auto_integrate_e2e]


def _resolve_verdict(content: dict[str, object]) -> str:
    verdict = content.get("verdict")
    if verdict is None:
        return "approve"
    return str(verdict)


def _resolve_tree_sha(workspace: FsWorkspace, content: dict[str, object]) -> str | None:
    if "tree_sha" in content:
        return str(content["tree_sha"])
    repo_path = Path(workspace.root)
    if not (repo_path / ".git").exists():
        return None
    try:
        from ralph.config.bootstrap import (
            auto_seed_default_git_exclude,
            auto_seed_default_gitignore,
        )
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


def _ctx(workspace: FsWorkspace) -> PhaseContext:
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )


def _invoke(phase: str = "development_commit_review") -> InvokeAgentEffect:
    return InvokeAgentEffect(agent_name="dev", phase=phase, prompt_file="cleanup.txt")


def _run(workspace: FsWorkspace, phase: str = "development_commit_review") -> list[object]:
    return handle_commit_review_phase(_invoke(phase), _ctx(workspace))


def test_identical_declined_path_replay_completes_without_phase_failure(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    workspace.write("src.py", "print(1)\n")
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "src.py"}],
        },
    )
    first = _run(workspace)
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "src.py"}],
        },
    )
    second = _run(workspace)
    assert len(first) == 1 and isinstance(first[0], CommitReviewLoopback)
    assert len(second) == 1 and isinstance(second[0], CommitReviewLoopback)
    assert (tmp_git_repo / "src.py").exists()
    assert not any(isinstance(event, PhaseFailureEvent) for event in [*first, *second])


def test_missing_artifact_identical_replay_is_bounded(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    events = [_run(workspace)[0] for _ in range(4)]
    assert isinstance(events[0], PhaseFailureEvent)
    assert events[0].failure_category is FailureCategory.ARTIFACT_VALIDATION
    assert isinstance(events[-1], CommitReviewGateFailed)
    assert not any(
        isinstance(event, PhaseFailureEvent) and event.phase == "failed_terminal"
        for event in events
    )


def test_unparseable_artifact_identical_replay_is_bounded(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    path = tmp_git_repo / COMMIT_REVIEW_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a commit_review artifact\n", encoding="utf-8")
    events = [_run(workspace)[0] for _ in range(4)]
    assert isinstance(events[0], PhaseFailureEvent)
    assert isinstance(events[-1], CommitReviewGateFailed)


def test_all_throw_apply_identical_replay_is_bounded(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "add_to_gitignore", "pattern": "*.bin"}],
        },
    )

    def _boom(_root: Path, _patterns: list[str]) -> None:
        raise OSError("append failed")

    monkeypatch.setattr("ralph.phases.commit_review.append_to_gitignore", _boom)
    events = [_run(workspace)[0] for _ in range(4)]
    assert isinstance(events[0], CommitReviewLoopback), (
        f"An apply-time failure must loop the agent back, got: {events[0]!r}"
    )
    assert "*.bin" in events[0].hint, (
        f"The loopback hint must name the failed pattern, got: {events[0].hint!r}"
    )
    assert isinstance(events[-1], CommitReviewGateFailed)


def test_wholly_declined_deletes_emit_no_phase_failure(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    workspace.write("README.md", "docs\n")
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "README.md"}],
        },
    )
    result = _run(workspace)
    assert len(result) == 1 and isinstance(result[0], CommitReviewLoopback)
    assert not any(isinstance(event, PhaseFailureEvent) for event in result)
    assert (tmp_git_repo / "README.md").exists()


def test_next_cleanup_prompt_contains_declined_path_and_reason(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    workspace.write("app.py", "x = 1\n")
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "app.py"}],
        },
    )
    _run(workspace, phase="development_final_commit_review")
    rendered = render_commit_review_prompt(
        phase="development_final_commit_review",
        workspace_root=tmp_git_repo,
        worker_namespace=None,
        prompt_content="criteria",
        product_criteria_path=str(tmp_git_repo / ".agent" / "PRODUCT_CRITERIA.md"),
        template_name="commit_review.jinja",
        tmpl_ctx=TemplateContext.default(tmp_git_repo),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
    )
    assert "app.py" in rendered
    assert "rejected" in rendered.lower() or "declined" in rendered.lower()


def test_next_cleanup_prompt_contains_apply_failure(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "add_to_git_exclude", "pattern": ".env.local"}],
        },
    )

    def _boom(_root: Path, _patterns: list[str]) -> None:
        raise OSError("exclude failed")

    monkeypatch.setattr("ralph.phases.commit_review.add_to_git_exclude", _boom)
    _run(workspace)
    rendered = render_commit_review_prompt(
        phase="development_commit_review",
        workspace_root=tmp_git_repo,
        worker_namespace=None,
        prompt_content="criteria",
        product_criteria_path=str(tmp_git_repo / ".agent" / "PRODUCT_CRITERIA.md"),
        template_name="commit_review.jinja",
        tmpl_ctx=TemplateContext.default(tmp_git_repo),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
    )
    assert ".env.local" in rendered
    assert "failed" in rendered.lower()


def test_declined_and_empty_decisions_log_once_at_warning(
    tmp_git_repo: Path,
    caplog: LogCaptureFixture,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    workspace.write("main.py", "pass\n")
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [
                {"action": "delete_file", "path": "main.py"},
                {"action": "delete_file", "path": "main.py"},
            ],
        },
    )
    records: list[str] = []
    handler_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        _run(workspace)
    finally:
        logger.remove(handler_id)
    text = "".join(records)
    assert "Skipping unsafe delete_file action for 'main.py'" in text
    assert "duplicate delete_file" in text


def test_mixed_declined_delete_and_throwing_ignore_is_not_applied_work(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    workspace.write("lib.py", "pass\n")
    _write_commit_review_artifact(
        workspace,
        {
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
    assert isinstance(result[0], CommitReviewLoopback), (
        f"A batch that applied no work must loop the agent back, got: {result!r}"
    )
    assert "lib.py" in result[0].hint, (
        f"The hint must name the declined path, got: {result[0].hint!r}"
    )
    assert "*.o" in result[0].hint, (
        f"The hint must name the pattern that failed to apply, got: {result[0].hint!r}"
    )


def test_untracked_completion_report_md_is_deleted(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    report = tmp_git_repo / "completion_report.md"
    report.write_text("# generated\n", encoding="utf-8")
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "completion_report.md"}],
        },
    )
    result = _run(workspace)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert not report.exists()


def test_tracked_completion_report_md_is_declined(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    report = tmp_git_repo / "completion_report.md"
    report.write_text("# tracked\n", encoding="utf-8")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["completion_report.md"])
        repo.index.commit("track completion report")
    finally:
        repo.close()
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "completion_report.md"}],
        },
    )
    result = _run(workspace)
    assert len(result) == 1 and isinstance(result[0], CommitReviewLoopback)
    assert not any(isinstance(event, PhaseFailureEvent) for event in result)
    assert report.exists()


def test_head_tracked_source_is_never_deleted(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    source = tmp_git_repo / "tracked.py"
    source.write_text("print('hi')\n", encoding="utf-8")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["tracked.py"])
        repo.index.commit("track source")
    finally:
        repo.close()
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "tracked.py"}],
        },
    )
    _run(workspace)
    assert source.exists()


def test_lockfile_is_never_deleted(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    lock = tmp_git_repo / "package-lock.json"
    lock.write_text("{}", encoding="utf-8")
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "package-lock.json"}],
        },
    )
    _run(workspace)
    assert lock.exists()
    assert "package-lock.json" in LOCKFILE_BASENAMES


def test_parent_traversal_delete_is_declined(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "../outside.txt"}],
        },
    )
    result = _run(workspace)
    assert len(result) == 1 and isinstance(result[0], CommitReviewLoopback)


def test_missing_delete_target_is_noop(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "scratch.tmp"}],
        },
    )
    result = _run(workspace)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert not (tmp_git_repo / "scratch.tmp").exists()


def test_idempotent_second_apply_of_well_formed_batch(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    junk = tmp_git_repo / "session-output.txt"
    junk.write_text("tmp\n", encoding="utf-8")
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [
                {"action": "delete_file", "path": "session-output.txt"},
                {"action": "add_to_gitignore", "pattern": "*.exe"},
            ],
        },
    )
    first = _run(workspace)
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [
                {"action": "delete_file", "path": "session-output.txt"},
                {"action": "add_to_gitignore", "pattern": "*.exe"},
            ],
        },
    )
    second = _run(workspace)
    assert len(first) == 1 and isinstance(first[0], CommitReviewApproved)
    assert len(second) == 1 and isinstance(second[0], CommitReviewApproved)
    assert not junk.exists()
    assert "*.exe" in workspace.read(".gitignore")


def test_stale_identical_artifact_is_not_a_new_attempt(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    path = tmp_git_repo / COMMIT_REVIEW_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stale leftover\n", encoding="utf-8")
    first = _run(workspace)[0]
    second = _run(workspace)[0]
    third = _run(workspace)[0]
    fourth = _run(workspace)[0]
    assert isinstance(first, PhaseFailureEvent)
    assert isinstance(second, PhaseFailureEvent)
    assert isinstance(third, (PhaseFailureEvent, CommitReviewGateFailed))
    assert isinstance(fourth, CommitReviewGateFailed)


def test_preparation_failure_is_visible_and_non_fatal(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(workspace, {"verdict": "approve", "actions": []})

    def _boom(*_args: object, **_kwargs: object) -> list[str]:
        raise OSError("untrack exploded")

    monkeypatch.setattr("ralph.phases.commit_review.untrack_engine_internal_files", _boom)
    records: list[str] = []
    handler_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        result = _run(workspace)
    finally:
        logger.remove(handler_id)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert "untrack_engine_internal_files failed" in "".join(records)


def test_prompt_and_engine_generated_text_classes_agree() -> None:
    table = render_delete_decision_rules_markdown()
    for marker in GENERATED_TEXT_MARKERS:
        assert marker in table
    assert "Markdown" in table or ".md" in table
    package_root = Path(__file__).resolve().parents[1]
    template = FsWorkspace(package_root).read(
        "ralph/prompts/templates/commit_review.jinja",
    )
    assert "{{ DELETE_DECISION_RULES }}" in template
    assert "{% if LAST_RETRY_ERROR %}" in template


def test_untracked_config_file_is_never_deleted(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    config = tmp_git_repo / "pyproject.toml"
    config.write_text('[project]\nname = "demo"\n', encoding="utf-8")
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "pyproject.toml"}],
        },
    )
    result = _run(workspace)
    assert len(result) == 1 and isinstance(result[0], CommitReviewLoopback)
    assert config.exists()


def test_untracked_docs_path_is_never_deleted(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    docs = tmp_git_repo / "docs" / "guide.md"
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text("# Guide\n", encoding="utf-8")
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "docs/guide.md"}],
        },
    )
    result = _run(workspace)
    assert len(result) == 1 and isinstance(result[0], CommitReviewLoopback)
    assert docs.exists()


def test_symlink_escape_is_never_deleted(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    outside = tmp_git_repo.parent / "commit-review-outside.txt"
    outside.write_text("preserve-me\n", encoding="utf-8")
    link = tmp_git_repo / "scratch.tmp"
    link.symlink_to(outside)
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": "scratch.tmp"}],
        },
    )
    result = _run(workspace)
    assert PipelineEvent.AGENT_SUCCESS in result or isinstance(
        result[0], (PhaseFailureEvent, CommitReviewLoopback, CommitReviewApproved)
    )
    assert outside.exists()
    assert outside.read_text(encoding="utf-8") == "preserve-me\n"


def test_recognized_secret_is_untracked_and_preserved(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    secret = tmp_git_repo / ".env"
    secret.write_text("TOKEN=secret\n", encoding="utf-8")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add([".env"])
        repo.index.commit("track secret")
    finally:
        repo.close()
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "add_to_git_exclude", "pattern": ".env"}],
        },
    )
    result = _run(workspace)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert secret.read_text(encoding="utf-8") == "TOKEN=secret\n"
    repo = Repo(tmp_git_repo)
    try:
        tracked = repo.git.ls_files("--cached").splitlines()
    finally:
        repo.close()
    assert ".env" not in tracked


def test_tracked_engine_internal_runtime_artifact_is_deleted(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    target = tmp_git_repo / ".agent" / "raw" / "opencode.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("log\n", encoding="utf-8")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add([".agent/raw/opencode.log"])
        repo.index.commit("track engine log")
    finally:
        repo.close()
    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": ".agent/raw/opencode.log"}],
        },
    )
    result = _run(workspace)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert not target.exists()
