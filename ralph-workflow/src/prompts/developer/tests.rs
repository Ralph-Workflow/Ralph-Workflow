// Tests for developer prompts.

use super::*;

#[test]
fn test_prompt_developer_iteration() {
    let result = prompt_developer_iteration(2, 5, ContextLevel::Normal, "test prompt", "test plan");
    // Agent should receive PROMPT and PLAN content directly
    assert!(result.contains("test prompt"));
    assert!(result.contains("test plan"));
    assert!(result.contains("IMPLEMENTATION MODE"));
    // Agent should NOT be told to read PROMPT.md (orchestrator handles it)
    assert!(!result.contains("PROMPT.md"));
    assert!(!result.contains("PLAN.md"));
    assert!(
        result.contains("Do NOT create STATUS.md")
            && result.contains("CURRENT_STATUS.md")
            && result.contains("CURRENT_IMPLEMENTATION.md"),
        "Prompt should explicitly ban status/handoff files"
    );
    assert!(
        result.contains("Do NOT write summaries")
            || result.contains("Do NOT attempt to communicate"),
        "Prompt should clearly ban result-summary communication"
    );
    assert!(
        !result.contains("may or may not be shown to the user"),
        "Prompt should avoid speculative next-step context"
    );
}

#[test]
fn test_developer_iteration_minimal_context() {
    let result =
        prompt_developer_iteration(1, 5, ContextLevel::Minimal, "test prompt", "test plan");
    // Minimal context should include essential files (not STATUS.md in isolation mode)
    // Agent should receive PROMPT and PLAN content directly
    assert!(result.contains("test prompt"));
    assert!(result.contains("test plan"));
    // Agent should NOT be told to read PROMPT.md (orchestrator handles it)
    assert!(!result.contains("PROMPT.md"));
    assert!(!result.contains("PLAN.md"));
    assert!(
        result.contains("Do NOT create STATUS.md")
            && result.contains("CURRENT_STATUS.md")
            && result.contains("CURRENT_IMPLEMENTATION.md"),
        "Prompt should explicitly ban status/handoff files"
    );
}

#[test]
fn test_prompt_plan() {
    let result = prompt_plan(None);
    // Prompt should NOT explicitly mention PROMPT.md file name
    // Agents receive content directly without knowing the source file
    assert!(!result.contains("PROMPT.md"));
    assert!(!result.contains("NEVER read, write, or delete this file"));
    // Plan is now returned as XML output format
    assert!(result.contains("PLANNING MODE"));
    assert!(result.contains("<ralph-implementation-steps>"));
    assert!(result.contains("<ralph-critical-files>"));
    assert!(result.contains("<ralph-verification-strategy>"));

    // Ensure strict read-only constraints are present (Claude Code alignment)
    assert!(result.contains("READ-ONLY"));
    assert!(result.contains("STRICTLY PROHIBITED"));

    // Ensure 5-phase workflow structure (Claude Code alignment)
    assert!(result.contains("PHASE 1: UNDERSTANDING"));
    assert!(result.contains("PHASE 2: EXPLORATION"));
    assert!(result.contains("PHASE 3: DESIGN"));
    assert!(result.contains("PHASE 4: REVIEW"));
    assert!(result.contains("PHASE 5: WRITE STRUCTURED PLAN"));

    // Ensure XML output format is specified
    assert!(result.contains("<ralph-plan>"));
    assert!(result.contains("<ralph-summary>"));
}

#[test]
fn test_prompt_plan_with_content() {
    let prompt_md = "# Test Prompt\n\nThis is the content.";
    let result = prompt_plan(Some(prompt_md));
    // Should include the content WITHOUT naming PROMPT.md
    assert!(result.contains("USER REQUIREMENTS:"));
    assert!(result.contains("This is the content."));
    // Should NOT mention PROMPT.md file name
    assert!(!result.contains("PROMPT.md"));
    // Should still have the planning structure
    assert!(result.contains("PLANNING MODE"));
    assert!(result.contains("PHASE 1: UNDERSTANDING"));
    // Should have XML output format
    assert!(result.contains("<ralph-plan>"));
}

#[test]
fn all_developer_prompts_isolate_agents_from_git() {
    // Verify developer prompts prohibit mutating git commands.
    // Read-only lookup examples (git status/git diff) are allowed when explicitly scoped.
    let prompts = vec![
        prompt_developer_iteration(1, 3, ContextLevel::Minimal, "", ""),
        prompt_developer_iteration(2, 3, ContextLevel::Normal, "", ""),
        prompt_plan(None),
    ];

    for prompt in prompts {
        assert!(
            !prompt.contains("git commit"),
            "Developer prompt should not tell agent to run git commit"
        );
        assert!(
            !prompt.contains("git add"),
            "Developer prompt should not tell agent to run git add"
        );

        if prompt.contains("git status") || prompt.contains("git diff") {
            assert!(
                prompt.contains("Do NOT run ANY git command except read-only lookup commands"),
                "git status/git diff references must appear only in read-only allowlist context"
            );
        }
    }
}

