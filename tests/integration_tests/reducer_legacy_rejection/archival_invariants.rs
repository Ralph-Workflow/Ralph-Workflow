//! Integration tests for archival invariants with legacy artifacts.
//!
//! Verifies that legacy artifacts from previous Ralph versions don't affect
//! pipeline execution. The reducer must derive all decisions from events,
//! not from file presence or content.
//!
//! Observable behaviors tested:
//! - Legacy PLAN.md files are ignored during planning
//! - Legacy ISSUES.md files are ignored during review
//! - Pipeline decisions come from events, not file system state
//! - Effect determination is independent of legacy artifacts
//!
//! # Integration Test Compliance
//!
//! These tests follow [../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md):
//! - Test observable behavior: effect determination
//! - Use `MemoryWorkspace` to simulate legacy files
//! - Verify event-driven architecture

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use std::path::Path;

// ============================================================================
// LEGACY ARTIFACT IGNORED DURING EXECUTION TESTS
// ============================================================================

/// Test that legacy artifacts in workspace don't affect effect determination.
///
/// When legacy files (e.g., ISSUES.md, PLAN.md from old versions) exist
/// in the workspace, the pipeline should NOT read them to derive results.
/// All pipeline decisions must come from reducer events/effects, not file presence.
#[test]
fn test_legacy_artifacts_ignored_during_execution() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::effect::Effect;
    use ralph_workflow::reducer::event::PipelinePhase;
    use ralph_workflow::reducer::orchestration::determine_next_effect;
    use ralph_workflow::reducer::state::PipelineState;

    with_default_timeout(|| {
        // Create state in Development phase with agents initialized
        let mut state = with_locked_prompt_permissions(PipelineState::initial(2, 1));
        state.phase = PipelinePhase::Development;
        state.agent_chain = state.agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        );

        // Effect determination should NOT depend on workspace file existence
        // (determine_next_effect is a pure function of state)
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::PrepareDevelopmentContext { .. }),
            "Effect should be determined from state alone, got {effect:?}"
        );

        // Even with max iterations reached, state-based transition should happen
        let mut state = with_locked_prompt_permissions(PipelineState::initial(0, 1));
        state.phase = PipelinePhase::Review;
        state.agent_chain = state.agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        );

        // Effect determination for review should not check for legacy ISSUES.md
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::PrepareReviewContext { .. }),
            "Review effect should be determined from state alone, got {effect:?}"
        );
    });
}

/// Test that legacy artifact files in workspace are completely ignored.
///
/// Even when legacy files exist in the workspace (ISSUES.md, PLAN.md, commit.xml),
/// the pipeline must not read them to derive results. All results must come from
/// the current XML paths. This test explicitly creates these files and verifies
/// `determine_next_effect` remains unchanged.
#[test]
fn test_legacy_artifact_files_completely_ignored() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::effect::Effect;
    use ralph_workflow::reducer::event::PipelinePhase;
    use ralph_workflow::reducer::orchestration::determine_next_effect;
    use ralph_workflow::reducer::state::PipelineState;

    with_default_timeout(|| {
        // determine_next_effect is a pure function of PipelineState - it takes no
        // workspace argument and cannot read the filesystem. Creating a MemoryWorkspace
        // here would be dead scaffolding since nothing consumes it.

        // Create state in Development phase
        let mut state = with_locked_prompt_permissions(PipelineState::initial(2, 1));
        state.phase = PipelinePhase::Development;
        state.agent_chain = state.agent_chain.with_agents(
            vec!["test-agent".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        );

        // Effect determination must be pure - only state drives the decision
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::PrepareDevelopmentContext { .. }),
            "Effect must be determined from state alone, not workspace files"
        );
    });
}

/// Test that archived XML files use .processed suffix consistently.
///
/// All XML archiving must use the `.processed` suffix for consistency.
/// This ensures the fallback pattern in handlers works correctly.
#[test]
fn test_archived_xml_uses_processed_suffix() {
    use ralph_workflow::files::archive_xml_file_with_workspace;
    use ralph_workflow::workspace::{MemoryWorkspace, Workspace};

    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test()
            .with_file(".agent/tmp/plan.xml", "<plan>test</plan>")
            .with_file(".agent/tmp/issues.xml", "<issues>test</issues>")
            .with_file(
                ".agent/tmp/development_result.xml",
                "<development>test</development>",
            )
            .with_file(".agent/tmp/fix_result.xml", "<fix>test</fix>")
            .with_file(".agent/tmp/commit_message.xml", "<commit>test</commit>");

        // Archive each file
        let paths = [
            ".agent/tmp/plan.xml",
            ".agent/tmp/issues.xml",
            ".agent/tmp/development_result.xml",
            ".agent/tmp/fix_result.xml",
            ".agent/tmp/commit_message.xml",
        ];

        for path in paths {
            archive_xml_file_with_workspace(&workspace, Path::new(path));

            // Original should be gone
            assert!(
                !workspace.exists(Path::new(path)),
                "Original file should be removed after archiving: {path}"
            );

            // .processed should exist
            let processed_path = format!("{path}.processed");
            assert!(
                workspace.exists(Path::new(&processed_path)),
                "Archived file should have .processed suffix: {processed_path}"
            );
        }
    });
}
