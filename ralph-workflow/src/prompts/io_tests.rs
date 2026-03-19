use super::*;
use crate::prompts::template_context::TemplateContext;
use crate::workspace::MemoryWorkspace;
use std::path::PathBuf;

#[test]
fn test_prompt_for_agent_developer() {
    let template_context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let result = prompt_for_agent(
        Role::Developer,
        Action::Iterate,
        ContextLevel::Normal,
        &template_context,
        PromptConfig::new()
            .with_iterations(3, 10)
            .with_prompt_and_plan("test prompt".to_string(), "test plan".to_string()),
        &workspace,
    );
    // Agent should NOT be told to read PROMPT.md (orchestrator handles it)
    assert!(!result.contains("PROMPT.md"));
    assert!(result.contains("test prompt"));
    assert!(result.contains("test plan"));
}

#[test]
fn test_prompt_for_agent_reviewer() {
    // Use the actual review prompt function that's used in production
    let workspace = MemoryWorkspace::new_test();
    let result = prompt_review_xml_with_context(
        &TemplateContext::default(),
        "sample prompt",
        "sample plan",
        "sample diff",
        &workspace,
    );
    // Verify the review_xml template behavior
    assert!(result.contains("REVIEW MODE"));
    assert!(result.contains("CRITICAL CONSTRAINTS"));
    assert!(result.contains("DO NOT MODIFY"));
}

#[test]
fn test_prompt_for_agent_plan() {
    let template_context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let result = prompt_for_agent(
        Role::Developer,
        Action::Plan,
        ContextLevel::Normal,
        &template_context,
        PromptConfig::new().with_prompt_md("test requirements".to_string()),
        &workspace,
    );
    // Plan is now returned as XML structured output
    assert!(result.contains("PLANNING MODE"));
    assert!(result.contains("<ralph-implementation-steps>"));
}

#[test]
fn test_prompts_are_agent_agnostic() {
    // All prompts should be free of agent-specific references
    // to ensure they work with any AI coding assistant
    let agent_specific_terms = [
        "claude", "codex", "opencode", "gemini", "aider", "goose", "cline", "amazon-q", "gpt",
        "copilot",
        // Note: "continue" is excluded as it's also a common English verb
    ];

    let template_context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let prompts_to_check: Vec<(&'static str, String)> = vec![
        (
            "developer_iteration_normal",
            prompt_developer_iteration(1, 5, ContextLevel::Normal, "", ""),
        ),
        (
            "developer_iteration_minimal",
            prompt_developer_iteration(1, 5, ContextLevel::Minimal, "", ""),
        ),
        (
            "review_xml",
            prompt_review_xml_with_context(&template_context, "", "", "sample diff", &workspace),
        ),
        ("fix", prompt_fix("", "", "")),
        ("plan", prompt_plan(None)),
        (
            "generate_commit_message",
            prompt_generate_commit_message_with_diff("diff --git a/a b/b"),
        ),
    ];

    for (prompt_name, prompt) in &prompts_to_check {
        let prompt_lower = prompt.to_lowercase();
        for term in agent_specific_terms {
            if prompt_lower.contains(term) {
                // Check if this is in a file path (acceptable) vs prompt content (not acceptable).
                // File paths can appear in various contexts:
                // 1. Absolute paths: /path/to/.agent/claude/log.txt
                // 2. Relative paths: .agent/opencode/output.txt
                // 3. Mid-line paths: "See file: .agent/claude/log.txt"
                // 4. Windows paths: C:\.agent\claude\log.txt
                //
                // We use a more robust detection: check if the term appears within a path-like context
                // by looking for path separators (/ or \) near the term, or in .agent/ directory paths.
                let mut found_non_path_occurrence = false;

                for line in prompt.lines() {
                    let line_lower = line.to_lowercase();
                    if line_lower.contains(term) {
                        // Find all occurrences of the term in this line
                        let mut search_start = 0;
                        while let Some(pos) = line_lower[search_start..].find(term) {
                            let actual_pos = search_start + pos;
                            let term_end = actual_pos + term.len();

                            // Extract context around the term (50 chars before and after)
                            let context_start = actual_pos.saturating_sub(50);
                            let context_end = (term_end + 50).min(line_lower.len());
                            let context = &line_lower[context_start..context_end];

                            // Check if this occurrence is in a path context:
                            // - Contains .agent/ or .agent\ (most common case for this codebase)
                            // - Contains multiple path separators (/ or \)
                            // - Starts with / (Unix absolute path)
                            // - Contains :/ or :\ (Windows absolute path or URL)
                            let is_path_context = context.contains("/.agent/")
                                || context.contains("\\.agent\\")
                                || context.contains(".agent/")
                                || context.contains(".agent\\")
                                || context.matches('/').count() >= 2
                                || context.matches('\\').count() >= 2
                                || context.starts_with('/')
                                || context.contains(":/")
                                || context.contains(":\\")
                                || context.contains("file://");

                            if !is_path_context {
                                found_non_path_occurrence = true;
                                break;
                            }

                            search_start = term_end;
                        }

                        if found_non_path_occurrence {
                            break;
                        }
                    }
                }

                assert!(
                    !found_non_path_occurrence,
                    "Prompt '{prompt_name}' contains agent-specific term '{term}' in non-path context"
                );
            }
        }
    }
}