#[test]
fn test_prompt_developer_iteration_with_context() {
    let context = TemplateContext::default();
    let result = prompt_developer_iteration_with_context(
        &context,
        2,
        5,
        ContextLevel::Normal,
        "test prompt",
        "test plan",
    );
    // Agent should receive PROMPT and PLAN content directly
    assert!(result.contains("test prompt"));
    assert!(result.contains("test plan"));
    assert!(result.contains("IMPLEMENTATION MODE"));
    // Agent should NOT be told to read PROMPT.md (orchestrator handles it)
    assert!(!result.contains("PROMPT.md"));
    assert!(!result.contains("PLAN.md"));
}

#[test]
fn test_prompt_developer_iteration_with_context_minimal() {
    let context = TemplateContext::default();
    let result = prompt_developer_iteration_with_context(
        &context,
        1,
        5,
        ContextLevel::Minimal,
        "test prompt",
        "test plan",
    );
    // Agent should receive PROMPT and PLAN content directly
    assert!(result.contains("test prompt"));
    assert!(result.contains("test plan"));
    assert!(!result.contains("PROMPT.md"));
    assert!(!result.contains("PLAN.md"));
}

#[test]
fn test_prompt_plan_with_context() {
    use crate::workspace::MemoryWorkspace;
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let result = prompt_plan_with_context(&context, None, &workspace);
    assert!(result.contains("PLANNING MODE"));
    assert!(result.contains("<ralph-implementation-steps>"));
    assert!(result.contains("<ralph-critical-files>"));
    assert!(result.contains("<ralph-verification-strategy>"));
    assert!(result.contains("READ-ONLY"));
    assert!(result.contains("STRICTLY PROHIBITED"));
    assert!(result.contains("PHASE 1: UNDERSTANDING"));
    assert!(result.contains("PHASE 2: EXPLORATION"));
    assert!(result.contains("PHASE 3: DESIGN"));
    assert!(result.contains("PHASE 4: REVIEW"));
    assert!(result.contains("PHASE 5: WRITE STRUCTURED PLAN"));
    assert!(result.contains("<ralph-plan>"));
    assert!(
        result.contains(
            "Choose a complete solution that fixes the problem at the root cause. Do not plan surface-level fixes or partial implementations."
        ),
        "Planning prompt should center design guidance on complete root-cause solutions"
    );
    assert!(
        !result.contains("Prefer the simplest approach that satisfies all requirements."),
        "Planning prompt should not bias toward simplistic approaches"
    );
}

#[test]
fn test_prompt_plan_with_context_uses_progress_based_anti_runaway_policy() {
    use crate::workspace::MemoryWorkspace;

    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let result = prompt_plan_with_context(&context, None, &workspace);

    assert!(
        result.contains("ANTI-RUNAWAY POLICY") && result.contains("PROGRESS-BASED"),
        "Planning prompt should require a progress-based anti-runaway policy"
    );
    assert!(
        result.contains("required_sections")
            && result.contains("objective")
            && result.contains("scope")
            && result.contains("steps")
            && result.contains("risks")
            && result.contains("verification"),
        "Planning prompt should define goal-coverage gates for required sections"
    );
    assert!(
        result.contains("unresolved_unknowns") && result.contains("max 3"),
        "Planning prompt should cap unresolved unknowns"
    );
    assert!(
        result.contains("stagnation_count") && result.contains("no materially new info"),
        "Planning prompt should track stagnation based on novelty"
    );
    assert!(
        result.contains("stagnation_count reaches 2")
            && result.contains("stop exploration")
            && result.contains("start drafting"),
        "Planning prompt should force draft mode after repeated non-novel exploration"
    );
    assert!(
        result.contains("convert extras into explicit investigation steps"),
        "Planning prompt should convert excess unknowns into explicit investigation steps"
    );
    assert!(
        result.contains("one critique pass"),
        "Planning prompt should include a bounded critique stage"
    );
    assert!(
        result.contains("at most one targeted re-exploration pass"),
        "Planning prompt should cap critique-driven re-exploration"
    );
    assert!(
        result.contains("Then finalize plan") && result.contains("no further exploration allowed"),
        "Planning prompt should hard-stop exploration after finalization"
    );
    assert!(
        result.contains("open questions")
            && result.contains("research")
            && result.contains("explicit investigation"),
        "Planning prompt should convert open questions/unknowns into explicit research steps when needed"
    );
    assert!(
        !result.contains("MAX 50 read/search operations"),
        "Planning prompt should avoid fixed operation hard caps"
    );
}

