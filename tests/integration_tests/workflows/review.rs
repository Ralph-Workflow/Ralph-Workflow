//! Review workflow integration tests.
//!
//! These tests verify the review workflow functionality.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** via effect capture
//! - Uses `MockAppEffectHandler` AND `MockEffectHandler` for git/filesystem isolation
//! - NO `TempDir`, `std::fs`, or real git operations
//! - Tests are deterministic and verify effects, not real filesystem state

use crate::common::{
    create_test_config_struct, mock_executor_with_success, run_ralph_cli_with_handlers,
};
use crate::test_timeout::with_default_timeout;
use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::PipelineState;
use std::path::PathBuf;

/// Standard PROMPT.md content for review tests.
const STANDARD_PROMPT: &str = r"## Goal

Do something.

## Acceptance

- Tests pass
";

/// Create mock handlers with standard setup for review tests.
fn create_review_test_handlers() -> (MockAppEffectHandler, MockEffectHandler) {
    let app_handler = MockAppEffectHandler::new()
        .with_head_oid("a".repeat(40))
        .with_cwd(PathBuf::from("/mock/repo"))
        .with_file("PROMPT.md", STANDARD_PROMPT)
        .with_diff("diff --git a/test.txt b/test.txt\n+new content")
        .with_staged_changes(true);

    let effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0));

    (app_handler, effect_handler)
}

// ============================================================================
// Review Workflow Tests
//
// Note: Tests that require agent execution (reviewer_reviews > 0) cannot be
// properly tested without the AgentExecutor trait infrastructure. Those tests
// should be unit tests with mocked executors at the code level.
//
// These integration tests focus on behavior that doesn't require agent execution.
// ============================================================================

/// Test that setting `reviewer_reviews` to zero skips the review phase.
///
/// This verifies that when a user runs ralph with `reviewer_reviews=0`,
/// the review phase is skipped entirely and no ISSUES.md file is created.
#[test]
fn test_zero_reviewer_reviews_skips_review() {
    with_default_timeout(|| {
        let (mut app_handler, mut effect_handler) = create_review_test_handlers();
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // ISSUES.md should NOT be created when review is skipped
        assert!(
            app_handler
                .get_file(&PathBuf::from(".agent/ISSUES.md"))
                .is_none(),
            "ISSUES.md should not be created when review phase is skipped"
        );
    });
}

/// Test that the pipeline succeeds without a review phase.
///
/// This verifies that when a user runs ralph with `reviewer_reviews=0`,
/// the pipeline completes successfully.
#[test]
fn test_pipeline_succeeds_without_review_phase() {
    with_default_timeout(|| {
        let (mut app_handler, mut effect_handler) = create_review_test_handlers();
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        let result = run_ralph_cli_with_handlers(
            &[],
            executor,
            config,
            &mut app_handler,
            &mut effect_handler,
        );

        assert!(
            result.is_ok(),
            "Pipeline should succeed without review phase"
        );
    });
}

/// Test that a commit is created when the review phase is skipped.
///
/// This verifies that when a user runs ralph with `reviewer_reviews=0`,
/// a commit effect is still triggered.
#[test]
fn test_commit_created_when_review_skipped() {
    with_default_timeout(|| {
        let (mut app_handler, mut effect_handler) = create_review_test_handlers();
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Verify CreateCommit effect was called at the reducer layer
        let was_commit_created =
            effect_handler.was_effect_executed(|e| matches!(e, Effect::CreateCommit { .. }));

        assert!(
            was_commit_created,
            "CreateCommit effect should be called when review phase is skipped"
        );
    });
}

// ============================================================================
// Review Prompt Construction Tests
//
// These tests verify that the review prompt is correctly constructed with
// the expected content. They test prompt_review_xml_with_context directly.
// ============================================================================