#[test]
fn test_prompt_for_agent_fix() {
    let template_context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let result = prompt_for_agent(
        Role::Developer,
        Action::Fix,
        ContextLevel::Normal,
        &template_context,
        PromptConfig::new().with_prompt_plan_and_issues(
            "test prompt".to_string(),
            "test plan".to_string(),
            "test issues".to_string(),
        ),
        &workspace,
    );
    assert!(result.contains("FIX MODE"));
    assert!(result.contains("test issues"));
    // Should include PROMPT and PLAN context
    assert!(result.contains("test prompt"));
    assert!(result.contains("test plan"));
}

#[test]
fn test_prompt_for_agent_fix_with_empty_context() {
    let template_context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let result = prompt_for_agent(
        Role::Developer,
        Action::Fix,
        ContextLevel::Normal,
        &template_context,
        PromptConfig::new(),
        &workspace,
    );
    assert!(result.contains("FIX MODE"));
    // Should still work with empty context
    assert!(!result.is_empty());
}

#[test]
fn test_reviewer_can_use_iterate_action() {
    // Edge case: Reviewer using Iterate action (fallback behavior)
    let template_context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let result = prompt_for_agent(
        Role::Reviewer,
        Action::Iterate,
        ContextLevel::Normal,
        &template_context,
        PromptConfig::new()
            .with_iterations(1, 3)
            .with_prompt_and_plan(String::new(), String::new()),
        &workspace,
    );
    // Should fall back to developer iteration prompt
    assert!(result.contains("IMPLEMENTATION MODE"));
}

#[test]
fn test_prompts_do_not_have_detailed_tracking_language() {
    // Prompts should NOT contain detailed history tracking language
    // to prevent context contamination in future runs
    let detailed_tracking_terms = [
        "iteration number",
        "phase completed",
        "previous iteration",
        "history of",
        "detailed log",
    ];

    let prompts_to_check = vec![
        prompt_developer_iteration(1, 5, ContextLevel::Normal, "", ""),
        prompt_fix("", "", ""),
    ];

    for prompt in prompts_to_check {
        let prompt_lower = prompt.to_lowercase();
        for term in detailed_tracking_terms {
            assert!(
                !prompt_lower.contains(term),
                "Prompt contains detailed tracking language '{}': {}",
                term,
                &prompt[..prompt.len().min(100)]
            );
        }
    }
}

#[test]
fn test_developer_notes_md_not_referenced() {
    // Developer prompt should NOT mention NOTES.md at all (isolation mode)
    let developer_prompt = prompt_developer_iteration(1, 5, ContextLevel::Normal, "", "");
    assert!(
        !developer_prompt.contains("NOTES.md"),
        "Developer prompt should not reference NOTES.md in isolation mode"
    );
}