#[test]
fn test_prompt_plan_with_context_is_concise_and_non_redundant() {
    use crate::workspace::MemoryWorkspace;

    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let result = prompt_plan_with_context(&context, None, &workspace);

    assert!(
        !result.contains("OPENCODE") && !result.contains("Claude"),
        "Planning prompt should avoid external branding/style references"
    );
    assert!(
        result.contains("READ-ONLY") && result.contains("STRICTLY PROHIBITED"),
        "Planning prompt must keep explicit read-only constraints"
    );
    assert!(
        result.contains("non-mutating") || result.contains("image"),
        "Planning prompt should allow non-mutating tooling like file reading and image analysis"
    );
}

#[test]
fn test_prompt_plan_with_context_and_content() {
    use crate::workspace::MemoryWorkspace;
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let prompt_md = "# Test Prompt\n\nThis is the content.";
    let result = prompt_plan_with_context(&context, Some(prompt_md), &workspace);
    assert!(result.contains("USER REQUIREMENTS:"));
    assert!(result.contains("This is the content."));
    assert!(!result.contains("PROMPT.md"));
    assert!(result.contains("PLANNING MODE"));
    assert!(result.contains("PHASE 1: UNDERSTANDING"));
}

#[test]
fn test_context_based_prompts_isolate_from_git() {
    use crate::workspace::MemoryWorkspace;
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let prompts = vec![
        prompt_developer_iteration_with_context(&context, 1, 3, ContextLevel::Minimal, "", ""),
        prompt_developer_iteration_with_context(&context, 2, 3, ContextLevel::Normal, "", ""),
        prompt_plan_with_context(&context, None, &workspace),
    ];

    for prompt in prompts {
        assert!(
            !prompt.contains("git commit"),
            "Developer prompt should not tell agent to run git commit"
        );
        assert!(
            !prompt.contains("git add"),
            "Developer prompt should not tell agent to run git add"
        );

        if prompt.contains("git status") || prompt.contains("git diff") {
            assert!(
                prompt.contains("Do NOT run ANY git command except read-only lookup commands"),
                "git status/git diff references must appear only in read-only allowlist context"
            );
        }
    }
}

#[test]
fn test_context_based_uses_workspace_rooted_paths() {
    use crate::workspace::MemoryWorkspace;

    // Create a workspace with a different root than current_dir
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();

    // Test that context-based planning function uses workspace-rooted paths
    let with_context_plan = prompt_plan_with_context(&context, None, &workspace);

    // The output should contain absolute paths rooted at the workspace
    // not at the process current_dir()
    let workspace_root = workspace.root().to_string_lossy();
    if with_context_plan.contains(".agent/tmp/plan.xml") {
        // If the path is in the output, verify it's workspace-rooted
        assert!(
            with_context_plan.contains(workspace_root.as_ref()),
            "Context-based prompt should use workspace-rooted paths, found plan path without workspace root"
        );
    }

    // Test that context-based developer iteration function works correctly
    let _with_context_dev = prompt_developer_iteration_with_context(
        &context,
        1,
        3,
        ContextLevel::Normal,
        "prompt",
        "plan",
    );

    let xsd_retry_workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/last_output.xml",
        "<ralph-development-result><ralph-status>partial</ralph-status></ralph-development-result>",
    );
    let continuation_xsd_retry = prompt_developer_iteration_xsd_retry_with_context_files(
        &context,
        "Test error",
        &xsd_retry_workspace,
        true,
    );
    assert!(
        continuation_xsd_retry.contains("development_continuation_result.xsd"),
        "Continuation-mode XSD retry should point at the continuation schema"
    );
    assert!(
        !continuation_xsd_retry.contains("Read .agent/tmp/development_result.xsd"),
        "Continuation-mode XSD retry should not point at the generic development schema"
    );

    // Both should contain the core content (PROMPT and PLAN)
    // The context-based version is designed to be the production API
    assert!(with_context_plan.contains("PLANNING MODE"));
}

#[test]
fn test_regular_functions_use_cwd_rooted_paths() {
    use std::env;

    // Test that regular (test-only) functions use current_dir
    let regular_plan = prompt_plan(None);

    // The regular function uses WorkspaceFs::new(env::current_dir())
    // so paths are rooted at CWD
    let binding = env::current_dir().unwrap();
    let cwd = binding.to_string_lossy();
    if regular_plan.contains(".agent/tmp/plan.xml") {
        // The path should be rooted at CWD, not necessarily at a workspace root
        // This is the test-only legacy behavior
        assert!(
            regular_plan.contains(cwd.as_ref()) || regular_plan.contains("/tmp/"),
            "Regular prompt function should use CWD-rooted paths (test-only legacy behavior)"
        );
    }
}

