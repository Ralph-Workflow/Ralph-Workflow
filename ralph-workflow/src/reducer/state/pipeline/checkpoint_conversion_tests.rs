#[cfg(test)]
mod tests {
    use super::*;
    use crate::reducer::event::PipelinePhase;
    use serde_json::Value;

    #[test]
    fn test_clear_planning_flags() {
        let mut state = PipelineState::initial(1, 0);
        state.planning_prompt_prepared_iteration = Some(1);
        state.planning_agent_invoked_iteration = Some(1);
        state.planning_xml_extracted_iteration = Some(1);
        state.planning_validated_outcome = Some(crate::reducer::state::PlanningValidatedOutcome {
            iteration: 1,
            valid: true,
            markdown: None,
        });

        let cleared = state.clear_phase_flags(PipelinePhase::Planning);

        assert_eq!(cleared.planning_prompt_prepared_iteration, None);
        assert_eq!(cleared.planning_agent_invoked_iteration, None);
        assert_eq!(cleared.planning_xml_extracted_iteration, None);
        assert_eq!(cleared.planning_validated_outcome, None);
    }

    #[test]
    fn test_clear_development_flags() {
        let mut state = PipelineState::initial(1, 0);
        state.development_context_prepared_iteration = Some(2);
        state.development_agent_invoked_iteration = Some(2);
        state.analysis_agent_invoked_iteration = Some(2);
        state.development_validated_outcome =
            Some(crate::reducer::state::DevelopmentValidatedOutcome {
                iteration: 2,
                status: crate::reducer::state::DevelopmentStatus::Completed,
                summary: "test".to_string(),
                files_changed: None,
                next_steps: None,
            });

        let cleared = state.clear_phase_flags(PipelinePhase::Development);

        assert_eq!(cleared.development_context_prepared_iteration, None);
        assert_eq!(cleared.development_agent_invoked_iteration, None);
        assert_eq!(cleared.analysis_agent_invoked_iteration, None);
        assert_eq!(cleared.development_validated_outcome, None);
    }

    #[test]
    fn test_clear_phase_flags_routes_to_correct_helper() {
        let mut state = PipelineState::initial(1, 0);
        state.planning_agent_invoked_iteration = Some(1);
        state.development_agent_invoked_iteration = Some(1);

        // Clear Planning should only affect Planning flags
        let cleared = state.clear_phase_flags(PipelinePhase::Planning);
        assert_eq!(cleared.planning_agent_invoked_iteration, None);
        assert_eq!(cleared.development_agent_invoked_iteration, Some(1));

        // Clear Development should only affect Development flags
        let cleared = state.clear_phase_flags(PipelinePhase::Development);
        assert_eq!(cleared.planning_agent_invoked_iteration, Some(1));
        assert_eq!(cleared.development_agent_invoked_iteration, None);
    }

    #[test]
    fn test_reset_iteration_decrements_counter() {
        let mut state = PipelineState::initial(5, 0);
        state.iteration = 3;
        state.planning_agent_invoked_iteration = Some(3);
        state.development_agent_invoked_iteration = Some(3);

        let reset = state.reset_iteration();

        assert_eq!(reset.iteration, 2);
        assert_eq!(reset.phase, PipelinePhase::Planning);
        assert_eq!(reset.planning_agent_invoked_iteration, None);
        assert_eq!(reset.development_agent_invoked_iteration, None);
    }

    #[test]
    fn test_reset_iteration_floor_at_zero() {
        let mut state = PipelineState::initial(1, 0);
        state.iteration = 0;

        let reset = state.reset_iteration();

        assert_eq!(reset.iteration, 0); // Floor at 0
    }

    #[test]
    fn test_reset_to_iteration_zero() {
        let mut state = PipelineState::initial(10, 0);
        state.iteration = 5;
        state.planning_agent_invoked_iteration = Some(5);
        state.development_agent_invoked_iteration = Some(5);

        let reset = state.reset_to_iteration_zero();

        assert_eq!(reset.iteration, 0);
        assert_eq!(reset.phase, PipelinePhase::Planning);
        assert_eq!(reset.planning_agent_invoked_iteration, None);
        assert_eq!(reset.development_agent_invoked_iteration, None);
    }

    #[test]
    fn test_phase_reset_preserves_unrelated_state() {
        let mut state = PipelineState::initial(10, 3);
        state.iteration = 2;
        state.reviewer_pass = 1;
        state.total_iterations = 10;
        state.planning_agent_invoked_iteration = Some(2);

        let cleared = state.clear_phase_flags(PipelinePhase::Planning);

        // Phase flags cleared
        assert_eq!(cleared.planning_agent_invoked_iteration, None);

        // Global counters preserved
        assert_eq!(cleared.iteration, 2);
        assert_eq!(cleared.reviewer_pass, 1);
        assert_eq!(cleared.total_iterations, 10);
    }

