// ============================================================================
// Dumb-Agent-Proof Contract Tests
// ============================================================================

/// Regression test: analysis status contract MUST include `completed`, `partial`, `failed`.
#[test]
fn test_analysis_status_contract_always_has_completed_partial_failed() {
    use crate::agents::session::SessionDrain;
    use crate::prompts::analysis::generate_analysis_prompt;
    use crate::prompts::analysis::generate_fix_analysis_prompt;
    use crate::prompts::template_variables::SessionCapabilities;
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test();
    let (caps, flags) = SessionCapabilities::from_drain(SessionDrain::Development);
    let session_caps = SessionCapabilities::new(&caps, &flags);

    // Test analysis prompt
    let analysis_prompt = generate_analysis_prompt("plan", "diff", false, &workspace, session_caps);
    assert!(
        analysis_prompt.contains("completed"),
        "Analysis prompt must include 'completed' status"
    );
    assert!(
        analysis_prompt.contains("partial"),
        "Analysis prompt must include 'partial' status"
    );
    assert!(
        analysis_prompt.contains("failed"),
        "Analysis prompt must include 'failed' status"
    );

    // Test fix analysis prompt
    let fix_analysis_prompt = generate_fix_analysis_prompt(
        "issues",
        "diff",
        "fix_result",
        false,
        &workspace,
        session_caps,
    );
    assert!(
        fix_analysis_prompt.contains("completed"),
        "Fix analysis prompt must include 'completed' status"
    );
    assert!(
        fix_analysis_prompt.contains("partial"),
        "Fix analysis prompt must include 'partial' status"
    );
    assert!(
        fix_analysis_prompt.contains("failed"),
        "Fix analysis prompt must include 'failed' status"
    );
}

/// Regression test: planning XSD retry MUST enforce submission-fix-only behavior.
#[test]
fn test_planning_xsd_retry_enforces_submission_fix_only() {
    use crate::agents::session::SessionDrain;
    use crate::prompts::developer::prompt_planning_xsd_retry_with_context;
    use crate::prompts::template_variables::SessionCapabilities;
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();
    let (caps, flags) = SessionCapabilities::from_drain(SessionDrain::Development);
    let session_caps = SessionCapabilities::new(&caps, &flags);

    let result = prompt_planning_xsd_retry_with_context(
        &context,
        "original prompt",
        "XSD error: missing element",
        "<invalid xml",
        &workspace,
        session_caps,
    );

    // Must say FIX XML ONLY or similar scope lock
    assert!(
        result.contains("FIX XML ONLY")
            || result.contains("fix") && (result.contains("XML") || result.contains("xml")),
        "Planning XSD retry must enforce XML fix scope. Got:\n{result}"
    );

    // Must label prior artifacts as REFERENCE ONLY
    assert!(
        result.contains("REFERENCE ONLY") || result.contains("Reference"),
        "Planning XSD retry must label prior artifacts as REFERENCE ONLY. Got:\n{result}"
    );

    // Must forbid new planning/implementation work
    assert!(
        result.to_uppercase().contains("MUST NOT")
            || result.to_uppercase().contains("DO NOT")
            || result.contains("no new planning")
            || result.contains("no new implementation")
            || result.contains("no planning")
            || result.contains("no implementation"),
        "Planning XSD retry must forbid new planning/implementation work. Got:\n{result}"
    );

    // Must mention malformed XML as primary target
    assert!(
        result.contains("malformed") || result.contains("MALFORMED") || result.contains("XML"),
        "Planning XSD retry should mention malformed XML. Got:\n{result}"
    );
}

/// Regression test: developer iteration XSD retry MUST enforce submission-fix-only behavior.
#[test]
fn test_developer_iteration_xsd_retry_enforces_submission_fix_only() {
    use crate::agents::session::SessionDrain;
    use crate::prompts::developer::prompt_developer_iteration_xsd_retry_with_context;
    use crate::prompts::template_variables::SessionCapabilities;
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();
    let (caps, flags) = SessionCapabilities::from_drain(SessionDrain::Development);
    let session_caps = SessionCapabilities::new(&caps, &flags);

    let result = prompt_developer_iteration_xsd_retry_with_context(
        &context,
        "XSD error: invalid element",
        "<invalid xml",
        &workspace,
        false,
        session_caps,
    );

    // Must say FIX XML ONLY or similar scope lock
    assert!(
        result.contains("FIX XML ONLY")
            || result.contains("fix") && (result.contains("XML") || result.contains("xml")),
        "Developer XSD retry must enforce XML fix scope. Got:\n{result}"
    );

    // Must label prior artifacts as REFERENCE ONLY
    assert!(
        result.contains("REFERENCE ONLY") || result.contains("Reference"),
        "Developer XSD retry must label prior artifacts as REFERENCE ONLY. Got:\n{result}"
    );

    // Must forbid new coding/implementation work
    assert!(
        result.to_uppercase().contains("MUST NOT")
            || result.to_uppercase().contains("DO NOT")
            || result.contains("no new code")
            || result.contains("no new implementation")
            || result.contains("no coding")
            || result.contains("no implementation"),
        "Developer XSD retry must forbid new coding/implementation work. Got:\n{result}"
    );
}

