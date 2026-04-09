use super::common::TestFixture;
use crate::executor::MockProcessExecutor;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{AgentEvent, PipelineEvent};
use crate::reducer::state::{ContinuationState, PipelineState, SameAgentRetryReason};
use crate::workspace::{MemoryWorkspace, Workspace};
use std::sync::Arc;

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

    // GUARD: capture project HEAD OID before any git operations
    let project_head_before = test_helpers::capture_project_head_oid();

    let repo_dir = tempfile::TempDir::new().expect("create temp git repo");
    let repo = git2::Repository::init(repo_dir.path()).expect("init git repo");
    test_helpers::assert_repo_is_isolated(&repo);

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

    // GUARD: verify project git state unchanged
    test_helpers::assert_project_head_unchanged(&project_head_before);
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

    // GUARD: capture project HEAD OID before any git operations
    let project_head_before = test_helpers::capture_project_head_oid();

    let repo_dir = tempfile::TempDir::new().expect("create temp git repo");
    let repo = git2::Repository::init(repo_dir.path()).expect("init git repo");
    test_helpers::assert_repo_is_isolated(&repo);
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

    // GUARD: verify project git state unchanged
    test_helpers::assert_project_head_unchanged(&project_head_before);
}

/// Boundary regression: analysis drain must configure `completion_output_path` to
/// `development_result.xml` so that the valid-result override works correctly.
///
/// Bug 2 root cause: Analysis drain returned `None` as the completion path, which bypassed
/// the valid-result check in the executor. When the agent produced a valid result file,
/// the timeout/error classification still saw no completion path and fell through to
/// generic failure or timeout classification.
///
/// Proof strategy: configure the mock executor to return proprietary exit code 91 (a
/// non-zero exit that is NOT a standard error, mimicking an agent that exited abnormally
/// but completed its work). When `completion_output_path` is wired correctly, the
/// executor finds the valid XML file and promotes the result to `InvocationSucceeded`
/// regardless of the exit code. If the path were `None`, the valid-file check is skipped
/// and the result would be `InvocationFailed`.
#[test]
fn test_invoke_analysis_agent_completion_output_path_wired_to_development_result_xml() {
    // Workspace contains a valid development_result.xml as the agent's output.
    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/tmp")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(
            ".agent/tmp/development_result.xml",
            "<ralph-development-result>\
             <ralph-status>completed</ralph-status>\
             </ralph-development-result>",
        );

    // Mock returns exit code 91 — a proprietary exit that does not map to a
    // standard error (no auth failure, no rate limit, no explicit timeout).
    // Without a valid completion path the executor cannot detect the result file
    // and would emit InvocationFailed. With the correct path it must emit
    // InvocationSucceeded because the valid result file wins.
    let executor = Arc::new(MockProcessExecutor::new().with_agent_result(
        "claude",
        Ok(crate::executor::AgentCommandResult::failure(91, "")),
    ));

    let workspace_arc = Arc::new(workspace.clone()) as Arc<dyn crate::workspace::Workspace>;
    let colors = crate::logger::Colors { enabled: false };
    let logger = crate::logger::Logger::new(colors);
    let mut timer = crate::pipeline::Timer::new();
    let config = crate::config::Config::default();
    let registry = crate::agents::AgentRegistry::new().unwrap();
    let template_context = crate::prompts::template_context::TemplateContext::default();
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let cloud = crate::config::types::CloudConfig::disabled();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

    let mut ctx = crate::phases::PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "rev",
        review_guidelines: None,
        template_context: &template_context,
        run_context: crate::checkpoint::RunContext::new(),
        execution_history: crate::checkpoint::execution_history::ExecutionHistory::new(),
        executor: executor.as_ref(),
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: std::path::Path::new("/mock/repo"),
        workspace: &workspace,
        workspace_arc: Arc::clone(&workspace_arc),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
    };

    let mut handler = MainEffectHandler::new(PipelineState {
        phase: crate::reducer::event::PipelinePhase::Development,
        iteration: 0,
        ..PipelineState::initial(1, 0)
    });

    let result = handler
        .invoke_analysis_agent(&mut ctx, 0)
        .expect("invoke_analysis_agent should not fail");

    // The key assertion: with a valid development_result.xml AND exit code 91, the
    // additional_events must contain InvocationSucceeded — proving that
    // completion_output_path was correctly wired to development_result.xml (not None).
    //
    // `build_agent_invocation_result` always puts InvocationStarted as the primary
    // event and the actual execution result in additional_events. So we look there.
    //
    // If completion_output_path were None, the valid-result check would be skipped
    // and exit code 91 would produce InvocationFailed instead.
    let execution_event = result
        .additional_events
        .iter()
        .find(|ev| {
            matches!(
                ev,
                PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
                    | PipelineEvent::Agent(AgentEvent::InvocationFailed { .. })
            )
        })
        .unwrap_or(&result.event);

    assert!(
        matches!(
            execution_event,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        ),
        "analysis drain with valid development_result.xml and exit code 91 must produce \
         InvocationSucceeded — completion_output_path must be wired to development_result.xml; \
         got primary={:?} additional={:?}",
        result.event,
        result.additional_events
    );
}