    #[test]
    fn checkpoint_resume_preserves_recovery_escalation_state() {
        use crate::checkpoint::state::{AgentConfigSnapshot, CliArgsSnapshot, RebaseState};
        use crate::checkpoint::{CheckpointBuilder, PipelinePhase as CheckpointPhase};

        let checkpoint = CheckpointBuilder::new()
            .phase(CheckpointPhase::AwaitingDevFix, 2, 5)
            .reviewer_pass(1, 2)
            .agents("dev", "rev")
            .cli_args(CliArgsSnapshot {
                developer_iters: 5,
                reviewer_reviews: 2,
                review_depth: None,
                isolation_mode: true,
                verbosity: 2,
                show_streaming_metrics: false,
                reviewer_json_parser: None,
            })
            .developer_config(AgentConfigSnapshot {
                name: "dev".to_string(),
                cmd: "dev".to_string(),
                output_flag: "-o".to_string(),
                yolo_flag: None,
                can_commit: true,
                model_override: None,
                provider_override: None,
                context_level: 1,
            })
            .reviewer_config(AgentConfigSnapshot {
                name: "rev".to_string(),
                cmd: "rev".to_string(),
                output_flag: "-o".to_string(),
                yolo_flag: None,
                can_commit: true,
                model_override: None,
                provider_override: None,
                context_level: 1,
            })
            .rebase_state(RebaseState::default())
            .git_identity(None, None)
            .build()
            .expect("checkpoint should build");

        let mut json: Value = serde_json::to_value(&checkpoint).expect("checkpoint to json");
        let obj = json.as_object_mut().expect("checkpoint json object");
        obj.insert("dev_fix_attempt_count".to_string(), Value::from(7));
        obj.insert("recovery_escalation_level".to_string(), Value::from(3));
        obj.insert(
            "failed_phase_for_recovery".to_string(),
            Value::String("CommitMessage".to_string()),
        );

        let checkpoint: PipelineCheckpoint =
            serde_json::from_value(json).expect("checkpoint json should deserialize");

        let state = PipelineState::from_checkpoint_with_execution_history_limit(checkpoint, 1000);

        assert_eq!(state.dev_fix_attempt_count, 7);
        assert_eq!(state.recovery_escalation_level, 3);
        assert_eq!(
            state.failed_phase_for_recovery,
            Some(PipelinePhase::CommitMessage)
        );
    }

    #[test]
    fn checkpoint_resume_preserves_cloud_state_when_present() {
        use crate::checkpoint::state::CloudCheckpointState;
        use crate::checkpoint::state::{AgentConfigSnapshot, CliArgsSnapshot, RebaseState};
        use crate::checkpoint::{CheckpointBuilder, PipelinePhase as CheckpointPhase};
        use crate::config::{CloudStateConfig, GitAuthStateMethod, GitRemoteStateConfig};

        let mut checkpoint = CheckpointBuilder::new()
            .phase(CheckpointPhase::Development, 1, 3)
            .reviewer_pass(0, 1)
            .agents("dev", "rev")
            .cli_args(CliArgsSnapshot {
                developer_iters: 3,
                reviewer_reviews: 1,
                review_depth: None,
                isolation_mode: true,
                verbosity: 2,
                show_streaming_metrics: false,
                reviewer_json_parser: None,
            })
            .developer_config(AgentConfigSnapshot {
                name: "dev".to_string(),
                cmd: "dev".to_string(),
                output_flag: "-o".to_string(),
                yolo_flag: None,
                can_commit: true,
                model_override: None,
                provider_override: None,
                context_level: 1,
            })
            .reviewer_config(AgentConfigSnapshot {
                name: "rev".to_string(),
                cmd: "rev".to_string(),
                output_flag: "-o".to_string(),
                yolo_flag: None,
                can_commit: true,
                model_override: None,
                provider_override: None,
                context_level: 1,
            })
            .rebase_state(RebaseState::default())
            .git_identity(None, None)
            .build()
            .expect("checkpoint should build");

        checkpoint.cloud_state = Some(CloudCheckpointState {
            cloud: CloudStateConfig {
                enabled: true,
                api_url: Some("https://api.example.com".to_string()),
                run_id: Some("run_123".to_string()),
                heartbeat_interval_secs: 30,
                graceful_degradation: true,
                git_remote: GitRemoteStateConfig {
                    auth_method: GitAuthStateMethod::SshKey { key_path: None },
                    push_branch: "feature/x".to_string(),
                    create_pr: true,
                    pr_title_template: None,
                    pr_body_template: None,
                    pr_base_branch: Some("main".to_string()),
                    force_push: false,
                    remote_name: "origin".to_string(),
                },
            },
            pending_push_commit: Some("abc123".to_string()),
            git_auth_configured: true,
            pr_created: true,
            pr_url: Some("https://example.com/pr/1".to_string()),
            pr_number: Some(1),
            push_count: 2,
            push_retry_count: 1,
            last_push_error: Some("Git push failed: https://<redacted>@github.com".to_string()),
            unpushed_commits: vec!["deadbeef".to_string()],
            last_pushed_commit: Some("beadfeed".to_string()),
        });

        let state = PipelineState::from_checkpoint_with_execution_history_limit(checkpoint, 1000);

        assert!(state.cloud.enabled);
        assert_eq!(state.pending_push_commit.as_deref(), Some("abc123"));
        assert!(state.git_auth_configured);
        assert!(state.pr_created);
        assert_eq!(state.pr_url.as_deref(), Some("https://example.com/pr/1"));
        assert_eq!(state.pr_number, Some(1));
        assert_eq!(state.push_count, 2);
        assert_eq!(state.push_retry_count, 1);
        assert!(state
            .last_push_error
            .as_deref()
            .is_some_and(|e| e.contains("<redacted>")));
        assert!(state.unpushed_commits.iter().any(|c| c == "deadbeef"));
        assert_eq!(state.last_pushed_commit.as_deref(), Some("beadfeed"));
    }