/// Regression test: planning prompt MUST have explicit required sections and validation checklist.
#[test]
fn test_planning_prompt_has_validation_checklist() {
    use crate::workspace::MemoryWorkspace;

    // Use in-memory workspace
    let workspace = MemoryWorkspace::new_test();
    let partials = crate::prompts::partials::get_shared_partials();
    let template_content = include_str!("../templates/planning_xml.txt");
    let template = crate::prompts::Template::new(template_content);
    let base_variables = std::collections::HashMap::from([
        ("PROMPT", "test prompt".to_string()),
        (
            "PLAN_XML_PATH",
            workspace.absolute_str(".agent/tmp/plan.xml"),
        ),
        (
            "PLAN_XSD_PATH",
            workspace.absolute_str(".agent/tmp/plan.xsd"),
        ),
    ]);
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Planning);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Planning);
    let variables: std::collections::HashMap<String, String> = base_variables
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(
            crate::prompts::template_variables::capability_template_variables(
                &capabilities,
                &policy_flags,
            ),
        )
        .collect();
    let variables_ref: std::collections::HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    let result = template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_default();

    // Must have explicit required sections
    assert!(
        result.contains("ralph-summary")
            || result.contains("summary")
            || result.contains("Required"),
        "Planning prompt must mention required sections. Got:\n{result}"
    );

    // Must have some form of validation/checklist before submission
    assert!(
        result.contains("checklist")
            || result.contains("Checklist")
            || result.contains("verify")
            || result.contains("before output")
            || result.contains("Before writing")
            || result.contains("validation"),
        "Planning prompt must have validation checklist. Got:\n{result}"
    );

    // Must have minimum counts or explicit requirements
    assert!(
        result.contains("minimum")
            || result.contains("minimum")
            || result.contains("at least")
            || result.contains("required"),
        "Planning prompt must specify minimum counts or requirements. Got:\n{result}"
    );
}

/// Regression test: review prompt MUST prioritize and make issues actionable.
#[test]
fn test_review_prompt_makes_issues_actionable() {
    use crate::agents::session::SessionDrain;
    use crate::prompts::template_variables::SessionCapabilities;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let (caps, flags) = SessionCapabilities::from_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&caps, &flags);
    let result = prompt_review_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        "test changes",
        &workspace,
        session_caps,
    );

    // Must prioritize issues
    assert!(
        result.contains("priority")
            || result.contains("Priority")
            || result.contains("priority order")
            || result.contains("high-signal"),
        "Review prompt must prioritize issues. Got:\n{result}"
    );

    // Must make issues actionable (what is wrong, where, why, how to fix)
    assert!(
        result.contains("what") || result.contains("What"),
        "Review prompt must explain what is wrong. Got:\n{result}"
    );
    assert!(
        result.contains("where") || result.contains("Where"),
        "Review prompt must explain where the issue is. Got:\n{result}"
    );
    assert!(
        result.contains("fix") || result.contains("Fix"),
        "Review prompt must explain how to fix. Got:\n{result}"
    );
}

use super::*;
use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::prompts::template_variables::SessionCapabilities;
use crate::workspace::MemoryWorkspace;
use std::path::PathBuf;

#[test]
fn test_prompt_review_xml_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let result = prompt_review_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        "test changes",
        &workspace,
        session_caps,
    );
    // prompt_content is no longer embedded - reviewer reads PROMPT.md.backup directly
    assert!(!result.contains("test prompt"));
    assert!(result.contains("PROMPT.md.backup"));
    assert!(result.contains("test plan"));
    assert!(result.contains("test changes"));
    assert!(result.contains("REVIEW MODE"));
    assert!(
        result.contains("ralph_submit_artifact"),
        "review_xml should require MCP artifact submission"
    );
    assert!(
        result.contains("Focus on high-signal, user-impacting issues"),
        "review_xml should prioritize high-signal, user-impacting findings"
    );
    assert!(
        result.contains("If no important issues are found, explicitly state why"),
        "review_xml should require an explicit no-issues rationale"
    );
    assert!(
        result.contains("Use parallel review agents only for independent review tracks"),
        "review_xml should provide conditional guidance for parallel review agents"
    );

    // Submission is mandatory via MCP tool
    assert!(
        result.contains("MANDATORY"),
        "review_xml should mark submission mandatory"
    );
    assert!(
        result.contains("Not submitting") && result.contains("FAILURE"),
        "review_xml should say not submitting is a failure"
    );
    assert!(
        result.contains("READ-ONLY"),
        "review_xml should be read-only"
    );

    assert!(
        !result.contains("DO NOT print")
            && !result.contains("Do NOT print")
            && !result.contains("ONLY acceptable output")
            && !result.contains("The ONLY acceptable output"),
        "review_xml should not include stdout suppression wording"
    );

    // Shared partials should be expanded (no raw partial directives left in output)
    assert!(
        result.contains("*** UNATTENDED MODE - NO USER INTERACTION ***"),
        "review_xml should render shared/_unattended_mode partial"
    );
    assert!(
        !result.contains("{{>"),
        "review_xml should not contain raw partial directives"
    );
}