#[test]
fn test_prompt_developer_iteration_xml_with_context_renders_shared_partials() {
    use crate::workspace::MemoryWorkspace;
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();

    let result = prompt_developer_iteration_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        &workspace,
    );

    assert!(result.contains("test prompt"));
    assert!(result.contains("test plan"));
    assert!(result.contains("IMPLEMENTATION MODE"));

    // Shared partials should be expanded
    assert!(
        result.contains("*** UNATTENDED MODE - NO USER INTERACTION ***"),
        "developer_iteration_xml should render shared/_unattended_mode partial"
    );
    assert!(
        !result.contains("{{>"),
        "developer_iteration_xml should not contain raw partial directives"
    );
}

// =========================================================================
// Tests for _with_references variants
// =========================================================================

#[test]
fn test_prompt_developer_iteration_xml_with_references_small_content() {
    use crate::prompts::content_builder::PromptContentBuilder;
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();

    let refs = PromptContentBuilder::new(&workspace)
        .with_prompt("Small prompt content".to_string())
        .with_plan("Small plan content".to_string())
        .build();

    let result = prompt_developer_iteration_xml_with_references(&context, &refs, &workspace);

    // Should embed content inline
    assert!(result.contains("Small prompt content"));
    assert!(result.contains("Small plan content"));
    assert!(result.contains("IMPLEMENTATION MODE"));

    // Shared partials should be expanded
    assert!(
        result.contains("*** UNATTENDED MODE - NO USER INTERACTION ***"),
        "developer_iteration_xml should render shared/_unattended_mode partial"
    );
    assert!(
        !result.contains("{{>"),
        "developer_iteration_xml should not contain raw partial directives"
    );
}

#[test]
fn test_prompt_developer_iteration_xml_with_references_large_prompt() {
    use crate::prompts::content_builder::PromptContentBuilder;
    use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test().with_file(".agent/PROMPT.md.backup", "backup");

    let context = TemplateContext::default();
    let large_prompt = "x".repeat(MAX_INLINE_CONTENT_SIZE + 1);

    let refs = PromptContentBuilder::new(&workspace)
        .with_prompt(large_prompt)
        .with_plan("Small plan".to_string())
        .build();

    let result = prompt_developer_iteration_xml_with_references(&context, &refs, &workspace);

    // Should reference backup file, not embed content
    assert!(result.contains("PROMPT.md.backup"));
    assert!(result.contains("Read from"));
    assert!(result.contains("Small plan"));
}

#[test]
fn test_prompt_developer_iteration_xml_with_references_large_plan() {
    use crate::prompts::content_builder::PromptContentBuilder;
    use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();
    let large_plan = "p".repeat(MAX_INLINE_CONTENT_SIZE + 1);

    let refs = PromptContentBuilder::new(&workspace)
        .with_prompt("Small prompt".to_string())
        .with_plan(large_plan)
        .build();

    let result = prompt_developer_iteration_xml_with_references(&context, &refs, &workspace);

    // Should reference PLAN.md file, not embed content
    assert!(result.contains(".agent/PLAN.md"));
    assert!(result.contains("plan.xml"));
    assert!(result.contains("Small prompt"));
}

#[test]
fn test_prompt_planning_xml_with_references_small_content() {
    use crate::prompts::content_reference::PromptContentReference;
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();

    let prompt_ref = PromptContentReference::from_content(
        "Small requirements".to_string(),
        Path::new(".agent/PROMPT.md.backup"),
        "User requirements",
    );

    let result = prompt_planning_xml_with_references(&context, &prompt_ref, &workspace);

    // Should embed content inline
    assert!(result.contains("Small requirements"));
    assert!(result.contains("PLANNING MODE"));

    // Read-only modes: planner must still write exactly one XML file.
    assert!(
        result.contains("explicitly authorized") && result.contains("EXACTLY ONE file"),
        "planning_xml should explicitly authorize writing exactly one XML file"
    );
    assert!(
        result.contains("MANDATORY"),
        "planning_xml should mark XML file write mandatory"
    );
    assert!(
        result.contains("Not writing") && result.contains("FAILURE"),
        "planning_xml should say not writing XML is a failure"
    );
    assert!(
        result.contains("does not conform") && result.contains("XSD") && result.contains("FAILURE"),
        "planning_xml should say non-XSD XML is a failure"
    );
    assert!(
        result.contains("READ-ONLY")
            && (result.contains("EXCEPT FOR writing")
                || result.contains("except for writing")
                || result.contains("Except for writing"))
            && result.contains("plan.xml"),
        "planning_xml should be read-only except for writing plan.xml"
    );

    assert!(
        !result.contains("DO NOT print")
            && !result.contains("Do NOT print")
            && !result.contains("ONLY acceptable output")
            && !result.contains("The ONLY acceptable output"),
        "planning_xml should not include stdout suppression wording"
    );
}

