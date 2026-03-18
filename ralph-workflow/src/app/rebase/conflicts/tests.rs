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

#[test]
fn try_resolve_conflicts_hook_runs_after_prompt_capture() {
    use std::cell::Cell;

    let config = crate::config::Config::default();
    let registry = crate::agents::AgentRegistry::new().expect("registry");

    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let template_context = TemplateContext::default();

    let executor: Arc<MockProcessExecutor> = Arc::new(MockProcessExecutor::new());
    let executor_arc = Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>;

    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Prompt")
        .with_file(".agent/PLAN.md", "Plan")
        .with_file(
            "src/lib.rs",
            "<<<<<<< HEAD\nleft\n=======\nright\n>>>>>>> branch\n",
        );
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

    let hook_called = Cell::new(false);

    let (_resolved, replay, prompt_history) = try_resolve_conflicts_with_hook(
        &["src/lib.rs".to_string()],
        &ctx,
        "PreRebase",
        &*executor,
        |replay| {
            hook_called.set(true);
            assert!(
                prompt_history.is_empty(),
                "hook must observe empty prompt_history before capture"
            );
            replay
                .captured_entry
                .clone()
                .map(|entry| (replay.key.clone(), entry))
        },
    )
    .expect("try_resolve_conflicts_with_hook should succeed");

    assert_eq!(
        replay.key, "prerebase_conflict_resolution",
        "Expected replay info to return the typed prompt key"
    );

    assert!(
        hook_called.get(),
        "Expected after_prompt_capture hook to run"
    );

    assert!(
        prompt_history.contains_key("prerebase_conflict_resolution"),
        "prompt_history must contain entry after conflict resolution"
    );

    let entry = prompt_history
        .get("prerebase_conflict_resolution")
        .expect("entry must exist");
    assert!(
        entry.content_id.as_deref().is_some_and(|id| id.len() == 64),
        "expected conflict-resolution prompt history entry to include a sha256 content_id"
    );
}
