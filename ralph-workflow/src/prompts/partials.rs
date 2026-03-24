//! Shared template partials for template composition.
//!
//! This module provides common template sections that can be included
//! in other templates using the `{{> partial_name}}` syntax.
//!
//! # Usage
//!
//! ```ignore
//! use crate::prompts::{Template, partials::get_shared_partials};
//!
//! let partials = get_shared_partials();
//! let template = Template::new("{{> shared/_critical_header}}\nContent here");
//! let variables = HashMap::from([("MODE", "REVIEW MODE".to_string())]);
//! let rendered = template.render_with_partials(&variables, &partials)?;
//! ```
//!
//! # Available Partials
//!
//! - `shared/_critical_header` - "CRITICAL: You have NO access" warning
//! - `shared/_context_section` - PROMPT and PLAN context variables
//! - `shared/_diff_section` - DIFF display in code block
//! - `shared/_developer_iteration_guidance` - Shared implementation guidance
//! - `shared/_no_git_commit` - Prohibits any git commit operations
//! - `shared/_output_checklist` - Prioritized checklist output format
//! - `shared/_safety_no_execute` - No command execution, read-only mode
//! - `shared/_session_capabilities` - Session granted capabilities display
//! - `shared/_unattended_mode` - Automated pipeline, no user interaction

use std::collections::HashMap;

