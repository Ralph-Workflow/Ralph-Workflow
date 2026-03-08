use super::*;
use crate::checkpoint::state::{
    AgentConfigSnapshot, CheckpointParams, CliArgsSnapshot, EnvironmentSnapshot, RebaseState,
};

fn make_test_checkpoint(phase: PipelinePhase, iteration: u32, pass: u32) -> PipelineCheckpoint {
    let cli_args = CliArgsSnapshot::new(5, 3, None, true, 2, false, None);
    let dev_config =
        AgentConfigSnapshot::new("claude".into(), "cmd".into(), "-o".into(), None, true);
    let rev_config =
        AgentConfigSnapshot::new("codex".into(), "cmd".into(), "-o".into(), None, true);
    let run_id = uuid::Uuid::new_v4().to_string();

    PipelineCheckpoint::from_params(CheckpointParams {
        phase,
        iteration,
        total_iterations: 5,
        reviewer_pass: pass,
        total_reviewer_passes: 3,
        developer_agent: "claude",
        reviewer_agent: "codex",
        cli_args,
        developer_agent_config: dev_config,
        reviewer_agent_config: rev_config,
        rebase_state: RebaseState::default(),
        git_user_name: None,
        git_user_email: None,
        run_id: &run_id,
        parent_run_id: None,
        resume_count: 0,
        actual_developer_runs: iteration,
        actual_reviewer_runs: pass,
        working_dir: "/test/repo".to_string(),
        prompt_md_checksum: None,
        config_path: None,
        config_checksum: None,
    })
}

#[test]
fn test_restored_context_from_checkpoint() {
    let checkpoint = make_test_checkpoint(PipelinePhase::Development, 3, 0);
    let context = RestoredContext::from_checkpoint(&checkpoint);

    assert_eq!(context.phase, PipelinePhase::Development);
    assert_eq!(context.resume_iteration, 3);
    assert_eq!(context.total_iterations, 5);
    assert_eq!(context.resume_reviewer_pass, 0);
    assert_eq!(context.developer_agent, "claude");
    assert!(context.cli_args.is_some());
}

#[test]
fn test_should_use_checkpoint_iterations() {
    let checkpoint = make_test_checkpoint(PipelinePhase::Development, 3, 0);
    let context = RestoredContext::from_checkpoint(&checkpoint);

    assert!(context.should_use_checkpoint_iterations());
}

#[test]
fn test_calculate_start_iteration_development() {
    let checkpoint = make_test_checkpoint(PipelinePhase::Development, 3, 0);
    let start = calculate_start_iteration(&checkpoint, 5);
    assert_eq!(start, 3);
}

#[test]
fn test_calculate_start_iteration_later_phase() {
    let checkpoint = make_test_checkpoint(PipelinePhase::Review, 5, 1);
    let start = calculate_start_iteration(&checkpoint, 5);
    assert_eq!(start, 5); // Development complete
}

#[test]
fn test_calculate_start_reviewer_pass() {
    let checkpoint = make_test_checkpoint(PipelinePhase::Review, 5, 2);
    let start = calculate_start_reviewer_pass(&checkpoint, 3);
    assert_eq!(start, 2);
}

#[test]
fn test_calculate_start_reviewer_pass_early_phase() {
    let checkpoint = make_test_checkpoint(PipelinePhase::Development, 3, 0);
    let start = calculate_start_reviewer_pass(&checkpoint, 3);
    assert_eq!(start, 1); // Start from beginning
}

#[test]
fn test_should_skip_phase() {
    let checkpoint = make_test_checkpoint(PipelinePhase::Review, 5, 1);

    // Earlier phases should be skipped
    assert!(should_skip_phase(PipelinePhase::Planning, &checkpoint));
    assert!(should_skip_phase(PipelinePhase::Development, &checkpoint));

    // Current and later phases should not be skipped
    assert!(!should_skip_phase(PipelinePhase::Review, &checkpoint));
    assert!(!should_skip_phase(
        PipelinePhase::FinalValidation,
        &checkpoint
    ));
}

#[test]
fn test_resume_context_from_checkpoint() {
    let checkpoint = make_test_checkpoint(PipelinePhase::Development, 3, 1);
    let resume_ctx = checkpoint.resume_context();

    assert_eq!(resume_ctx.phase, PipelinePhase::Development);
    assert_eq!(resume_ctx.iteration, 3);
    assert_eq!(resume_ctx.total_iterations, 5);
    assert_eq!(resume_ctx.reviewer_pass, 1);
    assert_eq!(resume_ctx.total_reviewer_passes, 3);
    assert_eq!(resume_ctx.resume_count, 0);
    assert_eq!(resume_ctx.run_id, checkpoint.run_id);
    assert!(resume_ctx.prompt_history.is_none());
}

#[test]
fn test_resume_context_phase_name_development() {
    let ctx = ResumeContext {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        reviewer_pass: 0,
        total_reviewer_passes: 3,
        resume_count: 0,
        rebase_state: RebaseState::default(),
        run_id: "test".to_string(),
        prompt_history: None,
        execution_history: None,
    };

    assert_eq!(ctx.phase_name(), "Development iteration 3/5");
}

#[test]
fn test_resume_context_phase_name_review() {
    let ctx = ResumeContext {
        phase: PipelinePhase::Review,
        iteration: 5,
        total_iterations: 5,
        reviewer_pass: 1,
        total_reviewer_passes: 3,
        resume_count: 0,
        rebase_state: RebaseState::default(),
        run_id: "test".to_string(),
        prompt_history: None,
        execution_history: None,
    };

    assert_eq!(ctx.phase_name(), "Review (pass 2/3)");
}

#[test]
fn test_restore_environment_skips_sensitive_vars() {
    // Arrange: checkpoint snapshot with safe and sensitive vars
    let mut checkpoint = make_test_checkpoint(PipelinePhase::Development, 1, 0);
    let mut snapshot = EnvironmentSnapshot::default();
    snapshot
        .ralph_vars
        .insert("RALPH_SAFE_SETTING".to_string(), "ok".to_string());
    snapshot
        .ralph_vars
        .insert("RALPH_API_TOKEN".to_string(), "secret".to_string());
    snapshot
        .other_vars
        .insert("EDITOR".to_string(), "vim".to_string());
    snapshot
        .other_vars
        .insert("GIT_PASSWORD".to_string(), "nope".to_string());
    checkpoint.env_snapshot = Some(snapshot);

    // Act: use injectable impl to spy on what would be set (no real env mutation)
    let mut set_calls: Vec<(String, String)> = Vec::new();
    let restored = restore_environment_impl(&checkpoint, |k, v| {
        set_calls.push((k.to_string(), v.to_string()));
    });

    // Assert: safe vars set, sensitive vars skipped
    assert_eq!(restored, 2);
    assert!(
        set_calls
            .iter()
            .any(|(k, v)| k == "RALPH_SAFE_SETTING" && v == "ok"),
        "RALPH_SAFE_SETTING should be restored; got: {set_calls:?}"
    );
    assert!(
        set_calls.iter().any(|(k, v)| k == "EDITOR" && v == "vim"),
        "EDITOR should be restored; got: {set_calls:?}"
    );
    assert!(
        !set_calls.iter().any(|(k, _)| k == "RALPH_API_TOKEN"),
        "RALPH_API_TOKEN is sensitive and must be skipped; got: {set_calls:?}"
    );
    assert!(
        !set_calls.iter().any(|(k, _)| k == "GIT_PASSWORD"),
        "GIT_PASSWORD is sensitive and must be skipped; got: {set_calls:?}"
    );
}