/// Test that review prompt is constructed with all required components.
///
/// This verifies that `prompt_review_xml_with_context` produces a prompt that includes:
/// - "REVIEW MODE" marker
/// - Reference to PROMPT.md.backup file (reviewer reads it directly)
/// - The implementation plan (PLAN content)
/// - Changes made (git diff content)
/// - XML output format instructions with <ralph-issues> tags
#[test]
fn review_prompt_construction_includes_all_required_components() {
    use ralph_workflow::prompts::prompt_review_xml_with_context;
    use ralph_workflow::prompts::template_context::TemplateContext;

    with_default_timeout(|| {
        let template_context = TemplateContext::default();

        // Test inputs - prompt_content is unused, reviewer reads PROMPT.md.backup directly
        let prompt_content = "# Test Requirements\n\nImplement feature X with validation";
        let plan_content = "# Implementation Plan\n\n1. Create module\n2. Add tests";
        let changes_content = "diff --git a/src/lib.rs b/src/lib.rs\n+fn new_function() {}";

        // Build the review prompt
        let workspace = ralph_workflow::workspace::MemoryWorkspace::new_test();
        let review_prompt = prompt_review_xml_with_context(
            &template_context,
            prompt_content,
            plan_content,
            changes_content,
            &workspace,
        );

        // Verify the prompt contains all required components
        assert!(
            review_prompt.contains("REVIEW MODE"),
            "Review prompt must contain 'REVIEW MODE' marker. Got:\n{}",
            &review_prompt[..500.min(review_prompt.len())]
        );

        // prompt_content is no longer embedded - reviewer reads PROMPT.md.backup directly
        assert!(
            review_prompt.contains("PROMPT.md.backup"),
            "Review prompt must reference PROMPT.md.backup for original requirements"
        );

        assert!(
            review_prompt.contains(plan_content),
            "Review prompt must contain the implementation plan"
        );

        assert!(
            review_prompt.contains(changes_content),
            "Review prompt must contain the changes/diff content"
        );

        assert!(
            review_prompt.contains("<ralph-issues>"),
            "Review prompt must contain XML output format instructions with <ralph-issues> tag"
        );

        assert!(
            review_prompt.contains("issues.xml"),
            "Review prompt must reference the issues.xml output file path"
        );
    });
}

/// Test that review prompt is still renderable when PLAN and CHANGES are empty.
///
/// This matches the real-world workflow when running with developer-iters=0 (no plan produced)
/// and/or when diff content is unavailable.
#[test]
fn review_prompt_allows_empty_plan_and_changes() {
    use ralph_workflow::prompts::prompt_review_xml_with_context;
    use ralph_workflow::prompts::template_context::TemplateContext;
    use ralph_workflow::prompts::template_registry::TemplateRegistry;

    with_default_timeout(|| {
        let template_context = TemplateContext::new(TemplateRegistry::new(None));
        let workspace = ralph_workflow::workspace::MemoryWorkspace::new_test();
        let review_prompt =
            prompt_review_xml_with_context(&template_context, "prompt", "", "", &workspace);

        assert!(
            !review_prompt.contains("{{PLAN}}"),
            "Review prompt must not contain unresolved {{PLAN}} placeholder"
        );
        assert!(
            !review_prompt.contains("{{CHANGES}}"),
            "Review prompt must not contain unresolved {{CHANGES}} placeholder"
        );
        assert!(
            review_prompt.contains("(no plan available)"),
            "Review prompt should include a default when plan content is empty"
        );
        assert!(
            review_prompt.contains("(no diff available)"),
            "Review prompt should include a default when diff content is empty"
        );
    });
}

/// Test that review prompt includes severity levels and file references in format instructions.
///
/// This verifies the prompt guides the reviewer to provide actionable output
/// with severity levels and <file:line> references.
#[test]
fn review_prompt_includes_output_format_guidance() {
    use ralph_workflow::prompts::prompt_review_xml_with_context;
    use ralph_workflow::prompts::template_context::TemplateContext;

    with_default_timeout(|| {
        let template_context = TemplateContext::default();
        let workspace = ralph_workflow::workspace::MemoryWorkspace::new_test();

        let review_prompt = prompt_review_xml_with_context(
            &template_context,
            "requirements",
            "plan",
            "changes",
            &workspace,
        );

        // Verify format guidance
        assert!(
            review_prompt.contains("Severity")
                || review_prompt.contains("severity")
                || review_prompt.contains("Critical")
                || review_prompt.contains("High")
                || review_prompt.contains("Medium")
                || review_prompt.contains("Low"),
            "Review prompt must mention severity levels for issues"
        );

        assert!(
            review_prompt.contains("file")
                && (review_prompt.contains("line") || review_prompt.contains(':')),
            "Review prompt must mention file:line references"
        );
    });
}

/// Test that review prompt handles empty inputs gracefully.
///
/// This verifies the prompt construction doesn't crash or produce invalid
/// output when given empty prompt, plan, or changes content.
#[test]
fn review_prompt_handles_empty_inputs() {
    use ralph_workflow::prompts::prompt_review_xml_with_context;
    use ralph_workflow::prompts::template_context::TemplateContext;

    with_default_timeout(|| {
        let template_context = TemplateContext::default();
        let workspace = ralph_workflow::workspace::MemoryWorkspace::new_test();

        // All empty inputs
        let review_prompt =
            prompt_review_xml_with_context(&template_context, "", "", "", &workspace);

        // Should still produce a valid prompt structure
        assert!(
            review_prompt.contains("REVIEW MODE"),
            "Review prompt must contain 'REVIEW MODE' even with empty inputs"
        );
        assert!(
            review_prompt.contains("<ralph-issues>"),
            "Review prompt must contain XML format instructions even with empty inputs"
        );
    });
}