#[test]
fn test_prompt_planning_xml_with_references_large_content() {
    use crate::prompts::content_reference::{PromptContentReference, MAX_INLINE_CONTENT_SIZE};
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test().with_file(".agent/PROMPT.md.backup", "backup");
    let context = TemplateContext::default();
    let large_content = "x".repeat(MAX_INLINE_CONTENT_SIZE + 1);

    let prompt_ref = PromptContentReference::from_content(
        large_content,
        Path::new(".agent/PROMPT.md.backup"),
        "User requirements",
    );

    let result = prompt_planning_xml_with_references(&context, &prompt_ref, &workspace);

    // Should reference backup file, not embed content
    assert!(result.contains("PROMPT.md.backup"));
    assert!(result.contains("Read from"));
    assert!(result.contains("PLANNING MODE"));
}

#[test]
fn test_prompt_planning_xml_with_references_writes_xsd() {
    use crate::prompts::content_reference::PromptContentReference;
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();

    let prompt_ref = PromptContentReference::inline("Test requirements".to_string());

    let _result = prompt_planning_xml_with_references(&context, &prompt_ref, &workspace);

    // Should have written the XSD schema file
    assert!(workspace.exists(Path::new(".agent/tmp/plan.xsd")));
}

#[test]
fn test_prompt_planning_xsd_retry_with_context_has_read_only_overrides() {
    use crate::workspace::MemoryWorkspace;

    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();

    let result = prompt_planning_xsd_retry_with_context(
        &context,
        "prompt content",
        "XSD error",
        "last output",
        &workspace,
    );

    assert!(result.contains("XSD error"));
    assert!(result.contains(".agent/tmp/plan.xsd"));
    assert!(result.contains(".agent/tmp/last_output.xml"));

    assert!(
        result.contains("explicitly authorized") && result.contains("EXACTLY ONE file"),
        "planning_xsd_retry should explicitly authorize writing exactly one XML file"
    );
    assert!(
        result.contains("MANDATORY"),
        "planning_xsd_retry should mark XML file write mandatory"
    );
    assert!(
        result.contains("Not writing") && result.contains("FAILURE"),
        "planning_xsd_retry should say not writing XML is a failure"
    );
    assert!(
        result.contains("does not conform") && result.contains("XSD") && result.contains("FAILURE"),
        "planning_xsd_retry should say non-XSD XML is a failure"
    );
    assert!(
        result.contains("READ-ONLY")
            && (result.contains("EXCEPT FOR writing")
                || result.contains("except for writing")
                || result.contains("Except for writing"))
            && result.contains("plan.xml"),
        "planning_xsd_retry should be read-only except for writing plan.xml"
    );

    assert!(
        !result.contains("DO NOT print")
            && !result.contains("Do NOT print")
            && !result.contains("ONLY acceptable output")
            && !result.contains("The ONLY acceptable output"),
        "planning_xsd_retry should not include stdout suppression wording"
    );

    // Verify files were written to workspace
    assert!(workspace.was_written(".agent/tmp/plan.xsd"));
    assert!(workspace.was_written(".agent/tmp/last_output.xml"));
}

