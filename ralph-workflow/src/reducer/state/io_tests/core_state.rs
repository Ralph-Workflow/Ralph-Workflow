#[test]
fn test_pipeline_state_initial() {
    let state = PipelineState::initial(5, 2);
    assert_eq!(state.phase, PipelinePhase::Planning);
    assert_eq!(state.total_iterations, 5);
    assert_eq!(state.total_reviewer_passes, 2);
    assert!(!state.is_complete());
}

#[test]
fn test_pipeline_state_serialization_does_not_include_cloud_secrets() {
    use crate::config::{CloudConfig, CloudStateConfig, GitAuthMethod, GitRemoteConfig};

    let mut state = PipelineState::initial(1, 0);
    let runtime = CloudConfig {
        enabled: true,
        api_url: Some("https://api.example.com/v1".to_string()),
        api_token: Some("cloud_api_token_should_not_serialize".to_string()),
        run_id: Some("run_123".to_string()),
        heartbeat_interval_secs: 30,
        graceful_degradation: true,
        git_remote: GitRemoteConfig {
            auth_method: GitAuthMethod::Token {
                token: "git_token_should_not_serialize".to_string(),
                username: "x-access-token".to_string(),
            },
            push_branch: Some("main".to_string()),
            create_pr: false,
            pr_title_template: None,
            pr_body_template: None,
            pr_base_branch: None,
            force_push: false,
            remote_name: "origin".to_string(),
        },
    };
    state.cloud = CloudStateConfig::from(&runtime);

    let json = serde_json::to_string(&state).expect("state serialization should succeed");
    assert!(
        !json.contains("cloud_api_token_should_not_serialize"),
        "cloud API token must never be serialized into PipelineState JSON"
    );
    assert!(
        !json.contains("git_token_should_not_serialize"),
        "git token must never be serialized into PipelineState JSON"
    );
}

#[test]
fn test_agent_chain_initial() {
    let chain = AgentChainState::initial();
    assert!(chain.agents.is_empty());
    assert_eq!(chain.current_agent_index, 0);
    assert_eq!(chain.retry_cycle, 0);
}

#[test]
fn test_agent_chain_with_agents() {
    let chain = AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string(), "codex".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        )
        .with_max_cycles(3);

    assert_eq!(chain.agents.len(), 2);
    assert_eq!(chain.current_agent(), Some(&"claude".to_string()));
    assert_eq!(chain.max_cycles, 3);
}

#[test]
fn test_agent_chain_advance_to_next_model() {
    let chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec!["model1".to_string(), "model2".to_string()]],
        AgentRole::Developer,
    );

    let new_chain = chain.advance_to_next_model();
    assert_eq!(new_chain.current_model_index, 1);
    assert_eq!(new_chain.current_model(), Some(&"model2".to_string()));
}

#[test]
fn test_agent_chain_advance_to_next_model_switches_agent_when_models_exhausted() {
    let chain = AgentChainState::initial()
        .with_agents(
            vec!["agent-a".to_string(), "agent-b".to_string()],
            vec![
                vec!["a1".to_string(), "a2".to_string()],
                vec!["b1".to_string()],
            ],
            AgentRole::Developer,
        )
        .with_session_id(Some("session-a".to_string()));

    let chain = chain.advance_to_next_model(); // a1 -> a2
    assert_eq!(chain.current_agent(), Some(&"agent-a".to_string()));
    assert_eq!(chain.current_model(), Some(&"a2".to_string()));

    // Exhausted models for agent-a; should move to agent-b instead of looping models.
    let chain = chain.advance_to_next_model();
    assert_eq!(chain.current_agent(), Some(&"agent-b".to_string()));
    assert_eq!(chain.current_model_index, 0);
    assert_eq!(chain.current_model(), Some(&"b1".to_string()));
    assert_eq!(
        chain.last_session_id, None,
        "Switching agents must clear the previous agent session id"
    );
}

#[test]
fn test_agent_chain_advance_to_next_model_clears_session_when_models_missing() {
    let chain = AgentChainState::initial()
        .with_agents(
            vec!["agent-a".to_string(), "agent-b".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        )
        .with_session_id(Some("session-a".to_string()));

    let next = chain.advance_to_next_model();
    assert_eq!(next.current_agent(), Some(&"agent-b".to_string()));
    assert_eq!(
        next.last_session_id, None,
        "Switching agents must clear the previous agent session id"
    );
}

#[test]
fn test_agent_chain_switch_to_next_agent() {
    let chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string(), "codex".to_string()],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );

    let new_chain = chain.switch_to_next_agent();
    assert_eq!(new_chain.current_agent_index, 1);
    assert_eq!(new_chain.current_agent(), Some(&"codex".to_string()));
    assert_eq!(new_chain.retry_cycle, 0);
}

