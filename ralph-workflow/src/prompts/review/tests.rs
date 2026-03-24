use super::*;
use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::workspace::MemoryWorkspace;
use std::path::PathBuf;

#[test]
fn test_prompt_review_xml_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let result = prompt_review_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        "test changes",
        &workspace,
        &capabilities,
        &policy_flags,
    );
    // prompt_content is no longer embedded - reviewer reads PROMPT.md.backup directly
    assert!(!result.contains("test prompt"));
    assert!(result.contains("PROMPT.md.backup"));
    assert!(result.contains("test plan"));
    assert!(result.contains("test changes"));
    assert!(result.contains("REVIEW MODE"));
    assert!(result.contains("<ralph-issues>"));
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

    // Read-only modes: reviewer must still write exactly one XML file.
    assert!(
        result.contains("explicitly authorized") && result.contains("EXACTLY ONE file"),
        "review_xml should explicitly authorize writing exactly one XML file"
    );
    assert!(
        result.contains("MANDATORY"),
        "review_xml should mark XML file write mandatory"
    );
    assert!(
        result.contains("Not writing") && result.contains("FAILURE"),
        "review_xml should say not writing XML is a failure"
    );
    assert!(
        result.contains("does not conform") && result.contains("XSD") && result.contains("FAILURE"),
        "review_xml should say non-XSD XML is a failure"
    );
    assert!(
        result.contains("READ-ONLY")
            && (result.contains("EXCEPT FOR writing")
                || result.contains("except for writing")
                || result.contains("Except for writing"))
            && result.contains("issues.xml"),
        "review_xml should be read-only except for writing issues.xml"
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
    let result = prompt_review_xml_with_context(&context, "prompt", "", "", &workspace, &capabilities, &policy_flags);

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
    let result =
        prompt_review_xml_with_context(&context, "prompt", "plan here", "diff here", &workspace, &capabilities, &policy_flags);

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
    let result = prompt_review_xsd_retry_with_context(
        &context,
        "test prompt",
        "test plan",
        "test changes",
        "XSD error",
        "last output",
        &workspace,
    );
    assert!(result.contains("XSD error"));
    assert!(result.contains(".agent/tmp/issues.xml"));
    assert!(result.contains(".agent/tmp/issues.xsd"));

    // Read-only modes: reviewer must still write exactly one XML file.
    assert!(
        result.contains("explicitly authorized") && result.contains("EXACTLY ONE file"),
        "review_xsd_retry should explicitly authorize writing exactly one XML file"
    );
    assert!(
        result.contains("MANDATORY"),
        "review_xsd_retry should mark XML file write mandatory"
    );
    assert!(
        result.contains("Not writing") && result.contains("FAILURE"),
        "review_xsd_retry should say not writing XML is a failure"
    );
    assert!(
        result.contains("does not conform") && result.contains("XSD") && result.contains("FAILURE"),
        "review_xsd_retry should say non-XSD XML is a failure"
    );
    assert!(
        result.contains("READ-ONLY")
            && (result.contains("EXCEPT FOR writing")
                || result.contains("except for writing")
                || result.contains("Except for writing"))
            && result.contains("issues.xml"),
        "review_xsd_retry should be read-only except for writing issues.xml"
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
    let result = prompt_fix_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        "test issues",
        &[],
        &workspace,
    );
    assert!(result.contains("test issues"));
    assert!(result.contains("FIX MODE"));
    assert!(result.contains("<ralph-fix-result>"));
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
    let result = prompt_fix_xsd_retry_with_context(
        &context,
        "test issues",
        "XSD error",
        "last output",
        &workspace,
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

    let refs = PromptContentBuilder::new(&workspace)
        .with_plan("Small plan content".to_string())
        .with_diff("Small diff content".to_string(), "abc123")
        .build();

    let result = prompt_review_xml_with_references(&context, &refs, &workspace, &capabilities, &policy_flags);

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
    let large_plan = "p".repeat(MAX_INLINE_CONTENT_SIZE + 1);

    let refs = PromptContentBuilder::new(&workspace)
        .with_plan(large_plan)
        .with_diff("Small diff".to_string(), "abc123")
        .build();

    let result = prompt_review_xml_with_references(&context, &refs, &workspace, &capabilities, &policy_flags);

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
    let large_diff = "d".repeat(MAX_INLINE_CONTENT_SIZE + 1);

    let refs = PromptContentBuilder::new(&workspace)
        .with_plan("Small plan".to_string())
        .with_diff(large_diff, "abc123def")
        .build();

    let result = prompt_review_xml_with_references(&context, &refs, &workspace, &capabilities, &policy_flags);

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
    let large_plan = "p".repeat(MAX_INLINE_CONTENT_SIZE + 1);
    let large_diff = "d".repeat(MAX_INLINE_CONTENT_SIZE + 1);

    let refs = PromptContentBuilder::new(&workspace)
        .with_plan(large_plan)
        .with_diff(large_diff, "start123")
        .build();

    let result = prompt_review_xml_with_references(&context, &refs, &workspace, &capabilities, &policy_flags);

    // Both should be referenced by file/git command
    assert!(result.contains(".agent/PLAN.md"));
    assert!(result.contains("git diff start123"));
    assert!(result.contains("git diff --cached start123"));
    // Should not contain the large content
    let pppp = "p".repeat(100);
    assert!(!result.contains(&pppp));
}
