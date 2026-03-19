use super::*;
use crate::executor::MockProcessExecutor;
use crate::logger::{Colors, Logger};
use crate::prompts::template_context::TemplateContext;
use crate::workspace::{MemoryWorkspace, Workspace};
use std::path::Path;
use std::sync::Arc;

#[test]
fn run_ai_conflict_resolution_uses_unique_logfile_with_attempt_index() {
    let config = crate::config::Config::default();
    let registry = crate::agents::AgentRegistry::new().expect("registry");

    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let template_context = TemplateContext::default();

    let executor: Arc<MockProcessExecutor> = Arc::new(MockProcessExecutor::new());
    let executor_arc = Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>;

    let workspace = MemoryWorkspace::new_test();
    workspace
        .create_dir_all(Path::new(".agent/logs/rebase_conflict_resolution"))
        .expect("create log dir");
    workspace
        .write(
            Path::new(".agent/logs/rebase_conflict_resolution/conflict_resolution_codex_0_a0.log"),
            "old",
        )
        .expect("write existing log");

    let workspace_arc = Arc::new(workspace.clone());

    let ctx = ConflictResolutionContext {
        config: &config,
        registry: &registry,
        template_context: &template_context,
        logger: &logger,
        colors,
        executor_arc,
        workspace: &workspace,
        workspace_arc,
    };

    let _ = run_ai_conflict_resolution("resolve conflicts", &ctx).expect("run");

    let calls = executor.agent_calls();
    assert_eq!(calls.len(), 1);
    assert!(calls[0]
        .logfile
        .ends_with("rebase_conflict_resolution/conflict_resolution_codex_0_a1.log"));
}

#[test]
fn handle_error_resolution_accepts_error_reference() {
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let executor = Arc::new(MockProcessExecutor::new());
    let error = anyhow::anyhow!("test error");

    let continued = handle_error_resolution(&logger, &*executor, &error);

    assert!(!continued);
}
