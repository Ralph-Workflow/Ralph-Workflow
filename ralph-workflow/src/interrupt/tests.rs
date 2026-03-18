// Tests for interrupt signal handling and checkpoint persistence.

use super::*;
use crate::checkpoint::load_checkpoint_with_workspace;
use crate::workspace::MemoryWorkspace;
use crate::workspace::Workspace;
use std::sync::atomic::Ordering;
use std::sync::Arc;

#[test]
fn interrupt_handler_source_is_ascii_only() {
    // Guardrail: keep interrupt handler output ASCII-only to avoid encoding/rendering
    // issues in unattended environments.
    let src = include_str!("mod.rs");
    assert!(
        src.is_ascii(),
        "interrupt/mod.rs must remain ASCII-only; found non-ASCII characters"
    );
    let src = include_str!("checkpoint.rs");
    assert!(
        src.is_ascii(),
        "interrupt/checkpoint.rs must remain ASCII-only; found non-ASCII characters"
    );
}

#[test]
fn test_interrupt_context_creation() {
    let workspace = Arc::new(MemoryWorkspace::new_test());
    let context = InterruptContext {
        phase: crate::checkpoint::PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        run_context: crate::checkpoint::RunContext::new(),
        execution_history: crate::checkpoint::ExecutionHistory::new(),
        prompt_history: std::collections::HashMap::new(),
        workspace,
    };

    assert_eq!(context.phase, crate::checkpoint::PipelinePhase::Development);
    assert_eq!(context.iteration, 2);
    assert_eq!(context.total_iterations, 5);
}

#[test]
fn test_set_and_clear_interrupt_context() {
    let workspace = Arc::new(MemoryWorkspace::new_test());
    let context = InterruptContext {
        phase: crate::checkpoint::PipelinePhase::Planning,
        iteration: 1,
        total_iterations: 3,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        run_context: crate::checkpoint::RunContext::new(),
        execution_history: crate::checkpoint::ExecutionHistory::new(),
        prompt_history: std::collections::HashMap::new(),
        workspace,
    };

    set_interrupt_context(context);
    {
        let ctx = INTERRUPT_CONTEXT.get().unwrap();
        assert!(ctx.is_some());
        assert_eq!(
            ctx.as_ref().unwrap().phase,
            crate::checkpoint::PipelinePhase::Planning
        );
    }

    clear_interrupt_context();
    let ctx = INTERRUPT_CONTEXT.get().unwrap();
    assert!(ctx.is_none());
}

#[test]
fn second_sigint_during_active_event_loop_requests_forced_exit() {
    EVENT_LOOP_ACTIVE.store(true, Ordering::SeqCst);
    EVENT_LOOP_ACTIVE_SIGINT_COUNT.store(0, Ordering::SeqCst);

    assert!(
        !register_sigint_during_active_event_loop(),
        "first Ctrl+C should request graceful shutdown"
    );
    assert!(
        register_sigint_during_active_event_loop(),
        "second Ctrl+C should force immediate exit"
    );

    EVENT_LOOP_ACTIVE.store(false, Ordering::SeqCst);
    EVENT_LOOP_ACTIVE_SIGINT_COUNT.store(0, Ordering::SeqCst);
}

#[test]
fn test_early_interrupt_persists_minimal_checkpoint_with_user_interrupt_flag() {
    // Regression: if Ctrl+C happens before the first regular checkpoint exists,
    // we must still persist a checkpoint (or marker) that records interrupted_by_user=true.
    let workspace: Arc<dyn Workspace> =
        Arc::new(MemoryWorkspace::new_test().with_file("PROMPT.md", "# prompt"));
    let context = InterruptContext {
        phase: crate::checkpoint::PipelinePhase::Planning,
        iteration: 0,
        total_iterations: 3,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        run_context: crate::checkpoint::RunContext::new(),
        execution_history: crate::checkpoint::ExecutionHistory::new(),
        prompt_history: std::collections::HashMap::new(),
        workspace: Arc::clone(&workspace),
    };

    // No checkpoint exists yet
    assert!(
        load_checkpoint_with_workspace(&*workspace)
            .expect("load should not error")
            .is_none(),
        "test precondition: no checkpoint should exist"
    );

    checkpoint::save_interrupt_checkpoint(&context).expect("save should succeed");

    let checkpoint = load_checkpoint_with_workspace(&*workspace)
        .expect("checkpoint should be readable")
        .expect("checkpoint should exist after early interrupt");

    // On Ctrl+C we persist the *current operational phase* so `--resume`
    // continues work instead of immediately terminating in Interrupted phase.
    assert_eq!(checkpoint.phase, context.phase);
    assert!(checkpoint.interrupted_by_user);
    assert!(
        checkpoint.prompt_md_checksum.is_some(),
        "prompt checksum must be recorded for resume validation"
    );
    assert_eq!(checkpoint.cli_args.developer_iters, 3);
    assert_eq!(checkpoint.cli_args.reviewer_reviews, 1);
}