/// Get all shared partials as a `HashMap`.
///
/// Partials are loaded at compile time via `include_str!` for efficiency.
/// The `HashMap` uses partial name (without .txt extension) as the key.
#[must_use]
pub fn get_shared_partials() -> HashMap<String, String> {
    HashMap::from([
        (
            "shared/_critical_header".to_string(),
            include_str!("templates/shared/_critical_header.txt").to_string(),
        ),
        (
            "shared/_context_section".to_string(),
            include_str!("templates/shared/_context_section.txt").to_string(),
        ),
        (
            "shared/_diff_section".to_string(),
            include_str!("templates/shared/_diff_section.txt").to_string(),
        ),
        (
            "shared/_developer_iteration_guidance".to_string(),
            include_str!("templates/shared/_developer_iteration_guidance.txt").to_string(),
        ),
        (
            "shared/_no_git_commit".to_string(),
            include_str!("templates/shared/_no_git_commit.txt").to_string(),
        ),
        (
            "shared/_output_checklist".to_string(),
            include_str!("templates/shared/_output_checklist.txt").to_string(),
        ),
        (
            "shared/_session_capabilities".to_string(),
            include_str!("templates/shared/_session_capabilities.txt").to_string(),
        ),
        (
            "shared/_safety_no_execute".to_string(),
            include_str!("templates/shared/_safety_no_execute.txt").to_string(),
        ),
        (
            "shared/_unattended_mode".to_string(),
            include_str!("templates/shared/_unattended_mode.txt").to_string(),
        ),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_shared_partials_exist() {
        let partials = get_shared_partials();
        assert!(partials.contains_key("shared/_critical_header"));
        assert!(partials.contains_key("shared/_context_section"));
        assert!(partials.contains_key("shared/_diff_section"));
        assert!(partials.contains_key("shared/_output_checklist"));
        assert!(partials.contains_key("shared/_safety_no_execute"));
        assert!(partials.contains_key("shared/_unattended_mode"));
        assert!(partials.contains_key("shared/_developer_iteration_guidance"));
        assert!(partials.contains_key("shared/_no_git_commit"));
        assert!(partials.contains_key("shared/_session_capabilities"));
    }

    #[test]
    fn test_shared_partials_not_empty() {
        let partials = get_shared_partials();
        partials.iter().for_each(|(name, content)| {
            assert!(!content.is_empty(), "Partial '{name}' should not be empty");
        });
    }

    #[test]
    fn test_critical_header_contains_mode_variable() {
        let partials = get_shared_partials();
        let header = partials.get("shared/_critical_header").unwrap();
        assert!(header.contains("{{MODE}}"));
    }

    #[test]
    fn test_context_section_contains_variables() {
        let partials = get_shared_partials();
        let context = partials.get("shared/_context_section").unwrap();
        assert!(context.contains("{{PROMPT}}"));
        assert!(
            context.contains("{{PLAN"),
            "expected context section to contain PLAN placeholder (possibly with defaults)"
        );
    }

    #[test]
    fn test_diff_section_contains_diff_variable() {
        let partials = get_shared_partials();
        let diff_section = partials.get("shared/_diff_section").unwrap();
        assert!(diff_section.contains("{{DIFF}}"));
    }

    #[test]
    fn test_developer_iteration_guidance_partial_contains_task_sections() {
        let partials = get_shared_partials();
        let guidance = partials
            .get("shared/_developer_iteration_guidance")
            .expect("developer iteration guidance partial should exist");

        assert!(guidance.contains("YOUR TASK"));
        assert!(guidance.contains("VERIFICATION AND VALIDATION"));
        assert!(guidance.contains("EXPLORATION AND CONTEXT GATHERING"));
    }

    #[test]
    fn test_developer_iteration_guidance_partial_is_concise() {
        let partials = get_shared_partials();
        let guidance = partials
            .get("shared/_developer_iteration_guidance")
            .expect("developer iteration guidance partial should exist");

        let content_lines = guidance
            .lines()
            .filter(|line| !line.trim().is_empty() && !line.trim_start().starts_with("{#"))
            .count();

        assert!(
            content_lines <= 45,
            "developer iteration guidance should stay concise; got {content_lines} content lines"
        );
    }

    #[test]
    fn test_developer_iteration_guidance_emphasizes_total_completion() {
        let partials = get_shared_partials();
        let guidance = partials
            .get("shared/_developer_iteration_guidance")
            .expect("developer iteration guidance partial should exist");

        assert!(
            !guidance.to_lowercase().contains("remaining"),
            "guidance should avoid 'remaining' wording"
        );
        assert!(
            guidance.contains("Complete ALL") || guidance.contains("complete ALL"),
            "guidance should explicitly emphasize complete ALL work"
        );
        assert!(
            !guidance.contains("If you can finish"),
            "guidance should not include conditional completion language"
        );
    }

    #[test]
    fn test_no_git_commit_partial_blocks_mutating_git_commands() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("Do NOT run any git command")
                || no_git.contains("Do NOT run ANY git command"),
            "partial should block all git commands by default"
        );
        assert!(
            no_git.contains("read-only") && no_git.contains("lookup"),
            "partial should allow only read-only lookup git commands"
        );
        assert!(
            no_git.contains("`git status`") && no_git.contains("`git diff`"),
            "partial should explicitly allow git status and git diff for read-only inspection"
        );
    }

    #[test]
    fn test_no_git_commit_partial_forbids_hook_deletion() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("hooks") && no_git.to_lowercase().contains("never"),
            "partial should forbid hook deletion"
        );
        assert!(
            no_git.contains(".git/ralph/no_agent_commit"),
            "partial should forbid .git/ralph/no_agent_commit marker deletion"
        );
    }

    #[test]
    fn test_no_git_commit_partial_forbids_bypass_attempts() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("/usr/bin/git") || no_git.contains("absolute path"),
            "partial should forbid bypassing with absolute git path"
        );
        assert!(
            no_git.contains("--no-verify"),
            "partial should forbid --no-verify flag"
        );
    }

    #[test]
    fn test_no_git_commit_partial_lists_allowed_commands() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        let _ = &[
            "`git status`",
            "`git log`",
            "`git diff`",
            "`git show`",
            "`git branch`",
        ]
        .into_iter()
        .for_each(|cmd| {
            assert!(no_git.contains(cmd), "partial should list {cmd} as allowed");
        });
    }

    #[test]
    fn test_no_git_commit_partial_forbids_add_and_init() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("add") && no_git.contains("staging"),
            "partial should forbid git add"
        );
        assert!(no_git.contains("init"), "partial should forbid git init");
    }

    #[test]
    fn test_no_git_commit_partial_forbids_file_operations_on_hooks() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("rm") || no_git.contains("unlink"),
            "partial should forbid rm/unlink on hook files"
        );
        assert!(
            no_git.contains("exec") || no_git.contains("env"),
            "partial should forbid exec/env bypass of PATH wrapper"
        );
    }

    #[test]
    fn test_no_git_commit_partial_warns_about_hook_reinstallation() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("reinstall") || no_git.contains("recreated"),
            "partial should warn that hooks are reinstalled before every agent run"
        );
    }

    #[test]
    fn test_no_git_commit_partial_contains_futility_messaging() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("FUTILE") || no_git.contains("futile"),
            "partial should contain futility messaging to deter bypass attempts"
        );
        assert!(
            no_git.contains("execution budget"),
            "partial should warn about wasting execution budget on bypass attempts"
        );
    }

    #[test]
    fn test_no_git_commit_partial_forbids_command_builtin_bypass() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("command"),
            "partial should forbid `command` shell builtin bypass"
        );
    }

    #[test]
    fn test_no_git_commit_partial_forbids_env_var_bypass() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("GIT_DIR") && no_git.contains("GIT_EXEC_PATH"),
            "partial should forbid GIT_DIR and GIT_EXEC_PATH env var bypass"
        );
    }

    #[test]
    fn test_no_git_commit_partial_mentions_pre_merge_commit_hook() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("pre-merge-commit"),
            "partial should mention pre-merge-commit hook"
        );
    }

    #[test]
    fn test_no_git_commit_partial_prohibits_mcp_git_tools() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("mcp__git__git_commit"),
            "partial should explicitly prohibit mcp__git__git_commit"
        );
        assert!(
            no_git.contains("mcp__git__git_add"),
            "partial should explicitly prohibit mcp__git__git_add"
        );
        assert!(
            no_git.contains("mcp__git__git_push"),
            "partial should explicitly prohibit mcp__git__git_push"
        );
        assert!(
            no_git.contains("MCP") || no_git.contains("mcp"),
            "partial should mention MCP tools"
        );
    }

    #[test]
    fn test_no_git_commit_partial_mentions_head_oid_detection() {
        let partials = get_shared_partials();
        let no_git = partials
            .get("shared/_no_git_commit")
            .expect("no git commit partial should exist");

        assert!(
            no_git.contains("HEAD OID"),
            "partial should mention HEAD OID comparison for unauthorized commit detection"
        );
    }
}