#[test]
fn test_prompt_review_xml_with_context_allows_empty_plan_and_changes() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let result =
        prompt_review_xml_with_context(&context, "prompt", "", "", &workspace, session_caps);

    assert!(
        !result.contains("{{PLAN}}"),
        "review prompt must not contain unresolved {{PLAN}} placeholder"
    );
    assert!(
        !result.contains("{{CHANGES}}"),
        "review prompt must not contain unresolved {{CHANGES}} placeholder"
    );
    assert!(
        result.contains("(no plan available)"),
        "review prompt should include a default when plan content is empty"
    );
    assert!(
        result.contains("(no diff available)"),
        "review prompt should include a default when changes/diff content is empty"
    );
}

#[test]
fn test_prompt_review_xml_with_context_uses_inline_plan_and_changes_when_present() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let result = prompt_review_xml_with_context(
        &context,
        "prompt",
        "plan here",
        "diff here",
        &workspace,
        session_caps,
    );

    assert!(result.contains("plan here"));
    assert!(result.contains("diff here"));

    assert!(
        !result.contains("(no plan available)"),
        "default plan text should not appear when plan is present"
    );
    assert!(
        !result.contains("(no diff available)"),
        "default diff text should not appear when diff is present"
    );
}

#[test]
fn test_prompt_review_xsd_retry_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let result = prompt_review_xsd_retry_with_context(
        &context,
        "XSD error",
        "last output",
        &workspace,
        session_caps,
    );
    assert!(result.contains("XSD error"));
    assert!(result.contains(".agent/tmp/issues.xml"));
    assert!(result.contains(".agent/tmp/issues.xsd"));

    // FIX XML ONLY retry: must enforce submission-fix-only behavior
    assert!(
        result.contains("FIX XML ONLY"),
        "review_xsd_retry should say FIX XML ONLY"
    );

    // Must label prior artifacts as REFERENCE ONLY
    assert!(
        result.contains("REFERENCE ONLY"),
        "review_xsd_retry should have REFERENCE ONLY section"
    );

    // Must forbid new review/implementation work
    assert!(
        result.contains("DO NOT DO"),
        "review_xsd_retry should have DO NOT DO section"
    );

    // Must emphasize malformed XML as primary target
    assert!(
        result.contains("PRIMARY OBJECTIVE"),
        "review_xsd_retry should emphasize primary objective"
    );

    // Must have anti-actions
    assert!(
        result.contains("Do NOT review"),
        "review_xsd_retry should forbid new review work"
    );

    assert!(
        !result.contains("DO NOT print")
            && !result.contains("Do NOT print")
            && !result.contains("ONLY acceptable output")
            && !result.contains("The ONLY acceptable output"),
        "review_xsd_retry should not include stdout suppression wording"
    );

    // Shared partials should be expanded
    assert!(
        result.contains("*** UNATTENDED MODE - NO USER INTERACTION ***"),
        "review_xsd_retry should render shared/_unattended_mode partial"
    );
    assert!(
        !result.contains("{{>"),
        "review_xsd_retry should not contain raw partial directives"
    );

    // Verify files were written to workspace
    assert!(workspace.was_written(".agent/tmp/issues.xsd"));
    assert!(workspace.was_written(".agent/tmp/last_output.xml"));
}