#[test]
fn test_continuation_prompt_contains_expected_elements() {
    use crate::reducer::state::{ContinuationState, DevelopmentStatus};
    use crate::workspace::MemoryWorkspace;

    let context = TemplateContext::default();
    let continuation_state = ContinuationState::new().trigger_continuation(
        DevelopmentStatus::Partial,
        "Implemented half the feature".to_string(),
        Some(vec!["src/lib.rs".to_string(), "src/main.rs".to_string()]),
        Some("Add tests for the new functionality".to_string()),
    );
    let workspace = MemoryWorkspace::new_test();
    let prompt =
        prompt_developer_iteration_continuation_xml(&context, &continuation_state, &workspace);

    // Debug: print the prompt to see what we're actually getting
    eprintln!("Generated prompt:\n{prompt}");

    // Verify the prompt contains key elements
    assert!(
        prompt.contains("IMPLEMENTATION MODE"),
        "Prompt should match iteration mode framing"
    );
    assert!(
        prompt.contains("CONTINUATION CONTEXT"),
        "Prompt should include minimal continuation context section"
    );
    assert!(
        prompt.contains("partial"),
        "Prompt should include previous status"
    );
    assert!(
        prompt.contains("Implemented half the feature"),
        "Prompt should include previous summary"
    );
    assert!(
        prompt.contains("Add tests for the new functionality"),
        "Prompt should include next steps when provided"
    );
    assert!(
        prompt.contains("failed to fully complete the entire plan")
            || prompt.contains("did not fully complete the entire plan"),
        "Continuation prompt should frame continuation as failure to fully complete the plan"
    );
    assert!(
        prompt.contains(
            "Success in this run means finishing the entire remaining plan to completion"
        ) || prompt.contains("Success means finishing the entire remaining plan to completion"),
        "Continuation prompt should set full plan completion as the default expectation"
    );
    assert!(
        prompt.contains("Going beyond the plan is acceptable")
            || prompt.contains("going beyond the plan is acceptable")
            || prompt.contains("acceptable to do more than the minimum remaining plan work"),
        "Continuation prompt should explicitly allow exceeding the minimum plan when that improves completion"
    );
    assert!(
        prompt.contains("ordered, actionable checklist"),
        "Continuation prompt should require an ordered checklist for recovery work"
    );
    assert!(
        prompt.contains("should resolve the remaining plan when completed"),
        "Continuation prompt should treat the passed checklist as sufficient to resolve the remaining plan"
    );
    assert!(
        prompt.contains("starting point")
            || prompt.contains("not the boundary of the remaining work"),
        "Continuation prompt should treat the passed checklist as a starting point, not a hard boundary"
    );
    assert!(
        prompt
            .to_lowercase()
            .contains("the plan is the goal, not the checklist")
            || prompt
                .to_lowercase()
                .contains("success is completing the plan, not finishing the checklist"),
        "Continuation prompt should explicitly prioritize full-plan completion over checklist completion"
    );
    assert!(
        prompt.contains("do whatever it takes to complete the entire remaining plan")
            || prompt.contains("complete the entire remaining plan by whatever work is required"),
        "Continuation prompt should frame continuation as full-plan completion work, not narrow recovery"
    );
    assert!(
        prompt.contains("ordered, actionable checklist") && prompt.contains("remaining plan"),
        "Continuation prompt should require an ordered actionable checklist for the remaining plan"
    );
    assert!(
        prompt.contains("starting point")
            || prompt.contains("not the boundary of the remaining work"),
        "Continuation prompt should treat prior continuation context as a starting point, not a boundary"
    );
    assert!(
        prompt
            .to_lowercase()
            .contains("use the previous summary and checklist as execution context"),
        "Continuation prompt should frame prior continuation data as execution context, not communication output"
    );
    assert!(
        prompt.contains("Continuation is an exception path")
            || prompt.contains("continuation is an exception path"),
        "Continuation prompt should explicitly frame continuation as exceptional"
    );
    assert!(
        prompt.contains("continuation 1 of"),
        "Prompt should include continuation progress label"
    );
    assert!(
        !prompt.contains("ANALYSIS AGENT ROLE"),
        "Prompt should not describe downstream orchestration"
    );
    assert!(
        prompt.contains("Do NOT create STATUS.md")
            && prompt.contains("CURRENT_STATUS.md")
            && prompt.contains("CURRENT_IMPLEMENTATION.md"),
        "Prompt should explicitly ban status/handoff files"
    );
    assert!(
        prompt.contains("Do NOT write summaries")
            || prompt.contains("Do NOT attempt to communicate"),
        "Prompt should ban summary-style communication"
    );
    assert!(
        !prompt.contains("Files changed in previous attempt:"),
        "Continuation prompt should avoid centering file-list bookkeeping"
    );
    assert!(
        !prompt.contains("src/lib.rs") && !prompt.contains("src/main.rs"),
        "Continuation prompt should not inline previous file lists"
    );
}

#[test]
fn test_continuation_prompt_includes_verification_guidance() {
    use crate::reducer::state::{ContinuationState, DevelopmentStatus};
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();
    let continuation_state = ContinuationState::new().trigger_continuation(
        DevelopmentStatus::Partial,
        "Previous work summary".to_string(),
        None,
        None,
    );

    let prompt =
        prompt_developer_iteration_continuation_xml(&context, &continuation_state, &workspace);

    // Should include new verification section
    assert!(
        prompt.contains("VERIFICATION AND VALIDATION"),
        "Continuation prompt should include verification guidance"
    );
    assert!(
        prompt.contains("build/test commands"),
        "Should mention build/test verification"
    );
    assert!(
        prompt.contains("If the plan specifies verification"),
        "Should mention plan-specified verification"
    );

    // Should include exploration section
    assert!(
        prompt.contains("EXPLORATION AND CONTEXT GATHERING"),
        "Should include exploration guidance"
    );
    assert!(
        prompt.contains("Read files beyond the plan"),
        "Should encourage broader exploration"
    );

    // Should NOT include old progress verification section
    assert!(
        !prompt.contains("You do NOT need to produce structured status output"),
        "Should not contain outdated verification section"
    );
    assert!(
        !prompt.contains("What was accomplished:"),
        "Should avoid broad summary/handoff sections"
    );
}