#[test]
fn test_agent_chain_exhausted() {
    let chain = AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        )
        .with_max_cycles(3);

    let chain = chain.start_retry_cycle();
    let chain = chain.start_retry_cycle();
    let chain = chain.start_retry_cycle();

    assert!(chain.is_exhausted());
}

#[test]
fn test_rebase_state_not_started() {
    let state = RebaseState::NotStarted;
    assert!(!state.is_terminal());
    assert!(state.current_head().is_none());
    assert!(!state.is_in_progress());
}

#[test]
fn test_rebase_state_in_progress() {
    let state = RebaseState::InProgress {
        original_head: "abc123".to_string(),
        target_branch: "main".to_string(),
    };
    assert!(!state.is_terminal());
    assert_eq!(state.current_head(), Some("abc123".to_string()));
    assert!(state.is_in_progress());
}

#[test]
fn test_rebase_state_completed() {
    let state = RebaseState::Completed {
        new_head: "def456".to_string(),
    };
    assert!(state.is_terminal());
    assert_eq!(state.current_head(), Some("def456".to_string()));
    assert!(!state.is_in_progress());
}

fn make_checkpoint_for_state(
    phase: CheckpointPhase,
    rebase_state: CheckpointRebaseState,
) -> PipelineCheckpoint {
    let run_id = uuid::Uuid::new_v4().to_string();
    PipelineCheckpoint::from_params(CheckpointParams {
        phase,
        iteration: 2,
        total_iterations: 5,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        developer_agent: "claude",
        reviewer_agent: "codex",
        cli_args: CliArgsSnapshot::new(5, 2, None, true, 2, false, None),
        developer_agent_config: AgentConfigSnapshot::new(
            "claude".into(),
            "cmd".into(),
            "-o".into(),
            None,
            true,
        ),
        reviewer_agent_config: AgentConfigSnapshot::new(
            "codex".into(),
            "cmd".into(),
            "-o".into(),
            None,
            true,
        ),
        rebase_state,
        git_user_name: None,
        git_user_email: None,
        run_id: &run_id,
        parent_run_id: None,
        resume_count: 0,
        actual_developer_runs: 2,
        actual_reviewer_runs: 0,
        working_dir: "/test/repo".to_string(),
        prompt_md_checksum: None,
        config_path: None,
        config_checksum: None,
    })
}

#[test]
fn test_pipeline_state_from_checkpoint_phase_mapping() {
    let checkpoint = make_checkpoint_for_state(
        CheckpointPhase::Development,
        CheckpointRebaseState::NotStarted,
    );
    let state: PipelineState = checkpoint.into();
    assert_eq!(state.phase, PipelinePhase::Development);
}

#[test]
fn test_pipeline_state_from_checkpoint_caps_legacy_execution_history() {
    use crate::checkpoint::execution_history::{ExecutionHistory, ExecutionStep, StepOutcome};
    use std::collections::VecDeque;

    let mut checkpoint = make_checkpoint_for_state(
        CheckpointPhase::Development,
        CheckpointRebaseState::NotStarted,
    );

    // Create an oversized legacy execution history (simulates old checkpoints)
    // that would otherwise load unbounded history into memory.
    let mut steps = VecDeque::new();
    for i in 0..1500u32 {
        steps.push_back(ExecutionStep::new(
            "Development",
            i,
            "legacy_step",
            StepOutcome::success(None, vec![]),
        ));
    }

    checkpoint.execution_history = Some(ExecutionHistory {
        steps,
        file_snapshots: std::collections::HashMap::new(),
    });

    // Conversion must apply a hard cap so oversized checkpoints cannot force
    // unbounded memory usage.
    let state: PipelineState = checkpoint.into();
    assert!(
        state.execution_history_len() <= 1000,
        "expected execution history to be capped during checkpoint conversion"
    );
}