#[test]
fn test_interrupt_checkpoint_does_not_overwrite_existing_checkpoint_phase() {
    // Regression: if a checkpoint already exists, Ctrl+C should NOT overwrite the
    // saved phase to Interrupted. Doing so makes `--resume` terminate immediately.
    let workspace: Arc<dyn Workspace> =
        Arc::new(MemoryWorkspace::new_test().with_file("PROMPT.md", "# prompt"));

    // Seed an existing checkpoint on disk.
    // Use the full from_params constructor to avoid builder preconditions.
    let run_context = crate::checkpoint::RunContext::new();
    let cli_args = crate::checkpoint::state::CliArgsSnapshotBuilder::new(
        /* developer_iters */ 3, /* reviewer_reviews */ 1, /* review_depth */ None,
        /* isolation_mode */ true,
    )
    .build();

    let developer_agent_config = crate::checkpoint::state::AgentConfigSnapshot::new(
        "dev".to_string(),
        "dev".to_string(),
        "-o".to_string(),
        None,
        /* can_commit */ true,
    );
    let reviewer_agent_config = crate::checkpoint::state::AgentConfigSnapshot::new(
        "rev".to_string(),
        "rev".to_string(),
        "-o".to_string(),
        None,
        /* can_commit */ true,
    );

    let working_dir = workspace.root().to_string_lossy().to_string();
    let mut existing = crate::checkpoint::state::PipelineCheckpoint::from_params(
        crate::checkpoint::state::CheckpointParams {
            phase: crate::checkpoint::PipelinePhase::Development,
            iteration: 1,
            total_iterations: 3,
            reviewer_pass: 0,
            total_reviewer_passes: 1,
            developer_agent: "dev",
            reviewer_agent: "rev",
            cli_args,
            developer_agent_config,
            reviewer_agent_config,
            rebase_state: crate::checkpoint::state::RebaseState::default(),
            git_user_name: None,
            git_user_email: None,
            run_id: &run_context.run_id,
            parent_run_id: run_context.parent_run_id.as_deref(),
            resume_count: run_context.resume_count,
            actual_developer_runs: run_context.actual_developer_runs,
            actual_reviewer_runs: run_context.actual_reviewer_runs,
            working_dir,
            prompt_md_checksum: Some("seed".to_string()),
            config_path: None,
            config_checksum: None,
        },
    );
    existing.interrupted_by_user = false;
    crate::checkpoint::save_checkpoint_with_workspace(&*workspace, &existing)
        .expect("seed checkpoint should save");

    let context = InterruptContext {
        phase: crate::checkpoint::PipelinePhase::Development,
        iteration: 1,
        total_iterations: 3,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        run_context: crate::checkpoint::RunContext::new(),
        execution_history: crate::checkpoint::ExecutionHistory::new(),
        prompt_history: std::collections::HashMap::new(),
        workspace: Arc::clone(&workspace),
    };

    checkpoint::save_interrupt_checkpoint(&context).expect("interrupt save should succeed");

    let checkpoint = load_checkpoint_with_workspace(&*workspace)
        .expect("checkpoint should be readable")
        .expect("checkpoint should exist");

    assert_eq!(
        checkpoint.phase,
        crate::checkpoint::PipelinePhase::Development
    );
    assert!(checkpoint.interrupted_by_user);
}