#[test]
fn test_continuation_prompt_emphasizes_recovery_over_incidental_activity() {
    use crate::reducer::state::{ContinuationState, DevelopmentStatus};
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();
    let continuation_state = ContinuationState::new().trigger_continuation(
        DevelopmentStatus::Failed,
        "Compilation failed after only partially implementing the plan".to_string(),
        Some(vec!["src/lib.rs".to_string(), "src/main.rs".to_string()]),
        Some(
            "1. Fix the compile errors\n2. Finish the remaining tests\n3. Re-run verification"
                .to_string(),
        ),
    );

    let prompt =
        prompt_developer_iteration_continuation_xml(&context, &continuation_state, &workspace);

    assert!(
        prompt.contains("Focus the continuation on completing the entire remaining plan")
            || prompt.contains("do whatever it takes to complete the entire remaining plan"),
        "Continuation prompt should center full-plan completion rather than narrow recovery"
    );
    assert!(
        prompt
            .to_lowercase()
            .contains("use the previous summary and checklist as execution context"),
        "Continuation prompt should frame prior continuation data as execution context, not communication output"
    );
    assert!(
        prompt.contains("remaining non-plan follow-up work discovered during verification")
            || prompt.contains("failed verification commands or checks"),
        "Continuation prompt should preserve the detailed recovery checklist framing from analysis output"
    );
    assert!(
        prompt.contains("starting point")
            || prompt.contains("not the boundary of the remaining work"),
        "Continuation prompt should tell the next run to expand beyond the handed-off checklist when needed"
    );
    assert!(
        prompt
            .to_lowercase()
            .contains("the plan is the goal, not the checklist")
            || prompt
                .to_lowercase()
                .contains("success is completing the plan, not finishing the checklist"),
        "Continuation prompt should explicitly prioritize full-plan completion over checklist completion"
    );
    assert!(
        prompt.contains(
            "actionable and specific enough for the next run to continue without ambiguity"
        ) || (prompt.contains("ordered, actionable checklist")
            && prompt.contains("specific enough for the next run to continue without ambiguity")),
        "Continuation prompt should require an actionable, ambiguity-free remaining-work checklist"
    );
    assert!(
        prompt.contains("remaining work needed to finish the entire plan")
            || prompt.contains("finish the entire remaining plan")
            || prompt.contains("finishing the remaining plan"),
        "Continuation prompt should center the whole remaining plan, not a local next step"
    );
    assert!(
        !prompt.contains(
            "failed to fully complete the plan and failed to fully complete the entire plan"
        ),
        "Continuation prompt should avoid duplicated whole-plan failure wording"
    );
    assert!(
        !prompt.contains(
            "Provide an ordered, actionable checklist for the remaining plan and the remaining work needed to finish the entire plan"
        ),
        "Continuation prompt should avoid repetitive checklist wording"
    );
}

