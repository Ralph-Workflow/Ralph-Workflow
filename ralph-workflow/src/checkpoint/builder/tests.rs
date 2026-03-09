use super::review_depth_to_string;
use crate::checkpoint::state::{AgentConfigSnapshot, CliArgsSnapshot};
use crate::checkpoint::CheckpointBuilder;
use crate::checkpoint::PipelinePhase;
use crate::config::ReviewDepth;
use crate::prompts::PromptHistoryEntry;

#[test]
fn test_builder_basic() {
    let cli_args = CliArgsSnapshot::new(5, 2, None, true, 2, false, None);
    let dev_config = AgentConfigSnapshot::new("dev".into(), "cmd".into(), "-o".into(), None, true);
    let rev_config = AgentConfigSnapshot::new("rev".into(), "cmd".into(), "-o".into(), None, true);

    let checkpoint = CheckpointBuilder::new()
        .phase(PipelinePhase::Development, 2, 5)
        .reviewer_pass(1, 2)
        .agents("dev", "rev")
        .cli_args(cli_args)
        .developer_config(dev_config)
        .reviewer_config(rev_config)
        .build()
        .unwrap();

    assert_eq!(checkpoint.phase, PipelinePhase::Development);
    assert_eq!(checkpoint.iteration, 2);
    assert_eq!(checkpoint.total_iterations, 5);
    assert_eq!(checkpoint.reviewer_pass, 1);
    assert_eq!(checkpoint.total_reviewer_passes, 2);
}

#[test]
fn test_builder_missing_required_field() {
    // Missing phase - should return None
    let result = CheckpointBuilder::new().build();
    assert!(result.is_none());
}

#[test]
fn test_review_depth_to_string() {
    assert_eq!(review_depth_to_string(ReviewDepth::Standard), "standard");
    assert_eq!(
        review_depth_to_string(ReviewDepth::Comprehensive),
        "comprehensive"
    );
    assert_eq!(review_depth_to_string(ReviewDepth::Security), "security");
    assert_eq!(
        review_depth_to_string(ReviewDepth::Incremental),
        "incremental"
    );
}

#[test]
fn test_review_depth_to_string_returns_static_reference() {
    let value = review_depth_to_string(ReviewDepth::Standard);
    let value_again = review_depth_to_string(ReviewDepth::Standard);

    assert!(std::ptr::eq(value, value_again));
}

#[test]
fn test_builder_with_prompt_history() {
    let cli_args = CliArgsSnapshot::new(5, 2, None, true, 2, false, None);
    let dev_config = AgentConfigSnapshot::new("dev".into(), "cmd".into(), "-o".into(), None, true);
    let rev_config = AgentConfigSnapshot::new("rev".into(), "cmd".into(), "-o".into(), None, true);

    let mut prompts = std::collections::HashMap::new();
    prompts.insert(
        "development_1".to_string(),
        PromptHistoryEntry::from_string("Implement feature X".to_string()),
    );

    let checkpoint = CheckpointBuilder::new()
        .phase(PipelinePhase::Development, 2, 5)
        .reviewer_pass(1, 2)
        .agents("dev", "rev")
        .cli_args(cli_args)
        .developer_config(dev_config)
        .reviewer_config(rev_config)
        .with_prompt_history(prompts)
        .build()
        .unwrap();

    assert_eq!(checkpoint.phase, PipelinePhase::Development);
    assert!(checkpoint.prompt_history.is_some());
    let history = checkpoint.prompt_history.as_ref().unwrap();
    assert_eq!(history.len(), 1);
    assert_eq!(
        history.get("development_1"),
        Some(&PromptHistoryEntry::from_string(
            "Implement feature X".to_string()
        ))
    );
}