#[test]
fn test_pipeline_state_from_checkpoint_preserves_prompt_inputs_when_present() {
    let checkpoint = make_checkpoint_for_state(
        CheckpointPhase::Development,
        CheckpointRebaseState::NotStarted,
    );

    let mut checkpoint_value =
        serde_json::to_value(checkpoint).expect("checkpoint should serialize to JSON");

    let prompt_inputs = serde_json::json!({
        "planning": {
            "iteration": 0,
            "prompt": {
                "kind": "Prompt",
                "content_id_sha256": "content-id",
                "consumer_signature_sha256": "consumer-sig",
                "original_bytes": 1,
                "final_bytes": 1,
                "model_budget_bytes": null,
                "inline_budget_bytes": 123,
                "representation": "Inline",
                "reason": "WithinBudgets"
            }
        },
        "development": null,
        "review": null,
        "commit": null,
        "xsd_retry_last_output": null
    });

    checkpoint_value["prompt_inputs"] = prompt_inputs;

    let checkpoint_with_inputs: PipelineCheckpoint =
        serde_json::from_value(checkpoint_value).expect("checkpoint JSON should deserialize");

    let state: PipelineState = checkpoint_with_inputs.into();
    assert!(
        state.prompt_inputs.planning.is_some(),
        "expected prompt_inputs.planning to be restored from checkpoint"
    );
    assert_eq!(
        state
            .prompt_inputs
            .planning
            .as_ref()
            .expect("planning inputs should exist")
            .prompt
            .content_id_sha256,
        "content-id"
    );
}

#[test]
fn test_pipeline_state_from_checkpoint_restores_substitution_log() {
    use crate::prompts::{SubstitutionEntry, SubstitutionLog, SubstitutionSource};

    let mut checkpoint = make_checkpoint_for_state(
        CheckpointPhase::Development,
        CheckpointRebaseState::NotStarted,
    );
    let log = SubstitutionLog {
        template_name: "planning_xml".to_string(),
        substituted: vec![SubstitutionEntry {
            name: "PROMPT".to_string(),
            source: SubstitutionSource::Value,
        }],
        unsubstituted: vec!["PLAN".to_string()],
    };

    checkpoint.last_substitution_log = Some(log);

    let state: PipelineState = checkpoint.into();
    let restored_log = state
        .last_substitution_log
        .expect("substitution log should be restored from checkpoint");
    assert_eq!(restored_log.template_name, "planning_xml");
    assert_eq!(restored_log.unsubstituted, vec!["PLAN".to_string()]);
    assert!(
        state.template_validation_failed,
        "state should derive validation failure from restored log"
    );
    assert_eq!(
        state.template_validation_unsubstituted,
        vec!["PLAN".to_string()]
    );
}

#[test]
fn test_pipeline_state_from_checkpoint_preserves_prompt_permissions() {
    let mut checkpoint = make_checkpoint_for_state(
        CheckpointPhase::Development,
        CheckpointRebaseState::NotStarted,
    );

    checkpoint.prompt_permissions = crate::reducer::state::PromptPermissionsState {
        locked: true,
        restore_needed: true,
        restored: false,
        last_warning: Some("locked with warning".to_string()),
    };

    let state: PipelineState = checkpoint.into();

    assert!(
        state.prompt_permissions.locked,
        "expected locked to be restored from checkpoint"
    );
    assert!(
        state.prompt_permissions.restore_needed,
        "expected restore_needed to be restored from checkpoint"
    );
    assert!(
        !state.prompt_permissions.restored,
        "expected restored to be false from checkpoint"
    );
    assert_eq!(
        state.prompt_permissions.last_warning.as_deref(),
        Some("locked with warning")
    );
}

#[test]
fn test_pipeline_state_from_checkpoint_rebase_conflicts() {
    let checkpoint = make_checkpoint_for_state(
        CheckpointPhase::PreRebaseConflict,
        CheckpointRebaseState::HasConflicts {
            files: vec!["file1.rs".to_string()],
        },
    );
    let state: PipelineState = checkpoint.into();
    assert!(matches!(state.rebase, RebaseState::Conflicted { .. }));
}

#[test]
fn test_commit_state_not_started() {
    let state = CommitState::NotStarted;
    assert!(!state.is_terminal());
}

#[test]
fn test_commit_state_generating() {
    let state = CommitState::Generating {
        attempt: 1,
        max_attempts: 3,
    };
    assert!(!state.is_terminal());
}

#[test]
fn test_commit_state_committed() {
    let state = CommitState::Committed {
        hash: "abc123".to_string(),
    };
    assert!(state.is_terminal());
}