    #[test]
    fn checkpoint_resume_preserves_commit_residual_state() {
        use crate::checkpoint::state::{AgentConfigSnapshot, CliArgsSnapshot, RebaseState};
        use crate::checkpoint::{CheckpointBuilder, PipelinePhase as CheckpointPhase};

        let mut checkpoint = CheckpointBuilder::new()
            .phase(CheckpointPhase::CommitMessage, 1, 1)
            .reviewer_pass(0, 0)
            .agents("dev", "rev")
            .cli_args(CliArgsSnapshot {
                developer_iters: 1,
                reviewer_reviews: 0,
                review_depth: None,
                isolation_mode: true,
                verbosity: 2,
                show_streaming_metrics: false,
                reviewer_json_parser: None,
            })
            .developer_config(AgentConfigSnapshot {
                name: "dev".to_string(),
                cmd: "dev".to_string(),
                output_flag: "-o".to_string(),
                yolo_flag: None,
                can_commit: true,
                model_override: None,
                provider_override: None,
                context_level: 1,
            })
            .reviewer_config(AgentConfigSnapshot {
                name: "rev".to_string(),
                cmd: "rev".to_string(),
                output_flag: "-o".to_string(),
                yolo_flag: None,
                can_commit: true,
                model_override: None,
                provider_override: None,
                context_level: 1,
            })
            .rebase_state(RebaseState::default())
            .git_identity(None, None)
            .build()
            .expect("checkpoint should build");

        checkpoint.commit_is_second_pass = true;
        checkpoint.commit_residual_files = vec!["src/leftover.rs".to_string()];

        let state = PipelineState::from_checkpoint_with_execution_history_limit(checkpoint, 1000);

        assert!(
            state.commit_is_second_pass,
            "commit_is_second_pass must survive resume when pass-2 was in progress"
        );
        assert_eq!(
            state.commit_residual_files,
            vec!["src/leftover.rs".to_string()],
            "commit_residual_files must survive resume for unattended carry-forward"
        );
    }

    #[test]
    fn checkpoint_resume_preserves_selective_commit_context_for_residual_handling() {
        use crate::checkpoint::state::{AgentConfigSnapshot, CliArgsSnapshot, RebaseState};
        use crate::checkpoint::{CheckpointBuilder, PipelinePhase as CheckpointPhase};
        use crate::reducer::state::pipeline::{ExcludedFile, ExcludedFileReason};

        let mut checkpoint = CheckpointBuilder::new()
            .phase(CheckpointPhase::CommitMessage, 1, 1)
            .reviewer_pass(0, 0)
            .agents("dev", "rev")
            .cli_args(CliArgsSnapshot {
                developer_iters: 1,
                reviewer_reviews: 0,
                review_depth: None,
                isolation_mode: true,
                verbosity: 2,
                show_streaming_metrics: false,
                reviewer_json_parser: None,
            })
            .developer_config(AgentConfigSnapshot {
                name: "dev".to_string(),
                cmd: "dev".to_string(),
                output_flag: "-o".to_string(),
                yolo_flag: None,
                can_commit: true,
                model_override: None,
                provider_override: None,
                context_level: 1,
            })
            .reviewer_config(AgentConfigSnapshot {
                name: "rev".to_string(),
                cmd: "rev".to_string(),
                output_flag: "-o".to_string(),
                yolo_flag: None,
                can_commit: true,
                model_override: None,
                provider_override: None,
                context_level: 1,
            })
            .rebase_state(RebaseState::default())
            .git_identity(None, None)
            .build()
            .expect("checkpoint should build");

        checkpoint.commit_selected_files = vec!["src/lib.rs".to_string()];
        checkpoint.commit_excluded_files = vec![ExcludedFile {
            path: ".agent/tmp/trace.log".to_string(),
            reason: ExcludedFileReason::InternalIgnore,
        }];

        let state = PipelineState::from_checkpoint_with_execution_history_limit(checkpoint, 1000);

        assert_eq!(state.commit_selected_files, vec!["src/lib.rs".to_string()]);
        assert_eq!(
            state.commit_excluded_files,
            vec![ExcludedFile {
                path: ".agent/tmp/trace.log".to_string(),
                reason: ExcludedFileReason::InternalIgnore,
            }]
        );
    }
}
