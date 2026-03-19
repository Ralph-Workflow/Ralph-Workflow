use super::common::TestFixture;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{ContinuationState, PipelineState, SameAgentRetryReason};
use crate::workspace::{MemoryWorkspace, Workspace};

#[test]
fn test_invoke_analysis_agent_gracefully_handles_missing_plan_and_diff() {
    // Regression: analysis should still run even when PLAN.md is missing or git diff cannot
    // be generated. These inputs should be substituted with placeholders.
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";

    let mut handler = MainEffectHandler::new(PipelineState {
        phase: crate::reducer::event::PipelinePhase::Development,
        iteration: 0,
        ..PipelineState::initial(1, 0)
    });

    handler
        .invoke_analysis_agent(&mut ctx, 0)
        .expect("invoke_analysis_agent should not fail when PLAN/DIFF inputs are missing");

    // Validate that the agent was invoked and the prompt has the analysis task structure.
    //
    // MemoryWorkspace has no on-disk .git, so get_git_diff_from_start_with_workspace
    // returns Err immediately — the diff section always contains the placeholder.
    // This assertion verifies the graceful fallback without relying on real git state.
    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    let prompt = &calls[0].prompt;
    assert!(
        prompt.contains("Your task is to VERIFY whether the code changes satisfy the PLAN"),
        "expected analysis prompt header in prompt, got: {prompt}"
    );
    assert!(
        prompt.contains("[DIFF unavailable"),
        "expected diff placeholder in prompt for MemoryWorkspace (no .git on disk), got: {prompt}"
    );
}

#[test]
fn test_invoke_analysis_agent_same_agent_retry_timeout_with_context_includes_context_file_guidance()
{
    let timeout_context_file_path = ".agent/tmp/timeout-context-analysis_1.md";
    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/tmp")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/DIFF.backup", "DIFF_BACKUP_MARKER")
        .with_file(timeout_context_file_path, "TIMEOUT_CONTEXT_MARKER");

    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";

    let mut handler = MainEffectHandler::new(PipelineState {
        phase: crate::reducer::event::PipelinePhase::Development,
        iteration: 0,
        continuation: ContinuationState {
            same_agent_retry_pending: true,
            same_agent_retry_reason: Some(SameAgentRetryReason::TimeoutWithContext),
            timeout_context_file_path: Some(timeout_context_file_path.to_string()),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(1, 0)
    });

    handler
        .invoke_analysis_agent(&mut ctx, 0)
        .expect("invoke_analysis_agent should succeed");

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    let prompt = &calls[0].prompt;

    assert!(
        prompt.contains("## Retry Note"),
        "expected same-agent retry preamble in analysis prompt, got: {prompt}"
    );
    assert!(
        prompt.contains("timed out with partial progress"),
        "expected timeout-with-context retry guidance in analysis prompt, got: {prompt}"
    );
    assert!(
        prompt.contains(timeout_context_file_path),
        "expected analysis prompt to reference timeout context file path, got: {prompt}"
    );
    assert!(
        prompt.contains("Read that file first to continue from where you left off."),
        "expected analysis prompt to instruct reading timeout context file, got: {prompt}"
    );
}

#[test]
fn test_invoke_analysis_agent_writes_diff_backup_when_git_diff_succeeds() {
    // When git diff generation succeeds, the handler should still write/update
    // `.agent/DIFF.backup` as a best-effort fallback for prompt materialization.
    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/tmp")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/DIFF.backup", "DIFF_BACKUP_MARKER");

    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";

    let mut handler = MainEffectHandler::new(PipelineState {
        phase: crate::reducer::event::PipelinePhase::Development,
        iteration: 0,
        ..PipelineState::initial(1, 0)
    });

    handler
        .invoke_analysis_agent(&mut ctx, 0)
        .expect("invoke_analysis_agent should succeed");

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    let prompt = &calls[0].prompt;
    assert!(
        prompt.contains("diff --git") || prompt.contains("[DIFF unavailable"),
        "expected a git diff or a diff-unavailable placeholder in prompt"
    );

    let backup = fixture
        .workspace
        .read(std::path::Path::new(".agent/DIFF.backup"))
        .expect("expected .agent/DIFF.backup to exist");
    assert!(
        backup.contains("diff --git") || backup.contains("[DIFF unavailable"),
        "expected .agent/DIFF.backup to contain a git diff or placeholder"
    );
    assert_ne!(
        backup, "DIFF_BACKUP_MARKER",
        "expected .agent/DIFF.backup to be refreshed"
    );
}

