//! Integration test for XSD retry missing file detection.
//!
//! Verifies that XSD retry prompts detect missing schema files and `last_output.xml`
//! and emit actionable diagnostics including workspace root path.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use ralph_workflow::prompts::{
    prompt_commit_xsd_retry_with_context, prompt_developer_iteration_xsd_retry_with_context_files,
    prompt_fix_xsd_retry_with_context_files, prompt_planning_xsd_retry_with_context_files,
    prompt_review_xsd_retry_with_context_files, TemplateContext,
};
use ralph_workflow::workspace::MemoryWorkspace;
use std::path::PathBuf;

use crate::test_timeout::with_default_timeout;

/// Test that planning XSD retry detects missing schema file.
#[test]
fn test_planning_xsd_retry_detects_missing_schema() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate XSD retry prompt with missing schema
        let prompt = prompt_planning_xsd_retry_with_context_files(
            &template_context,
            "Test error",
            &workspace,
        );

        // Verify: prompt indicates missing file AND includes workspace root
        assert!(
            prompt.contains("WARNING: Required XSD retry files are missing")
                && prompt.contains("workspace.root()"),
            "Should detect missing schema AND include workspace root diagnostics. Got prompt: \n{prompt}"
        );
    });
}

/// Test that review XSD retry detects missing files.
#[test]
fn test_review_xsd_retry_detects_missing_files() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate XSD retry prompt with missing schema
        let prompt =
            prompt_review_xsd_retry_with_context_files(&template_context, "Test error", &workspace);

        // Verify: prompt indicates missing file AND includes workspace root
        assert!(
            prompt.contains("WARNING: Required XSD retry files are missing")
                && prompt.contains("workspace.root()"),
            "Should detect missing schema AND include workspace root diagnostics. Got prompt: \n{prompt}"
        );
    });
}

/// Test that development XSD retry detects missing files.
#[test]
fn test_development_xsd_retry_detects_missing_files() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate XSD retry prompt with missing schema
        let prompt = prompt_developer_iteration_xsd_retry_with_context_files(
            &template_context,
            "Test error",
            &workspace,
            true,
        );

        // Verify: prompt indicates missing file AND includes workspace root
        assert!(
            prompt.contains("WARNING: Required XSD retry files are missing")
                && prompt.contains("workspace.root()")
                && prompt.contains("development_continuation_result.xsd"),
            "Should detect missing schema AND include workspace root diagnostics. Got prompt: \n{prompt}"
        );
    });
}

/// Test that fix XSD retry detects missing files.
#[test]
fn test_fix_xsd_retry_detects_missing_files() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate XSD retry prompt with missing schema
        let prompt =
            prompt_fix_xsd_retry_with_context_files(&template_context, "Test error", &workspace);

        // Verify: prompt indicates missing file AND includes workspace root
        assert!(
            prompt.contains("WARNING: Required XSD retry files are missing")
                && prompt.contains("workspace.root()"),
            "Should detect missing schema AND include workspace root diagnostics. Got prompt: \n{prompt}"
        );
    });
}

/// Test that commit XSD retry detects missing files.
#[test]
fn test_commit_xsd_retry_detects_missing_files() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate XSD retry prompt with missing schema
        let prompt =
            prompt_commit_xsd_retry_with_context(&template_context, "Test error", &workspace);

        // Verify: prompt indicates missing file AND includes workspace root
        assert!(
            prompt.contains("WARNING: Required XSD retry files are missing")
                && prompt.contains("workspace.root()"),
            "Should detect missing schema AND include workspace root diagnostics. Got prompt: \n{prompt}"
        );
    });
}