#[test]
fn test_builder_with_prompt_history_multiple() {
    let cli_args = CliArgsSnapshot::new(5, 2, None, true, 2, false, None);
    let dev_config = AgentConfigSnapshot::new("dev".into(), "cmd".into(), "-o".into(), None, true);
    let rev_config = AgentConfigSnapshot::new("rev".into(), "cmd".into(), "-o".into(), None, true);

    let mut prompts = std::collections::HashMap::new();
    prompts.insert(
        "development_1".to_string(),
        PromptHistoryEntry::from_string("Implement feature X".to_string()),
    );
    prompts.insert(
        "review_1".to_string(),
        PromptHistoryEntry::from_string("Review the changes".to_string()),
    );

    let checkpoint = CheckpointBuilder::new()
        .phase(PipelinePhase::Development, 2, 5)
        .reviewer_pass(1, 2)
        .agents("dev", "rev")
        .cli_args(cli_args)
        .developer_config(dev_config)
        .reviewer_config(rev_config)
        .with_prompt_history(prompts)
        .build()
        .unwrap();

    assert!(checkpoint.prompt_history.is_some());
    let history = checkpoint.prompt_history.as_ref().unwrap();
    assert_eq!(history.len(), 2);
    assert_eq!(
        history.get("development_1"),
        Some(&PromptHistoryEntry::from_string(
            "Implement feature X".to_string()
        ))
    );
    assert_eq!(
        history.get("review_1"),
        Some(&PromptHistoryEntry::from_string(
            "Review the changes".to_string()
        ))
    );
}

// =========================================================================
// Workspace-based tests (for testability without real filesystem)
// =========================================================================

#[cfg(feature = "test-utils")]
mod workspace_tests {
    use super::*;
    use crate::executor::MockProcessExecutor;
    use crate::workspace::MemoryWorkspace;
    use std::sync::Arc;

    #[test]
    fn test_builder_with_workspace_captures_file_state() {
        // Create a workspace with PROMPT.md file
        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "# Test prompt content");

        let cli_args = CliArgsSnapshot::new(5, 2, None, true, 2, false, None);
        let dev_config =
            AgentConfigSnapshot::new("dev".into(), "cmd".into(), "-o".into(), None, true);
        let rev_config =
            AgentConfigSnapshot::new("rev".into(), "cmd".into(), "-o".into(), None, true);

        let checkpoint = CheckpointBuilder::new()
            .phase(PipelinePhase::Development, 2, 5)
            .reviewer_pass(1, 2)
            .agents("dev", "rev")
            .cli_args(cli_args)
            .developer_config(dev_config)
            .reviewer_config(rev_config)
            .with_executor_from_context(Arc::new(MockProcessExecutor::new()))
            .build_with_workspace(&workspace)
            .unwrap();

        // Verify file system state was captured
        assert!(checkpoint.file_system_state.is_some());
        let fs_state = checkpoint.file_system_state.as_ref().unwrap();

        // PROMPT.md should be captured
        assert!(fs_state.files.contains_key("PROMPT.md"));
        let snapshot = &fs_state.files["PROMPT.md"];
        assert!(snapshot.exists);
        assert_eq!(snapshot.size, 21); // "# Test prompt content"
    }

    #[test]
    fn test_builder_with_workspace_captures_agent_files() {
        // Create a workspace with PROMPT.md and agent files
        let workspace = MemoryWorkspace::new_test()
            .with_file("PROMPT.md", "# Test prompt")
            .with_file(".agent/PLAN.md", "# Plan")
            .with_file(".agent/ISSUES.md", "# Issues");

        let cli_args = CliArgsSnapshot::new(5, 2, None, true, 2, false, None);
        let dev_config =
            AgentConfigSnapshot::new("dev".into(), "cmd".into(), "-o".into(), None, true);
        let rev_config =
            AgentConfigSnapshot::new("rev".into(), "cmd".into(), "-o".into(), None, true);

        let checkpoint = CheckpointBuilder::new()
            .phase(PipelinePhase::Review, 2, 5)
            .reviewer_pass(1, 2)
            .agents("dev", "rev")
            .cli_args(cli_args)
            .developer_config(dev_config)
            .reviewer_config(rev_config)
            .with_executor_from_context(Arc::new(MockProcessExecutor::new()))
            .build_with_workspace(&workspace)
            .unwrap();

        let fs_state = checkpoint.file_system_state.as_ref().unwrap();

        // Both agent files should be captured
        assert!(fs_state.files.contains_key(".agent/PLAN.md"));
        assert!(fs_state.files.contains_key(".agent/ISSUES.md"));

        let plan_snapshot = &fs_state.files[".agent/PLAN.md"];
        assert!(plan_snapshot.exists);

        let issues_snapshot = &fs_state.files[".agent/ISSUES.md"];
        assert!(issues_snapshot.exists);
    }
}