#[test]
fn test_is_complete_during_finalizing() {
    // Finalizing phase should NOT be complete - event loop must continue
    // to execute the RestorePromptPermissions effect
    let state = PipelineState {
        phase: PipelinePhase::Finalizing,
        ..PipelineState::initial(5, 2)
    };
    assert!(
        !state.is_complete(),
        "Finalizing phase should not be complete - event loop must continue"
    );
}

#[test]
fn test_is_complete_after_finalization() {
    // Complete phase IS complete
    let state = PipelineState {
        phase: PipelinePhase::Complete,
        ..PipelineState::initial(5, 2)
    };
    assert!(state.is_complete(), "Complete phase should be complete");
}

#[test]
fn test_interrupted_requires_checkpoint_for_completion() {
    let mut state = PipelineState::initial(1, 1);

    // Transition to Interrupted
    state.phase = PipelinePhase::Interrupted;
    state.previous_phase = Some(PipelinePhase::Development);

    // Without checkpoint saved, should NOT be complete
    assert_eq!(state.checkpoint_saved_count, 0);
    assert!(
        !state.is_complete(),
        "Interrupted phase without checkpoint should not be complete"
    );

    // After checkpoint saved, should be complete
    state.checkpoint_saved_count = 1;
    assert!(
        state.is_complete(),
        "Interrupted phase with checkpoint should be complete"
    );
}

#[test]
fn test_awaiting_dev_fix_not_terminal() {
    let mut state = PipelineState::initial(1, 1);
    state.phase = PipelinePhase::AwaitingDevFix;
    state.previous_phase = Some(PipelinePhase::Development);

    // AwaitingDevFix should never be terminal, even with checkpoint
    assert!(
        !state.is_complete(),
        "AwaitingDevFix phase should not be terminal"
    );

    state.checkpoint_saved_count = 5;
    assert!(
        !state.is_complete(),
        "AwaitingDevFix phase should not be terminal even with checkpoint"
    );
}

#[test]
fn test_interrupted_from_awaiting_dev_fix_is_complete() {
    let mut state = PipelineState::initial(1, 1);
    state.phase = PipelinePhase::Interrupted;
    state.previous_phase = Some(PipelinePhase::AwaitingDevFix);
    state.checkpoint_saved_count = 0;

    assert!(
        state.is_complete(),
        "Interrupted phase from AwaitingDevFix should be complete even without checkpoint, \
         because completion marker was written during TriggerDevFixFlow"
    );
}

#[test]
fn test_interrupted_with_checkpoint_is_complete() {
    let mut state = PipelineState::initial(1, 1);
    state.phase = PipelinePhase::Interrupted;
    state.checkpoint_saved_count = 1;

    assert!(
        state.is_complete(),
        "Interrupted phase with checkpoint should always be complete"
    );
}

#[test]
fn test_interrupted_without_context_not_complete() {
    let mut state = PipelineState::initial(1, 1);
    state.phase = PipelinePhase::Interrupted;
    state.previous_phase = None;
    state.checkpoint_saved_count = 0;

    assert!(
        !state.is_complete(),
        "Interrupted phase without previous_phase and without checkpoint should not be complete \
         (edge case for resumed checkpoints)"
    );
}

#[test]
fn test_is_terminal_interrupted_requires_checkpoint_or_awaiting_dev_fix_marker() {
    let mut state = PipelineState::initial(1, 1);
    state.phase = PipelinePhase::Interrupted;
    state.previous_phase = Some(PipelinePhase::Development);
    state.checkpoint_saved_count = 0;

    assert!(
        !state.is_terminal(),
        "Interrupted without checkpoint and without AwaitingDevFix marker should not be terminal"
    );

    state.checkpoint_saved_count = 1;
    assert!(
        state.is_terminal(),
        "Interrupted with checkpoint should be terminal"
    );
}

#[test]
fn test_is_terminal_interrupted_from_awaiting_dev_fix_is_terminal_even_without_checkpoint() {
    let mut state = PipelineState::initial(1, 1);
    state.phase = PipelinePhase::Interrupted;
    state.previous_phase = Some(PipelinePhase::AwaitingDevFix);
    state.checkpoint_saved_count = 0;

    assert!(
        state.is_terminal(),
        "Interrupted from AwaitingDevFix should be terminal even without checkpoint"
    );
}

#[test]
fn test_is_terminal_complete_is_terminal() {
    let state = PipelineState {
        phase: PipelinePhase::Complete,
        ..PipelineState::initial(1, 1)
    };
    assert!(state.is_terminal());
}