#[test]
fn test_all_prompts_isolate_agents_from_git() {
    // AC3: "AI agent does not know that we have previous committed change"
    // All prompts should NOT tell agents to run git commands
    // Git operations are handled by the orchestrator via libgit2

    // These patterns indicate the agent is being instructed to RUN git commands
    // We exclude patterns that are part of constraint lists (like "MUST NOT run X, Y, Z")
    let instructive_git_patterns = [
        "Run `git",
        "run git",
        "execute git",
        "Try: git",
        "you can git",
        "should run git",
        "please run git",
        "\ngit ", // Command starting at line beginning after newline
    ];

    // Context patterns that indicate the command is being FORBIDDEN, not instructed
    // These should be excluded from the check
    let forbid_contexts = [
        "MUST NOT run",
        "DO NOT run",
        "must not run",
        "do not run",
        "NOT run commands",
        "commands (",
        "commands:",
        "including:",
        "such as",
    ];

    // Special case: "Use git" is allowed in fix_mode_xml.txt for fault tolerance
    // when issue descriptions lack file context - the fixer needs to find the relevant code
    // This is part of the recovery mechanism for vague issues

    let template_context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let prompts_to_check: Vec<String> = vec![
        prompt_developer_iteration(1, 5, ContextLevel::Normal, "", ""),
        prompt_developer_iteration(1, 5, ContextLevel::Minimal, "", ""),
        prompt_review_xml_with_context(&template_context, "", "", "sample diff", &workspace),
        // Note: fix_mode_xml.txt is intentionally excluded from "Use git" check
        // because it contains "Use git grep/rg ONLY when issue descriptions lack file context"
        // which is part of the fault tolerance design
        prompt_fix("", "", ""),
        prompt_plan(None),
        prompt_generate_commit_message_with_diff("diff --git a/a b/b\n"),
    ];

    for prompt in prompts_to_check {
        for pattern in instructive_git_patterns {
            if prompt.contains(pattern) {
                // Check if this is in a "forbidden" context
                let is_forbidden = forbid_contexts.iter().any(|ctx| {
                    prompt.find(ctx).is_some_and(|pos| {
                        prompt[pos..].find(pattern).is_some_and(|pattern_pos| {
                            // Pattern is within reasonable proximity (200 chars) of forbid context
                            pattern_pos < 200
                        })
                    })
                });

                assert!(
                    is_forbidden,
                    "Prompt contains instructive git command pattern '{}': {}",
                    pattern,
                    &prompt[..prompt.len().min(150)]
                );
            }
        }
    }

    // Verify the orchestrator-specific function for commit message generation
    // DOES contain the diff content (orchestrator receives diff, not git commands).
    // The orchestrator uses this function to pass diff to the LLM via stdin.
    let orchestrator_prompt = prompt_generate_commit_message_with_diff("some diff");
    assert!(
        orchestrator_prompt.contains("DIFF:") || orchestrator_prompt.contains("diff"),
        "Orchestrator prompt should contain the diff content for commit message generation"
    );
    // But the prompt should NOT tell the agent to run git commands (orchestrator handles git)
    for pattern in instructive_git_patterns {
        if orchestrator_prompt.contains(pattern) {
            // Check if this is in a "forbidden" context
            let is_forbidden = forbid_contexts.iter().any(|ctx| {
                orchestrator_prompt.find(ctx).is_some_and(|pos| {
                    orchestrator_prompt[pos..]
                        .find(pattern)
                        .is_some_and(|pattern_pos| pattern_pos < 200)
                })
            });

            assert!(
                is_forbidden,
                "Orchestrator prompt contains instructive git command pattern '{pattern}'"
            );
        }
    }
}

#[test]
fn test_prompt_with_resume_context() {
    let template_context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let result = prompt_for_agent(
        Role::Developer,
        Action::Iterate,
        ContextLevel::Normal,
        &template_context,
        PromptConfig::new()
            .with_resume(true)
            .with_iterations(2, 5)
            .with_prompt_and_plan("test prompt".to_string(), "test plan".to_string()),
        &workspace,
    );
    // Should include resume note
    assert!(result.contains("resuming from a previous run"));
    assert!(result.contains("git history"));
}

#[test]
fn test_prompt_with_rich_resume_context_development() {
    use crate::checkpoint::state::{PipelinePhase, RebaseState};

    let template_context = TemplateContext::default();

    // Create a resume context for development phase
    let resume_context = ResumeContext {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        reviewer_pass: 0,
        total_reviewer_passes: 3,
        resume_count: 1,
        rebase_state: RebaseState::NotStarted,
        run_id: "test-run-id".to_string(),
        prompt_history: None,
        execution_history: None,
    };

    let workspace = MemoryWorkspace::new_test();
    let result = prompt_for_agent(
        Role::Developer,
        Action::Iterate,
        ContextLevel::Normal,
        &template_context,
        PromptConfig::new()
            .with_resume_context(resume_context)
            .with_iterations(3, 5)
            .with_prompt_and_plan("test prompt".to_string(), "test plan".to_string()),
        &workspace,
    );

    // Should include rich resume context
    assert!(result.contains("SESSION RESUME CONTEXT"));
    assert!(result.contains("DEVELOPMENT phase"));
    assert!(result.contains("iteration 3 of 5"));
    assert!(result.contains("has been resumed 1 time"));
    assert!(result.contains("Continue working on the implementation"));
}

#[test]
fn test_prompt_with_rich_resume_context_review() {
    use crate::checkpoint::state::{PipelinePhase, RebaseState};

    let template_context = TemplateContext::default();

    // Create a resume context for review phase
    let resume_context = ResumeContext {
        phase: PipelinePhase::Review,
        iteration: 5,
        total_iterations: 5,
        reviewer_pass: 1,
        total_reviewer_passes: 3,
        resume_count: 2,
        rebase_state: RebaseState::NotStarted,
        run_id: "test-run-id".to_string(),
        prompt_history: None,
        execution_history: None,
    };

    let workspace = MemoryWorkspace::new_test();
    let result = prompt_for_agent(
        Role::Reviewer,
        Action::Fix,
        ContextLevel::Normal,
        &template_context,
        PromptConfig::new()
            .with_resume_context(resume_context)
            .with_prompt_plan_and_issues(
                "test prompt".to_string(),
                "test plan".to_string(),
                "test issues".to_string(),
            ),
        &workspace,
    );

    // Should include rich resume context for review
    assert!(result.contains("SESSION RESUME CONTEXT"));
    assert!(result.contains("REVIEW phase"));
    assert!(result.contains("pass 2 of 3"));
    assert!(result.contains("has been resumed 2 time"));
}

// Note: get_stored_or_generate_prompt tests are in prompt_dispatch.rs