#[test]
fn test_invoke_analysis_agent_uses_repo_root_for_diff_not_start_commit_baseline() {
    // TDD regression: analysis must generate its diff from `ctx.repo_root` via
    // `git_diff_in_repo`, not via workspace-based `.agent/start_commit` baseline logic.
    //
    // This test is deterministic: it creates an isolated on-disk git repo with a
    // known working-tree change, then asserts that the analysis prompt contains a
    // diff for that repo (including a unique marker).
    //
    // IMPORTANT: Avoid mutating the process CWD here. CWD is process-global and Rust
    // tests run in parallel by default.
    use std::path::Path;

    let repo_dir = tempfile::TempDir::new().expect("create temp git repo");
    let repo = git2::Repository::init(repo_dir.path()).expect("init git repo");

    // Create an initial commit so the diff baseline is HEAD.
    let marker_file = "ralph_test_repo_root_diff_marker.txt";
    let marker_abs = repo_dir.path().join(marker_file);
    std::fs::write(&marker_abs, "initial\n").expect("write marker file");

    let mut index = repo.index().expect("open index");
    index
        .add_path(Path::new(marker_file))
        .expect("add marker file");
    index.write().expect("write index");
    let tree_oid = index.write_tree().expect("write tree");
    let tree = repo.find_tree(tree_oid).expect("find tree");
    let sig = git2::Signature::now("test", "test@test.com").expect("signature");
    repo.commit(Some("HEAD"), &sig, &sig, "init", &tree, &[])
        .expect("create initial commit");

    // Modify the tracked file to create a deterministic patch.
    let unique_marker = "UNIQUE_REPO_ROOT_MARKER";
    std::fs::write(&marker_abs, format!("initial\nmodified\n{unique_marker}\n"))
        .expect("modify marker file");

    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/tmp")
        .with_file(".agent/PLAN.md", "# Plan\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.repo_root = repo_dir.path().to_path_buf();

    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";

    let mut handler = MainEffectHandler::new(PipelineState {
        phase: crate::reducer::event::PipelinePhase::Development,
        iteration: 0,
        ..PipelineState::initial(1, 0)
    });

    handler
        .invoke_analysis_agent(&mut ctx, 0)
        .expect("invoke_analysis_agent should succeed");

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    let prompt = &calls[0].prompt;

    // The key assertion: the prompt must include a patch for the repo at repo_root.
    assert!(
        prompt.contains("diff --git a/ralph_test_repo_root_diff_marker.txt b/ralph_test_repo_root_diff_marker.txt"),
        "expected analysis prompt to include diff for marker file from ctx.repo_root; got: {prompt}"
    );
    assert!(
        prompt.contains(unique_marker),
        "expected analysis prompt to include unique marker from ctx.repo_root diff; got: {prompt}"
    );
    assert!(
        !prompt.contains("[DIFF unavailable"),
        "expected diff generation to succeed; got: {prompt}"
    );
}

#[test]
fn test_invoke_analysis_agent_uses_head_baseline_not_start_commit() {
    // TDD regression: invoke_analysis_agent must generate its diff from HEAD
    // (working-tree vs last commit), NOT from .agent/start_commit (pipeline-start baseline).
    //
    // Proof strategy (A/B/C):
    //   Commit A: initial commit (baseline)
    //   Commit B: committed change (already in history — must NOT appear in analysis diff)
    //   Change C: uncommitted modification with unique marker (MUST appear in analysis diff)
    //
    // If HEAD baseline is used: diff shows only C. ✓
    // If start_commit baseline is used: diff shows both B and C. ✗
    //
    // IMPORTANT: Use an isolated tempdir repo; never mutate process CWD (test parallelism).
    use std::path::Path;

    let repo_dir = tempfile::TempDir::new().expect("create temp git repo");
    let repo = git2::Repository::init(repo_dir.path()).expect("init git repo");
    let sig = git2::Signature::now("test", "test@test.com").expect("signature");

    // Commit A: create two tracked files.
    // file_committed: will hold the "already committed" change (commit B).
    // file_working:   will hold the uncommitted working-tree change (C).
    let file_committed = "analysis_committed_change.txt";
    let file_working = "analysis_working_change.txt";
    let abs_committed = repo_dir.path().join(file_committed);
    let abs_working = repo_dir.path().join(file_working);
    std::fs::write(&abs_committed, "base content\n").expect("write committed file A");
    std::fs::write(&abs_working, "base content\n").expect("write working file A");
    let mut index = repo.index().expect("open index A");
    index
        .add_path(Path::new(file_committed))
        .expect("stage committed file A");
    index
        .add_path(Path::new(file_working))
        .expect("stage working file A");
    index.write().expect("write index A");
    let tree_a = repo
        .find_tree(index.write_tree().expect("write tree A"))
        .expect("find tree A");
    repo.commit(Some("HEAD"), &sig, &sig, "commit A: initial", &tree_a, &[])
        .expect("create commit A");

    // Commit B: modify file_committed — becomes part of history.
    // HEAD baseline: file_committed has NO working-tree changes (HEAD == workdir for this file).
    // start_commit baseline: file_committed would show committed_marker as added.
    let committed_marker = "ANALYSIS_COMMITTED_CHANGE_MUST_NOT_APPEAR_IN_DIFF";
    std::fs::write(
        &abs_committed,
        format!("base content\n{committed_marker}\n"),
    )
    .expect("write committed file for commit B");
    let mut index = repo.index().expect("open index B");
    index
        .add_path(Path::new(file_committed))
        .expect("stage committed file B");
    index.write().expect("write index B");
    let tree_b = repo
        .find_tree(index.write_tree().expect("write tree B"))
        .expect("find tree B");
    let parent_a = repo
        .head()
        .expect("head after A")
        .peel_to_commit()
        .expect("commit A");
    repo.commit(
        Some("HEAD"),
        &sig,
        &sig,
        "commit B: committed change",
        &tree_b,
        &[&parent_a],
    )
    .expect("create commit B");

    // Change C: modify file_working without staging (MUST appear in HEAD diff).
    // file_working is tracked (in A) but untouched in B, so HEAD has base content.
    let uncommitted_marker = "ANALYSIS_UNCOMMITTED_CHANGE_MUST_APPEAR_IN_DIFF";
    std::fs::write(
        &abs_working,
        format!("base content\n{uncommitted_marker}\n"),
    )
    .expect("write uncommitted change to working file");

    // Set up fixture with isolated repo root. Workspace has no .agent/start_commit file,
    // so any start_commit-based code path would either error or use a wrong baseline.
    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/tmp")
        .with_file(".agent/PLAN.md", "# Plan\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.repo_root = repo_dir.path().to_path_buf();
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";

    let mut handler = MainEffectHandler::new(PipelineState {
        phase: crate::reducer::event::PipelinePhase::Development,
        iteration: 0,
        ..PipelineState::initial(1, 0)
    });

    handler
        .invoke_analysis_agent(&mut ctx, 0)
        .expect("invoke_analysis_agent should succeed with isolated repo");

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1, "expected exactly one agent invocation");
    let prompt = &calls[0].prompt;

    // C (uncommitted) must appear — proves HEAD diff captures working tree changes.
    assert!(
        prompt.contains(uncommitted_marker),
        "expected uncommitted change marker in analysis prompt; got: {prompt}"
    );

    // B (committed) must NOT appear — proves HEAD baseline is used, not start_commit.
    assert!(
        !prompt.contains(committed_marker),
        "expected already-committed change to be ABSENT from analysis prompt (HEAD baseline); got: {prompt}"
    );
}