#[test]
fn test_continuation_prompt_includes_original_request_and_plan_sections() {
    use crate::reducer::state::{ContinuationState, DevelopmentStatus};
    use crate::workspace::MemoryWorkspace;
    use std::fs;
    use tempfile::tempdir;

    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Original request body")
        .with_file(".agent/PLAN.md", "Implementation plan body");
    let context = TemplateContext::default();
    let continuation_state = ContinuationState::new().trigger_continuation(
        DevelopmentStatus::Partial,
        "Previous work summary".to_string(),
        None,
        None,
    );

    let prompt =
        prompt_developer_iteration_continuation_xml(&context, &continuation_state, &workspace);

    assert!(
        prompt.contains("ORIGINAL REQUEST"),
        "Continuation prompt should include ORIGINAL REQUEST section"
    );
    assert!(
        prompt.contains("IMPLEMENTATION PLAN"),
        "Continuation prompt should include IMPLEMENTATION PLAN section"
    );
    assert!(
        prompt.contains("Original request body"),
        "Continuation prompt should include prompt content"
    );
    assert!(
        prompt.contains("Implementation plan body"),
        "Continuation prompt should include plan content"
    );

    let template_dir = tempdir().expect("create temp template dir");
    fs::write(
        template_dir
            .path()
            .join("developer_iteration_continuation_xml.txt"),
        "broken {{MISSING_VARIABLE}} template",
    )
    .expect("write broken continuation template");

    let context = TemplateContext::from_user_templates_dir(Some(template_dir.path().to_path_buf()));
    let continuation_state = ContinuationState::new().trigger_continuation(
        DevelopmentStatus::Partial,
        "Previous work summary".to_string(),
        None,
        Some("1. Finish the remaining work".to_string()),
    );
    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Original request body")
        .with_file(".agent/PLAN.md", "Implementation plan body");

    let fallback_prompt =
        prompt_developer_iteration_continuation_xml(&context, &continuation_state, &workspace);

    assert!(
        fallback_prompt.contains("ORIGINAL REQUEST"),
        "Fallback continuation prompt should include ORIGINAL REQUEST section"
    );
    assert!(
        fallback_prompt.contains("IMPLEMENTATION PLAN"),
        "Fallback continuation prompt should include IMPLEMENTATION PLAN section"
    );
    assert!(
        fallback_prompt.contains("Original request body"),
        "Fallback continuation prompt should preserve prompt content"
    );
    assert!(
        fallback_prompt.contains("Implementation plan body"),
        "Fallback continuation prompt should preserve plan content"
    );
    assert!(
        fallback_prompt.contains("Success means finishing the entire remaining plan to completion")
            || fallback_prompt.contains(
                "Success in this run means finishing the entire remaining plan to completion"
            ),
        "Fallback continuation prompt should preserve whole-plan completion framing"
    );
    assert!(
        fallback_prompt.contains("1. Finish the remaining work"),
        "Fallback continuation prompt should preserve the ordered recovery checklist when provided"
    );
    assert!(
        fallback_prompt.contains("VERIFICATION AND VALIDATION")
            && fallback_prompt.contains("EXPLORATION AND CONTEXT GATHERING"),
        "Fallback continuation prompt should preserve shared developer iteration guidance"
    );
    assert!(
        fallback_prompt.contains("Do NOT run ANY git command except read-only lookup commands"),
        "Fallback continuation prompt should preserve read-only git guidance"
    );
    assert!(
        !fallback_prompt.contains(
            "failed to fully complete the plan and failed to fully complete the entire plan"
        ),
        "Fallback continuation prompt should avoid duplicated whole-plan failure wording"
    );
    assert!(
        !fallback_prompt.contains(
            "Provide an ordered, actionable checklist for the remaining plan and the remaining work needed to finish the entire plan"
        ),
        "Fallback continuation prompt should avoid repetitive checklist wording"
    );

    let true_fallback_prompt = fallback_continuation_prompt(
        2,
        "failed",
        "The full plan was not completed because verification still fails.",
        Some(
            "1. Fix the failing verification.\n2. Re-run the focused continuation tests.\n3. Finish the remaining plan and run repository verification.",
        ),
        "Original request body",
        "Implementation plan body",
    );

    assert!(
        true_fallback_prompt.contains("UNATTENDED MODE")
            && true_fallback_prompt
                .contains("Do NOT run ANY git command except read-only lookup commands"),
        "True string fallback should preserve unattended and read-only git guidance"
    );
    assert!(
        true_fallback_prompt.contains("VERIFICATION AND VALIDATION")
            && true_fallback_prompt.contains("EXPLORATION AND CONTEXT GATHERING"),
        "True string fallback should preserve shared developer iteration guidance"
    );
    assert!(
        true_fallback_prompt.contains("ORIGINAL REQUEST")
            && true_fallback_prompt.contains("IMPLEMENTATION PLAN")
            && true_fallback_prompt.contains("Original request body")
            && true_fallback_prompt.contains("Implementation plan body"),
        "True string fallback should preserve original request and plan context"
    );
}

#[test]
fn test_initial_iteration_prompt_includes_verification_guidance() {
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Test prompt")
        .with_file(".agent/PLAN.md", "Test plan");
    let context = TemplateContext::default();

    let prompt = prompt_developer_iteration_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        &workspace,
    );

    // Should include verification section
    assert!(
        prompt.contains("VERIFICATION AND VALIDATION"),
        "Initial iteration prompt should include verification guidance"
    );

    // Should include exploration section
    assert!(
        prompt.contains("EXPLORATION AND CONTEXT GATHERING"),
        "Should include exploration guidance"
    );

    // Should NOT include old progress verification wording
    assert!(
        !prompt.contains("You do NOT need to produce structured status output"),
        "Should not contain outdated verification section"
    );
}
