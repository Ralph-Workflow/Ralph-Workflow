use super::*;
use crate::agents::session::AuditTrail;
use crate::agents::AgentRegistry;
use crate::checkpoint::execution_history::ExecutionHistory;
use crate::checkpoint::RunContext;
use crate::config::Config;
use crate::executor::{MockProcessExecutor, ProcessExecutor};
use crate::logger::{Colors, Logger};
use crate::pipeline::Timer;
use crate::prompts::template_context::TemplateContext;
use crate::prompts::template_registry::TemplateRegistry;
use crate::workspace::MemoryWorkspace;
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use tempfile::tempdir;

#[test]
fn test_run_review_pass_uses_unique_logfile_with_attempt_suffix() {
    let cloud = crate::config::types::CloudConfig::disabled();
    let workspace = MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Plan\n");
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let config = Config::default();
    let registry = AgentRegistry::new().unwrap();
    let template_context = TemplateContext::default();
    let executor = Arc::new(
        MockProcessExecutor::new()
            .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
    );
    let executor_arc: Arc<dyn ProcessExecutor> = executor.clone();

    let repo_root = PathBuf::from("/mock/repo");
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();
    let mut ctx = super::super::context::PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "claude",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: executor_arc.as_ref(),
        executor_arc: executor_arc.clone(),
        repo_root: repo_root.as_path(),
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
        active_session: None,
        audit_trail: AuditTrail::new(),
    };

    let _ =
        run_review_pass(&mut ctx, 1, "review", "", None).expect("run_review_pass should succeed");

    let calls = executor.agent_calls();
    assert_eq!(calls.len(), 1);
    assert!(
        calls[0].logfile.contains("/agents/reviewer_1.log"),
        "review logfile should use per-run format with phase_index naming: {}",
        calls[0].logfile
    );
}

#[test]
fn test_run_fix_pass_uses_unique_logfile_with_attempt_suffix() {
    let cloud = crate::config::types::CloudConfig::disabled();
    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "# Prompt\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "# Issues\n");
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let config = Config::default();
    let registry = AgentRegistry::new().unwrap();
    let template_context = TemplateContext::default();
    let executor = Arc::new(
        MockProcessExecutor::new()
            .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
    );
    let executor_arc: Arc<dyn ProcessExecutor> = executor.clone();

    let repo_root = PathBuf::from("/mock/repo");
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();
    let mut ctx = super::super::context::PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "claude",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: executor_arc.as_ref(),
        executor_arc: executor_arc.clone(),
        repo_root: repo_root.as_path(),
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
        active_session: None,
        audit_trail: AuditTrail::new(),
    };

    let resume_ctx: Option<&crate::checkpoint::restore::ResumeContext> = None;
    let _ = run_fix_pass(
        &mut ctx,
        1,
        crate::prompts::ContextLevel::Normal,
        resume_ctx,
        None,
    )
    .expect("run_fix_pass should succeed");

    let calls = executor.agent_calls();
    assert_eq!(calls.len(), 1);
    // New per-run log format: .agent/logs-<run_id>/agents/reviewer_fix_1.log
    // Agent identity is in the log file header, not the filename
    assert!(
        calls[0].logfile.contains("/agents/reviewer_fix_1.log"),
        "fix logfile should use per-run format with phase_index naming: {}",
        calls[0].logfile
    );
}

#[test]
fn test_run_review_pass_errors_on_missing_template_variables() {
    let cloud = crate::config::types::CloudConfig::disabled();
    let tempdir = tempdir().expect("create temp dir");
    let template_path = tempdir.path().join("review.txt");
    fs::write(
        &template_path,
        "Review {{PLAN}}\n{{CHANGES}}\nMissing: {{MISSING}}\n",
    )
    .expect("write review template");

    let workspace = MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Plan\n");
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let config = Config::default();
    let registry = AgentRegistry::new().unwrap();
    let template_context =
        TemplateContext::new(TemplateRegistry::new(Some(tempdir.path().to_path_buf())));
    let executor = Arc::new(MockProcessExecutor::new());
    let executor_arc: Arc<dyn ProcessExecutor> = executor.clone();

    let repo_root = PathBuf::from("/mock/repo");
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();
    let mut ctx = super::super::context::PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "claude",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: executor_arc.as_ref(),
        executor_arc: executor_arc.clone(),
        repo_root: repo_root.as_path(),
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
        active_session: None,
        audit_trail: AuditTrail::new(),
    };

    let err =
        run_review_pass(&mut ctx, 1, "review", "", None).expect_err("expected validation failure");
    assert!(
        err.to_string().contains("unresolved placeholders"),
        "expected unresolved placeholder error, got: {err}"
    );
    assert!(
        executor.agent_calls().is_empty(),
        "agent should not be invoked when template variables are missing"
    );
}

#[test]
fn test_run_fix_pass_errors_on_missing_template_variables() {
    let cloud = crate::config::types::CloudConfig::disabled();
    let tempdir = tempdir().expect("create temp dir");
    let template_path = tempdir.path().join("fix_mode.txt");
    fs::write(
        &template_path,
        "Fix {{PROMPT}}\n{{PLAN}}\n{{ISSUES}}\nMissing: {{MISSING}}\n",
    )
    .expect("write fix template");

    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "# Prompt\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "# Issues\n");
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let config = Config::default();
    let registry = AgentRegistry::new().unwrap();
    let template_context =
        TemplateContext::new(TemplateRegistry::new(Some(tempdir.path().to_path_buf())));
    let executor = Arc::new(MockProcessExecutor::new());
    let executor_arc: Arc<dyn ProcessExecutor> = executor.clone();

    let repo_root = PathBuf::from("/mock/repo");
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();
    let mut ctx = super::super::context::PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "claude",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: executor_arc.as_ref(),
        executor_arc: executor_arc.clone(),
        repo_root: repo_root.as_path(),
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
        active_session: None,
        audit_trail: AuditTrail::new(),
    };

    let resume_ctx: Option<&crate::checkpoint::restore::ResumeContext> = None;
    let err = run_fix_pass(
        &mut ctx,
        1,
        crate::prompts::ContextLevel::Normal,
        resume_ctx,
        None,
    )
    .expect_err("expected validation failure");
    assert!(
        err.to_string().contains("unresolved placeholders"),
        "expected unresolved placeholder error, got: {err}"
    );
    assert!(
        executor.agent_calls().is_empty(),
        "agent should not be invoked when template variables are missing"
    );
}
