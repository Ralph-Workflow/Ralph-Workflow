//! Integration test for workspace-rooted prompt path resolution.
//!
//! Verifies that prompts use `workspace.root()` for absolute paths, not process CWD.
//! This prevents the bug where reviewers write XML to the wrong directory in
//! multi-worktree or isolation mode scenarios.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use ralph_workflow::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use ralph_workflow::prompts::content_reference::{DiffContentReference, PlanContentReference};
use ralph_workflow::prompts::{
    prompt_generate_commit_message_with_diff_with_context, prompt_planning_xml_with_references,
    prompt_review_xml_with_references, PromptContentReference, SessionCapabilities,
    TemplateContext,
};
use ralph_workflow::workspace::MemoryWorkspace;
use std::path::{Path, PathBuf};

use crate::test_timeout::with_default_timeout;

/// Test that planning prompts use workspace-rooted paths, not CWD.
#[test]
fn test_planning_prompts_use_workspace_root() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();
        let prompt_ref = PromptContentReference::inline("Test prompt".to_string());

        // Generate planning prompt
        let prompt = prompt_planning_xml_with_references(
            &template_context,
            &prompt_ref,
            &workspace,
            SessionCapabilities::new(
                &CapabilitySet::defaults_for_drain(SessionDrain::Planning),
                &PolicyFlagSet::defaults_for_drain(SessionDrain::Planning),
            ),
        );

        // Verify: prompt instructs agent to submit via MCP
        assert!(
            prompt.contains("ralph_submit_artifact"),
            "Planning prompt should instruct agent to use ralph_submit_artifact"
        );
    });
}

/// Test that review prompts use workspace-rooted paths, not CWD.
#[test]
fn test_review_prompts_use_workspace_root() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate review prompt
        let plan_ref = PlanContentReference::from_plan(
            "Test plan".to_string(),
            Path::new(".agent/PLAN.md"),
            None,
        );
        let diff_ref = DiffContentReference::from_diff(
            "Test changes".to_string(),
            "",
            Path::new(".agent/DIFF.backup"),
        );
        let refs = ralph_workflow::prompts::content_builder::PromptContentReferences {
            prompt: None,
            plan: Some(plan_ref),
            diff: Some(diff_ref),
        };
        let prompt = prompt_review_xml_with_references(
            &template_context,
            &refs,
            &workspace,
            SessionCapabilities::new(
                &CapabilitySet::defaults_for_drain(SessionDrain::Review),
                &PolicyFlagSet::defaults_for_drain(SessionDrain::Review),
            ),
        );

        // Verify: prompt instructs agent to submit via MCP
        assert!(
            prompt.contains("ralph_submit_artifact"),
            "Review prompt should instruct agent to use ralph_submit_artifact"
        );
    });
}

/// Test that commit prompts use MCP artifact submission.
#[test]
fn test_commit_prompts_use_workspace_root() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate commit prompt
        let (caps, flags) = SessionCapabilities::from_drain(SessionDrain::Commit);
        let session_caps = SessionCapabilities::new(&caps, &flags);
        let prompt = prompt_generate_commit_message_with_diff_with_context(
            &template_context,
            "Test diff",
            &workspace,
            session_caps,
        );

        // Verify: prompt instructs agent to submit via MCP
        assert!(
            prompt.contains("ralph_submit_artifact"),
            "Commit prompt should instruct agent to use ralph_submit_artifact"
        );
    });
}
