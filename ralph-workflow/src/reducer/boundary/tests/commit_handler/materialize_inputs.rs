use super::super::common::TestFixture;
use crate::reducer::event::PipelineEvent;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{AgentChainState, CommitState, PipelineState};
use crate::workspace::MemoryWorkspace;

#[test]
fn test_materialize_commit_inputs_invalidates_diff_when_commit_diff_missing() {
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.commit_diff_prepared = true;
    handler.state.commit_diff_empty = false;
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    // `.agent/tmp/commit_diff.txt` is intentionally missing. The effect should not abort;
    // it should invalidate diff-prepared state to force rerunning CheckCommitDiff.
    let result = handler
        .materialize_commit_inputs(&ctx, 1)
        .expect("materialize_commit_inputs should return an EffectResult");

    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(crate::reducer::event::CommitEvent::DiffInvalidated { .. })
        ),
        "Expected DiffInvalidated event when commit_diff.txt is missing, got {:?}",
        result.event
    );
}

#[test]
fn test_materialize_commit_inputs_uses_min_model_budget_across_agent_chain() {
    use crate::reducer::event::PromptInputEvent;

    let large_diff = format!("diff --git a/a b/a\n+{}\n", "x".repeat(250_000));
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/commit_diff.txt", &large_diff)
        .with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec![
            "claude".to_string(),
            "qwen".to_string(),
            "default-agent".to_string(),
        ],
        vec![vec![], vec![], vec![]],
        crate::agents::AgentRole::Commit,
    );

    let result = handler
        .materialize_commit_inputs(&ctx, 1)
        .expect("materialize_commit_inputs should succeed");

    let materialized = match result.event {
        PipelineEvent::PromptInput(PromptInputEvent::CommitInputsMaterialized {
            attempt,
            diff,
        }) => {
            assert_eq!(attempt, 1);
            diff
        }
        other => panic!("unexpected event: {other:?}"),
    };

    assert_eq!(
        materialized.model_budget_bytes,
        Some(100_000),
        "expected model budget to be min across agent chain (qwen-like => 100KB)"
    );
    assert!(
        fixture
            .workspace
            .was_written(".agent/tmp/commit_diff.model_safe.txt"),
        "materialized model-safe diff should be written once to a canonical path"
    );
    assert!(
        materialized.final_bytes <= 100_000,
        "model-safe diff should not exceed the effective model budget"
    );
}

#[test]
fn test_materialize_commit_inputs_includes_size_info_in_ui_events() {
    use crate::reducer::event::PromptInputEvent;
    use crate::reducer::ui_event::UIEvent;

    // Create diff that exceeds model budget (100KB for qwen) but not inline budget
    let large_diff = format!("diff --git a/a b/a\n+{}\n", "x".repeat(150_000));
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/commit_diff.txt", &large_diff)
        .with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    // Use qwen to trigger model budget truncation (100KB budget)
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["qwen".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    let result = handler
        .materialize_commit_inputs(&ctx, 1)
        .expect("materialize_commit_inputs should succeed");

    // Verify main event has correct sizes
    match &result.event {
        PipelineEvent::PromptInput(PromptInputEvent::CommitInputsMaterialized { diff, .. }) => {
            assert!(
                diff.original_bytes > 100_000,
                "original bytes should exceed budget"
            );
            assert!(
                diff.final_bytes <= 100_000,
                "final bytes should be within budget"
            );
        }
        other => panic!("unexpected event: {other:?}"),
    }

    // Verify UI event includes size information
    let has_size_ui_event = result.ui_events.iter().any(|event| {
        if let UIEvent::AgentActivity { message, .. } = event {
            message.contains("KB") && message.contains("->")
        } else {
            false
        }
    });
    assert!(
        has_size_ui_event,
        "UI events should include size information when truncation occurs"
    );
}

#[test]
fn test_materialize_commit_inputs_records_correct_materialization_reason() {
    use crate::reducer::event::PromptInputEvent;
    use crate::reducer::state::PromptMaterializationReason;

    // Create diff that exceeds model budget
    let large_diff = format!("diff --git a/a b/a\n+{}\n", "x".repeat(150_000));
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/commit_diff.txt", &large_diff)
        .with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    // Use qwen to trigger model budget truncation
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["qwen".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    let result = handler
        .materialize_commit_inputs(&ctx, 1)
        .expect("materialize_commit_inputs should succeed");

    match &result.event {
        PipelineEvent::PromptInput(PromptInputEvent::CommitInputsMaterialized { diff, .. }) => {
            assert!(
                matches!(
                    diff.reason,
                    PromptMaterializationReason::ModelBudgetExceeded
                ),
                "reason should be ModelBudgetExceeded when diff is truncated for model budget"
            );
        }
        other => panic!("unexpected event: {other:?}"),
    }
}

#[test]
fn test_materialize_commit_inputs_records_combined_reason_when_truncated_and_referenced() {
    use crate::reducer::event::PromptInputEvent;
    use crate::reducer::state::{PromptInputRepresentation, PromptMaterializationReason};
    use std::path::PathBuf;

    // Create diff that exceeds both model budget (claude: 300KB) and inline budget (~100KB).
    // Use many medium-sized lines so truncation still leaves a large payload.
    let mut large_diff = String::from("diff --git a/a b/a\n");
    for _ in 0..6_000 {
        large_diff.push('+');
        large_diff.push_str(&"x".repeat(100));
        large_diff.push('\n');
    }
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/commit_diff.txt", &large_diff)
        .with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    // Use claude to get a large model budget while still exceeding inline budget.
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude-opus".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    let result = handler
        .materialize_commit_inputs(&ctx, 1)
        .expect("materialize_commit_inputs should succeed");

    match &result.event {
        PipelineEvent::PromptInput(PromptInputEvent::CommitInputsMaterialized { diff, .. }) => {
            assert!(
                matches!(
                    diff.representation,
                    PromptInputRepresentation::FileReference { .. }
                ),
                "diff should be referenced by file when still above inline budget"
            );
            assert!(
                matches!(
                    diff.reason,
                    PromptMaterializationReason::ModelBudgetExceeded
                ),
                "reason should reflect model truncation even when a file reference is used"
            );
            if let PromptInputRepresentation::FileReference { path } = &diff.representation {
                assert!(
                    !path.is_absolute(),
                    "file reference path should be workspace-relative (checkpoints must not store absolute paths)"
                );
                assert_eq!(
                    path,
                    &PathBuf::from(".agent/tmp/commit_diff.model_safe.txt"),
                    "expected file reference to point at the model-safe diff artifact"
                );
            }
        }
        other => panic!("unexpected event: {other:?}"),
    }
}

#[test]
fn test_materialize_commit_inputs_within_budget_records_correct_reason() {
    use crate::reducer::event::PromptInputEvent;
    use crate::reducer::state::PromptMaterializationReason;

    // Create small diff within all budgets
    let small_diff = "diff --git a/a b/a\n+small change\n";
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/commit_diff.txt", small_diff)
        .with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    let result = handler
        .materialize_commit_inputs(&ctx, 1)
        .expect("materialize_commit_inputs should succeed");

    match &result.event {
        PipelineEvent::PromptInput(PromptInputEvent::CommitInputsMaterialized { diff, .. }) => {
            assert!(
                matches!(diff.reason, PromptMaterializationReason::WithinBudgets),
                "reason should be WithinBudgets for small diff"
            );
            assert_eq!(
                diff.original_bytes, diff.final_bytes,
                "sizes should be equal when no truncation"
            );
        }
        other => panic!("unexpected event: {other:?}"),
    }
}
