//! Embedded prompt templates for the policy crate.
//!
//! All canonical-named templates are embedded here via `include_str!`.
//! `ralph-workflow` loads templates by using the exported constants directly,
//! ensuring template ownership lives in `ralph-workflow-policy`.
//!
//! Template names use the canonical form (no `_xml` suffix).

// Shared partial templates (embedded for use by get_shared_partials in ralph-workflow)
pub const PARTIAL_CRITICAL_HEADER: &str = include_str!("../templates/shared/_critical_header.txt");
pub const PARTIAL_CONTEXT_SECTION: &str = include_str!("../templates/shared/_context_section.txt");
pub const PARTIAL_DIFF_SECTION: &str = include_str!("../templates/shared/_diff_section.txt");
pub const PARTIAL_DEVELOPER_ITERATION_GUIDANCE: &str =
    include_str!("../templates/shared/_developer_iteration_guidance.txt");
pub const PARTIAL_NO_GIT_COMMIT: &str = include_str!("../templates/shared/_no_git_commit.txt");
pub const PARTIAL_OUTPUT_CHECKLIST: &str =
    include_str!("../templates/shared/_output_checklist.txt");
pub const PARTIAL_SESSION_CAPABILITIES: &str =
    include_str!("../templates/shared/_session_capabilities.txt");
pub const PARTIAL_SAFETY_NO_EXECUTE: &str =
    include_str!("../templates/shared/_safety_no_execute.txt");
pub const PARTIAL_UNATTENDED_MODE: &str = include_str!("../templates/shared/_unattended_mode.txt");
pub const PARTIAL_MCP_TOOLS: &str = include_str!("../templates/shared/_mcp_tools.txt");

// Planning and development templates
pub const PLANNING_TEMPLATE: &str = include_str!("../templates/planning.txt");
pub const DEVELOPER_ITERATION_TEMPLATE: &str = include_str!("../templates/developer_iteration.txt");
pub const DEVELOPER_ITERATION_CONTINUATION_TEMPLATE: &str =
    include_str!("../templates/developer_iteration_continuation.txt");

// Review and fix templates
pub const REVIEW_TEMPLATE: &str = include_str!("../templates/review.txt");
pub const FIX_MODE_TEMPLATE: &str = include_str!("../templates/fix_mode.txt");

// Commit templates
pub const COMMIT_MESSAGE_TEMPLATE: &str = include_str!("../templates/commit_message.txt");
pub const COMMIT_SIMPLIFIED_TEMPLATE: &str = include_str!("../templates/commit_simplified.txt");

// Analysis system prompt templates
pub const ANALYSIS_SYSTEM_PROMPT_TEMPLATE: &str =
    include_str!("../templates/analysis_system_prompt.txt");
pub const FIX_ANALYSIS_SYSTEM_PROMPT_TEMPLATE: &str =
    include_str!("../templates/fix_analysis_system_prompt.txt");

// Parallel worker templates
pub const PARALLEL_PLANNING_TEMPLATE: &str = include_str!("../templates/parallel_planning.txt");
pub const PARALLEL_DEV_WORKER_TEMPLATE: &str = include_str!("../templates/parallel_dev_worker.txt");
pub const PARALLEL_VERIFIER_TEMPLATE: &str = include_str!("../templates/parallel_verifier.txt");

// Rebase/conflict resolution templates
pub const CONFLICT_RESOLUTION_TEMPLATE: &str = include_str!("../templates/conflict_resolution.txt");
pub const CONFLICT_RESOLUTION_FALLBACK_TEMPLATE: &str =
    include_str!("../templates/conflict_resolution_fallback.txt");

/// Retrieve an embedded policy template by its canonical name (without `.txt` extension).
///
/// # Returns
///
/// * `Some(&'static str)` — the embedded template content if the name is known.
/// * `None` — the name is not registered in the policy crate.
#[must_use]
pub fn get_policy_template(name: &str) -> Option<&'static str> {
    match name {
        "planning" => Some(PLANNING_TEMPLATE),
        "developer_iteration" => Some(DEVELOPER_ITERATION_TEMPLATE),
        "developer_iteration_continuation" => Some(DEVELOPER_ITERATION_CONTINUATION_TEMPLATE),
        "review" => Some(REVIEW_TEMPLATE),
        "fix_mode" => Some(FIX_MODE_TEMPLATE),
        "commit_message" => Some(COMMIT_MESSAGE_TEMPLATE),
        "commit_simplified" => Some(COMMIT_SIMPLIFIED_TEMPLATE),
        "analysis_system_prompt" => Some(ANALYSIS_SYSTEM_PROMPT_TEMPLATE),
        "fix_analysis_system_prompt" => Some(FIX_ANALYSIS_SYSTEM_PROMPT_TEMPLATE),
        "parallel_planning" => Some(PARALLEL_PLANNING_TEMPLATE),
        "parallel_dev_worker" => Some(PARALLEL_DEV_WORKER_TEMPLATE),
        "parallel_verifier" => Some(PARALLEL_VERIFIER_TEMPLATE),
        "conflict_resolution" => Some(CONFLICT_RESOLUTION_TEMPLATE),
        "conflict_resolution_fallback" => Some(CONFLICT_RESOLUTION_FALLBACK_TEMPLATE),
        _ => None,
    }
}

/// All canonical template names registered in the policy crate.
pub const CANONICAL_TEMPLATE_NAMES: &[&str] = &[
    "planning",
    "developer_iteration",
    "developer_iteration_continuation",
    "review",
    "fix_mode",
    "commit_message",
    "commit_simplified",
    "analysis_system_prompt",
    "fix_analysis_system_prompt",
    "parallel_planning",
    "parallel_dev_worker",
    "parallel_verifier",
    "conflict_resolution",
    "conflict_resolution_fallback",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_all_canonical_templates_are_non_empty() {
        for &name in CANONICAL_TEMPLATE_NAMES {
            let content = get_policy_template(name);
            assert!(
                content.is_some(),
                "template '{}' is in CANONICAL_TEMPLATE_NAMES but not found by get_policy_template",
                name
            );
            assert!(!content.unwrap().is_empty(), "template '{}' is empty", name);
        }
    }

    #[test]
    fn test_unknown_template_returns_none() {
        assert!(get_policy_template("nonexistent_template").is_none());
        assert!(get_policy_template("planning_xml").is_none());
        assert!(get_policy_template("developer_iteration_xml").is_none());
        assert!(get_policy_template("").is_none());
    }

    #[test]
    fn test_planning_template_content_exists() {
        assert!(
            !PLANNING_TEMPLATE.is_empty(),
            "planning template must not be empty"
        );
    }

    #[test]
    fn test_developer_iteration_template_content_exists() {
        assert!(
            !DEVELOPER_ITERATION_TEMPLATE.is_empty(),
            "developer_iteration template must not be empty"
        );
    }

    #[test]
    fn test_review_template_content_exists() {
        assert!(
            !REVIEW_TEMPLATE.is_empty(),
            "review template must not be empty"
        );
    }
}