#[test]
fn test_prompt_fix_xml_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let result = prompt_fix_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        "test issues",
        &[],
        &workspace,
        session_caps,
    );
    assert!(result.contains("test issues"));
    assert!(result.contains("FIX MODE"));
    assert!(
        result.contains("ralph_submit_artifact"),
        "fix_mode_xml should reference MCP submission tool"
    );
    assert!(
        result.contains("Run relevant unit/integration tests"),
        "fix_mode_xml should require running relevant tests beyond listed issues"
    );
    assert!(
        result.contains("If tests or investigation reveal additional real bugs"),
        "fix_mode_xml should require fixing additional real bugs discovered incidentally"
    );
    assert!(
        result.contains("DO NOT ONLY FIX the listed issues"),
        "fix_mode_xml should explicitly forbid narrow fixing when other bugs are discovered"
    );
    assert!(
        result.contains("Ensure your final changes are validated with relevant checks"),
        "fix_mode_xml should require final validation/checklist discipline"
    );
    assert!(
        result.contains("AGENTS.md") && result.contains("CLAUDE.md"),
        "fix_mode_xml should reference project-specific agent instruction files for required checks"
    );
    assert!(
        !result.contains("ISSUES TO FIX"),
        "fix_mode_xml should avoid narrow-scope section labels"
    );
    assert!(
        !result.contains("Fix the issues listed above. For each issue:"),
        "fix_mode_xml should not frame work as only the listed issues"
    );
    assert!(
        !result.contains("you may explore LIMITEDLY"),
        "fix_mode_xml should not restrict investigation when additional concrete bugs are found"
    );
    assert!(
        result.contains("Address the listed review findings and any additional concrete defects"),
        "fix_mode_xml should explicitly broaden scope to concrete discovered defects"
    );

    // Shared partials should be expanded
    assert!(
        result.contains("*** UNATTENDED MODE - NO USER INTERACTION ***"),
        "fix_mode_xml should render shared/_unattended_mode partial"
    );
    assert!(
        !result.contains("{{>"),
        "fix_mode_xml should not contain raw partial directives"
    );
}

#[test]
fn test_prompt_fix_xsd_retry_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let result = prompt_fix_xsd_retry_with_context(
        &context,
        "test issues",
        "XSD error",
        "last output",
        &workspace,
        session_caps,
    );
    assert!(result.contains("XSD error"));
    assert!(result.contains(".agent/tmp/fix_result.xml"));
    assert!(result.contains(".agent/tmp/fix_result.xsd"));
    // Verify files were written to workspace
    assert!(workspace.was_written(".agent/tmp/fix_result.xsd"));
    assert!(workspace.was_written(".agent/tmp/last_output.xml"));
}

// =========================================================================
// Tests for _with_references variants
// =========================================================================

#[test]
fn test_prompt_review_xml_with_references_small_content() {
    use crate::prompts::content_builder::PromptContentBuilder;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);

    let refs = PromptContentBuilder::new(&workspace)
        .with_plan("Small plan content".to_string())
        .with_diff("Small diff content".to_string(), "abc123")
        .build();

    let result = prompt_review_xml_with_references(&context, &refs, &workspace, session_caps);

    // Should embed content inline
    assert!(result.contains("Small plan content"));
    assert!(result.contains("Small diff content"));
    assert!(result.contains("REVIEW MODE"));
}

#[test]
fn test_prompt_review_xml_with_references_large_plan() {
    use crate::prompts::content_builder::PromptContentBuilder;
    use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let large_plan = "p".repeat(MAX_INLINE_CONTENT_SIZE + 1);

    let refs = PromptContentBuilder::new(&workspace)
        .with_plan(large_plan)
        .with_diff("Small diff".to_string(), "abc123")
        .build();

    let result = prompt_review_xml_with_references(&context, &refs, &workspace, session_caps);

    // Should reference PLAN.md file, not embed content
    assert!(result.contains(".agent/PLAN.md"));
    assert!(result.contains("plan.xml"));
    assert!(result.contains("Small diff"));
}

#[test]
fn test_prompt_review_xml_with_references_large_diff() {
    use crate::prompts::content_builder::PromptContentBuilder;
    use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let large_diff = "d".repeat(MAX_INLINE_CONTENT_SIZE + 1);

    let refs = PromptContentBuilder::new(&workspace)
        .with_plan("Small plan".to_string())
        .with_diff(large_diff, "abc123def")
        .build();

    let result = prompt_review_xml_with_references(&context, &refs, &workspace, session_caps);

    // Should instruct to use git diff fallback commands, not embed content
    assert!(result.contains("git diff abc123def"));
    assert!(result.contains("git diff --cached abc123def"));
    assert!(result.contains("Small plan"));
}

#[test]
fn test_prompt_review_xml_with_references_both_large() {
    use crate::prompts::content_builder::PromptContentBuilder;
    use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let large_plan = "p".repeat(MAX_INLINE_CONTENT_SIZE + 1);
    let large_diff = "d".repeat(MAX_INLINE_CONTENT_SIZE + 1);

    let refs = PromptContentBuilder::new(&workspace)
        .with_plan(large_plan)
        .with_diff(large_diff, "start123")
        .build();

    let result = prompt_review_xml_with_references(&context, &refs, &workspace, session_caps);

    // Both should be referenced by file/git command
    assert!(result.contains(".agent/PLAN.md"));
    assert!(result.contains("git diff start123"));
    assert!(result.contains("git diff --cached start123"));
    // Should not contain the large content
    let pppp = "p".repeat(100);
    assert!(!result.contains(&pppp));
}
