mod tests {
    use super::*;
    use crate::phases::commit::diff_truncation::{
        truncate_diff_if_large, truncate_lines_to_fit, CLAUDE_MAX_PROMPT_SIZE, GLM_MAX_PROMPT_SIZE,
        MAX_SAFE_PROMPT_SIZE,
    };
    use crate::prompts::SubstitutionLog;
    use crate::reducer::event::{CommitEvent, PipelineEvent};

    #[test]
    fn test_truncate_diff_if_large() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let large_diff = "diff --git a/src/main.rs b/src/main.rs\n".repeat(1000);
        let truncated = truncate_diff_if_large(&large_diff, 10_000);

        assert!(truncated.len() <= 10_000 + 200);
        assert!(truncated.contains("[Truncated:"));
    }

    #[test]
    fn test_truncate_diff_no_truncation_needed() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let small_diff = "diff --git a/src/main.rs b/src/main.rs\n+change\n";
        let truncated = truncate_diff_if_large(small_diff, 10_000);

        assert_eq!(truncated, small_diff);
    }

    #[test]
    fn test_truncate_diff_preserves_structure() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let diff = "diff --git a/src/main.rs b/src/main.rs\n+change1\n\
            diff --git a/src/lib.rs b/src/lib.rs\n+change2\n";
        let truncated = truncate_diff_if_large(diff, 10_000);

        assert!(truncated.contains("diff --git a/src/main.rs"));
        assert!(truncated.contains("diff --git a/src/lib.rs"));
    }

    #[test]
    fn test_truncate_diff_very_small_limit() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        // Diff must exceed the limit (80) to trigger truncation.
        let diff =
            "diff --git a/src/main.rs b/src/main.rs\n+change with enough content here to exceed\n";
        let truncated = truncate_diff_if_large(diff, 80);

        assert!(truncated.len() <= 80 + 200);
        assert!(truncated.contains("[Truncated:"));
    }

    #[test]
    fn test_truncate_lines_to_fit() {
        let lines = vec![
            "line1".to_string(),
            "line2".to_string(),
            "line3".to_string(),
        ];
        let max_size = 12;

        let truncated = truncate_lines_to_fit(&lines, max_size);

        assert!(truncated.join("\n").len() <= max_size);
    }

    #[test]
    fn test_truncate_lines_to_fit_no_truncation() {
        let lines = vec!["a".to_string(), "b".to_string()];
        let max_size = 100;

        let truncated = truncate_lines_to_fit(&lines, max_size);

        assert_eq!(truncated.len(), 2);
    }

    #[test]
    fn test_effective_model_budget_bytes_single_agent() {
        let agents = vec!["claude".to_string()];
        assert_eq!(
            effective_model_budget_bytes(&agents),
            CLAUDE_MAX_PROMPT_SIZE
        );
    }

    #[test]
    fn test_effective_model_budget_bytes_multiple_agents() {
        let agents = vec!["claude".to_string(), "glm".to_string()];
        assert_eq!(effective_model_budget_bytes(&agents), GLM_MAX_PROMPT_SIZE);
    }

    #[test]
    fn test_effective_model_budget_bytes_no_agents() {
        let agents: Vec<String> = vec![];
        assert_eq!(effective_model_budget_bytes(&agents), MAX_SAFE_PROMPT_SIZE);
    }

    #[test]
    fn test_commit_prompt_content_id_includes_residual_files() {
        let residual = vec!["src/lib.rs".to_string(), "Cargo.toml".to_string()];
        let with_residual = commit_prompt_content_id("diff123", "consumer456", &residual);
        let without_residual = commit_prompt_content_id("diff123", "consumer456", &[]);

        assert_ne!(with_residual, without_residual);
    }

    #[test]
    fn test_prepend_residual_files_context_formats_note() {
        let base_prompt = "Base prompt";
        let residual = vec!["src/lib.rs".to_string(), "Cargo.toml".to_string()];

        let updated = prepend_residual_files_context(base_prompt, &residual);

        assert!(updated.contains("must be accounted for in this commit run"));
        assert!(updated.contains("- src/lib.rs"));
        assert!(updated.contains("- Cargo.toml"));
        assert!(updated.ends_with(base_prompt));
    }

    #[test]
    fn test_diff_unavailable_investigation_instructions_contains_error() {
        let message = diff_unavailable_investigation_instructions("boom");

        assert!(message.contains("git diff"));
        assert!(message.contains("boom"));
        assert!(message.contains("<ralph-commit>"));
    }

    #[test]
    fn test_commit_outcome_event_prefers_message() {
        let event = commit_outcome_event_from_validated(
            Some("feat: add parser".to_string()),
            Some("should be ignored".to_string()),
            7,
        );

        assert!(matches!(
            event,
            PipelineEvent::Commit(CommitEvent::MessageGenerated {
                message,
                attempt: 7
            }) if message == "feat: add parser"
        ));
    }

    #[test]
    fn test_commit_outcome_event_uses_reason_without_message() {
        let event = commit_outcome_event_from_validated(None, Some("invalid xml".to_string()), 3);

        assert!(matches!(
            event,
            PipelineEvent::Commit(CommitEvent::MessageValidationFailed {
                reason,
                attempt: 3
            }) if reason == "invalid xml"
        ));
    }

    #[test]
    fn test_commit_outcome_event_reports_missing_message_and_reason() {
        let event = commit_outcome_event_from_validated(None, None, 1);

        assert!(matches!(
            event,
            PipelineEvent::Commit(CommitEvent::GenerationFailed { reason })
                if reason == "Commit validation outcome missing message and reason"
        ));
    }

    #[test]
    fn test_commit_representation_and_reason_prefers_model_budget_exceeded() {
        let (representation, reason) = commit_representation_and_reason(
            123,
            100,
            true,
            std::path::Path::new(".agent/tmp/commit_diff.model_safe.txt"),
        );

        assert!(matches!(
            representation,
            crate::reducer::state::PromptInputRepresentation::FileReference { .. }
        ));
        assert_eq!(
            reason,
            crate::reducer::state::PromptMaterializationReason::ModelBudgetExceeded
        );
    }

    #[test]
    fn test_commit_representation_and_reason_selects_inline_within_budgets() {
        let (representation, reason) = commit_representation_and_reason(
            64,
            100,
            false,
            std::path::Path::new(".agent/tmp/commit_diff.model_safe.txt"),
        );

        assert_eq!(
            representation,
            crate::reducer::state::PromptInputRepresentation::Inline
        );
        assert_eq!(
            reason,
            crate::reducer::state::PromptMaterializationReason::WithinBudgets
        );
    }

    #[test]
    fn test_base_prompt_for_same_agent_retry_strips_existing_retry_header() {
        use crate::reducer::state::{ContinuationState, SameAgentRetryReason};
        let continuation = ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            ..ContinuationState::default()
        };
        let preamble =
            crate::reducer::boundary::retry_guidance::same_agent_retry_preamble(&continuation);
        let original = "Original base prompt";
        let previous_prompt = format!("{preamble}\n\n{original}");
        let generated = "Freshly generated prompt";

        let (base_prompt, should_validate) =
            base_prompt_for_same_agent_retry(Some(&previous_prompt), generated);

        assert_eq!(base_prompt, "Original base prompt");
        assert!(!should_validate);
    }

    #[test]
    fn test_prompt_captured_event_returns_none_when_prompt_was_replayed() {
        let event = prompt_captured_event("scope-key", "prompt body", "content-id", true);

        assert!(event.is_none());
    }

    #[test]
    fn test_prompt_captured_event_returns_prompt_input_when_fresh() {
        let event = prompt_captured_event("scope-key", "prompt body", "content-id", false)
            .expect("fresh prompts must emit PromptCaptured event");

        assert!(matches!(
            event,
            PipelineEvent::PromptInput(crate::reducer::event::PromptInputEvent::PromptCaptured {
                key,
                content,
                content_id: Some(id)
            }) if key == "scope-key" && content == "prompt body" && id == "content-id"
        ));
    }

    #[test]
    fn test_commit_prompt_prepared_result_adds_template_rendered_event_when_present() {
        let rendered_log = SubstitutionLog {
            template_name: "commit_message".to_string(),
            substituted: Vec::new(),
            unsubstituted: Vec::new(),
        };

        let result = commit_prompt_prepared_result(
            2,
            crate::reducer::event::PipelinePhase::Planning,
            "scope-key".to_string(),
            false,
            None,
            Some(rendered_log),
            "commit_message",
        );

        assert!(matches!(
            result.event,
            PipelineEvent::Commit(CommitEvent::PromptPrepared { attempt: 2 })
        ));
        assert!(matches!(
            result.ui_events.as_slice(),
            [
                crate::reducer::ui_event::UIEvent::PhaseTransition {
                    to: crate::reducer::event::PipelinePhase::CommitMessage,
                    ..
                },
                crate::reducer::ui_event::UIEvent::PromptReplayHit { key, was_replayed: false }
            ] if key == "scope-key"
        ));
        assert!(matches!(
            result.additional_events.as_slice(),
            [PipelineEvent::PromptInput(
                crate::reducer::event::PromptInputEvent::TemplateRendered {
                    phase: crate::reducer::event::PipelinePhase::CommitMessage,
                    template_name,
                    ..
                }
            )] if template_name == "commit_message"
        ));
    }

    #[test]
    fn test_commit_submission_retry_prompt_requires_resubmission_not_plain_output() {
        let base_prompt = "Base commit prompt";

        let retry =
            commit_submission_retry_prompt(base_prompt, "mcp__ralph__ralph_submit_artifact");

        assert!(retry.contains("Base commit prompt"));
        assert!(retry.contains("mcp__ralph__ralph_submit_artifact"));
        assert!(retry.contains("Do NOT print the commit message"));
        assert!(retry.contains("submit the artifact"));
        assert!(
            retry.contains("Do NOT run git commit"),
            "retry prompt must preserve orchestrator-owned commit execution boundary"
        );
    }

    #[test]
    fn test_commit_submission_retry_prompt_keeps_artifact_only_boundary_for_any_tool_name() {
        let base_prompt = "Base commit prompt";

        let retry = commit_submission_retry_prompt(base_prompt, "mcp__ralph__write_file");

        assert!(
            retry.contains("submit the artifact"),
            "retry prompt must enforce artifact-only handoff even when previous tool was not submit_artifact"
        );
        assert!(
            retry.contains("Do NOT run git commit"),
            "retry prompt must preserve orchestrator-owned commit execution boundary"
        );
    }
}
