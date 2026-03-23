//! Rebase conflict resolution prompts.
//!
//! This module provides prompts for AI agents to resolve merge conflicts
//! that occur during rebase operations.
//!
//! # Design Note
//!
//! Per project requirements, AI agents should NOT know that we are in the
//! middle of a rebase. The prompt frames conflicts as "merge conflicts between
//! two versions" without mentioning rebase or rebasing.

#![deny(unsafe_code)]

use crate::prompts::template_context::TemplateContext;
use crate::prompts::template_engine::Template;
use crate::workspace::Workspace;
use std::collections::HashMap;
use std::path::Path;

/// Structure representing a single file conflict.
#[derive(Debug, Clone)]
pub struct FileConflict {
    /// The conflict marker content from the file
    pub conflict_content: String,
    /// The current file content with conflict markers
    pub current_content: String,
}

/// Build a conflict resolution prompt for the AI agent.
///
/// This function generates a prompt that instructs the AI agent to resolve
/// merge conflicts. The prompt does NOT mention "rebase" - it frames the
/// task as resolving merge conflicts between two versions.
///
/// # Arguments
///
/// * `conflicts` - Map of file paths to their conflict information
/// * `prompt_md_content` - Optional content from PROMPT.md for task context
/// * `plan_content` - Optional content from PLAN.md for additional context
///
/// # Returns
///
/// Returns a formatted prompt string for the AI agent.
#[cfg(test)]
#[expect(clippy::print_stderr, reason = "test-only error logging")]
pub fn build_conflict_resolution_prompt(
    conflicts: &HashMap<String, FileConflict>,
    prompt_md_content: Option<&str>,
    plan_content: Option<&str>,
) -> String {
    let template_content = include_str!("templates/conflict_resolution.txt");
    let template = Template::new(template_content);

    let context = format_context_section(prompt_md_content, plan_content);
    let conflicts_section = format_conflicts_section(conflicts);

    let variables = HashMap::from([
        ("CONTEXT", context),
        ("CONFLICTS", conflicts_section.clone()),
    ]);

    template.render(&variables).unwrap_or_else(|e| {
        eprintln!("Warning: Failed to render conflict resolution template: {e}");
        let fallback_template_content = include_str!("templates/conflict_resolution_fallback.txt");
        let fallback_template = Template::new(fallback_template_content);
        fallback_template.render(&variables).unwrap_or_else(|e| {
            eprintln!("Critical: Failed to render fallback template: {e}");
            format!(
                "# MERGE CONFLICT RESOLUTION\n\nResolve these conflicts:\n\n{}",
                &conflicts_section
            )
        })
    })
}

/// Build a conflict resolution prompt using template registry.
///
/// This version uses the template registry which supports user template overrides.
/// It's the recommended way to generate prompts going forward.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `conflicts` - Map of file paths to their conflict information
/// * `prompt_md_content` - Optional content from PROMPT.md for task context
/// * `plan_content` - Optional content from PLAN.md for additional context
#[must_use]
#[expect(
    clippy::print_stderr,
    reason = "error logging for template rendering failures"
)]
pub fn build_conflict_resolution_prompt_with_context<S: std::hash::BuildHasher>(
    context: &TemplateContext,
    conflicts: &HashMap<String, FileConflict, S>,
    prompt_md_content: Option<&str>,
    plan_content: Option<&str>,
) -> String {
    let template_content = context
        .registry()
        .get_template("conflict_resolution")
        .unwrap_or_else(|_| include_str!("templates/conflict_resolution.txt").to_string());
    let template = Template::new(&template_content);

    let ctx_section = format_context_section(prompt_md_content, plan_content);
    let conflicts_section = format_conflicts_section(conflicts);

    let variables = HashMap::from([
        ("CONTEXT", ctx_section),
        ("CONFLICTS", conflicts_section.clone()),
    ]);

    template.render(&variables).unwrap_or_else(|e| {
        eprintln!("Warning: Failed to render conflict resolution template: {e}");
        // Use fallback template
        let fallback_template_content = context
            .registry()
            .get_template("conflict_resolution_fallback")
            .unwrap_or_else(|_| {
                include_str!("templates/conflict_resolution_fallback.txt").to_string()
            });
        let fallback_template = Template::new(&fallback_template_content);
        fallback_template.render(&variables).unwrap_or_else(|e| {
            eprintln!("Critical: Failed to render fallback template: {e}");
            // Last resort: minimal emergency prompt - conflicts_section is captured from closure
            format!(
                "# MERGE CONFLICT RESOLUTION\n\nResolve these conflicts:\n\n{}",
                &conflicts_section
            )
        })
    })
}

/// Format the context section with PROMPT.md and PLAN.md content.
///
/// This helper builds the context section that gets injected into the
/// {{CONTEXT}} template variable.
fn format_context_section(prompt_md_content: Option<&str>, plan_content: Option<&str>) -> String {
    let prompt_part = prompt_md_content.map(|prompt_md| {
        format!(
            "## Task Context\n\nThe user was working on the following task:\n\n```\n{}\n```\n\n",
            prompt_md
        )
    });

    let plan_part = plan_content.map(|plan| {
        format!(
            "## Implementation Plan\n\nThe following plan was being implemented:\n\n```\n{}\n```\n\n",
            plan
        )
    });

    [prompt_part, plan_part]
        .into_iter()
        .flatten()
        .collect::<String>()
}

/// Format the conflicts section for all conflicted files.
///
/// This helper builds the conflicts section that gets injected into the
/// {{CONFLICTS}} template variable.
fn format_conflicts_section<S: std::hash::BuildHasher>(
    conflicts: &HashMap<String, FileConflict, S>,
) -> String {
    let sections: Vec<String> = conflicts
        .iter()
        .map(|(path, conflict)| {
            let header = format!("### {path}\n\n");
            let current = format!(
                "Current state (with conflict markers):\n\n```{}\n{}\n```\n\n",
                get_language_marker(path),
                conflict.current_content
            );
            let conflict_part = if conflict.conflict_content.is_empty() {
                String::new()
            } else {
                format!(
                    "Conflict sections:\n\n```{}\n{}\n```\n\n",
                    get_language_marker(path),
                    conflict.conflict_content
                )
            };
            [header, current, conflict_part].join("")
        })
        .collect();

    sections.join("")
}

/// Get a language marker for syntax highlighting based on file extension.
fn get_language_marker(path: &str) -> String {
    let ext = Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("");

    match ext {
        "rs" => "rust",
        "py" => "python",
        "js" | "jsx" => "javascript",
        "ts" | "tsx" => "typescript",
        "go" => "go",
        "java" => "java",
        "c" => "c",
        "cpp" | "cc" | "cxx" => "cpp",
        "h" | "hpp" => "cpp",
        "cs" => "csharp",
        "rb" => "ruby",
        "php" => "php",
        "swift" => "swift",
        "kt" | "kts" => "kotlin",
        "scala" => "scala",
        "sh" | "bash" | "zsh" => "bash",
        "yml" | "yaml" => "yaml",
        "json" => "json",
        "toml" => "toml",
        "xml" => "xml",
        "html" | "htm" => "html",
        "css" => "css",
        "scss" | "sass" => "scss",
        "sql" => "sql",
        "md" | "markdown" => "markdown",
        _ => "",
    }
    .to_string()
}

/// Branch information for enhanced context.
#[derive(Debug, Clone)]
pub struct BranchInfo {
    /// Current branch name
    pub current_branch: String,
    /// Upstream/target branch name
    pub upstream_branch: String,
    /// Recent commits on current branch
    pub current_commits: Vec<String>,
    /// Recent commits on upstream branch
    pub upstream_commits: Vec<String>,
    /// Number of diverging commits
    pub diverging_count: usize,
}

/// Build an enhanced conflict resolution prompt with branch information.
///
/// This version includes additional context about the branches involved
/// in the conflict for more informed resolution.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `conflicts` - Map of file paths to their conflict information
/// * `branch_info` - Optional branch information for enhanced context
/// * `prompt_md_content` - Optional content from PROMPT.md for task context
/// * `plan_content` - Optional content from PLAN.md for additional context
#[must_use]
#[expect(
    clippy::print_stderr,
    reason = "error logging for template rendering failures"
)]
pub fn build_enhanced_conflict_resolution_prompt<S: std::hash::BuildHasher>(
    context: &TemplateContext,
    conflicts: &HashMap<String, FileConflict, S>,
    branch_info: Option<&BranchInfo>,
    prompt_md_content: Option<&str>,
    plan_content: Option<&str>,
) -> String {
    let template_content = context
        .registry()
        .get_template("conflict_resolution")
        .unwrap_or_else(|_| include_str!("templates/conflict_resolution.txt").to_string());
    let template = Template::new(&template_content);

    let ctx_section = match branch_info {
        Some(info) => {
            format_context_section(prompt_md_content, plan_content)
                + &format_branch_info_section(info)
        }
        None => format_context_section(prompt_md_content, plan_content),
    };

    let conflicts_section = format_conflicts_section(conflicts);

    let variables = HashMap::from([
        ("CONTEXT", ctx_section),
        ("CONFLICTS", conflicts_section.clone()),
    ]);

    template.render(&variables).unwrap_or_else(|e| {
        eprintln!("Warning: Failed to render conflict resolution template: {e}");
        // Use fallback template
        let fallback_template_content = context
            .registry()
            .get_template("conflict_resolution_fallback")
            .unwrap_or_else(|_| {
                include_str!("templates/conflict_resolution_fallback.txt").to_string()
            });
        let fallback_template = Template::new(&fallback_template_content);
        fallback_template.render(&variables).unwrap_or_else(|e| {
            eprintln!("Critical: Failed to render fallback template: {e}");
            // Last resort: minimal emergency prompt - conflicts_section is captured from closure
            format!(
                "# MERGE CONFLICT RESOLUTION\n\nResolve these conflicts:\n\n{}",
                &conflicts_section
            )
        })
    })
}

/// Format branch information for context section.
///
/// This helper builds a branch information section that gets injected
/// into the context for AI conflict resolution.
fn format_branch_info_section(info: &BranchInfo) -> String {
    let header = format!(
        "## Branch Information\n\n- **Current branch**: `{}`\n- **Target branch**: `{}`\n- **Diverging commits**: {}\n\n",
        info.current_branch, info.upstream_branch, info.diverging_count
    );

    let current_commits_section = if info.current_commits.is_empty() {
        String::new()
    } else {
        let commits: Vec<String> = info
            .current_commits
            .iter()
            .take(5)
            .enumerate()
            .map(|(i, msg)| format!("{}. {}", i + 1, msg))
            .collect();
        format!(
            "### Recent commits on current branch:\n\n{}\n\n",
            commits.join("\n")
        )
    };

    let upstream_commits_section = if info.upstream_commits.is_empty() {
        String::new()
    } else {
        let commits: Vec<String> = info
            .upstream_commits
            .iter()
            .take(5)
            .enumerate()
            .map(|(i, msg)| format!("{}. {}", i + 1, msg))
            .collect();
        format!(
            "### Recent commits on target branch:\n\n{}\n\n",
            commits.join("\n")
        )
    };

    [header, current_commits_section, upstream_commits_section]
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect()
}

/// Collect branch information for conflict resolution.
///
/// Queries git to gather information about the branches involved in the conflict.
///
/// # Arguments
///
/// * `upstream_branch` - The name of the upstream/target branch
/// * `executor` - Process executor for external process execution
///
/// # Returns
///
/// Returns `Ok(BranchInfo)` with the gathered information, or an error if git operations fail.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn collect_branch_info(
    upstream_branch: &str,
    executor: &dyn crate::executor::ProcessExecutor,
) -> std::io::Result<BranchInfo> {
    // Get current branch name
    let current_branch =
        executor.execute("git", &["rev-parse", "--abbrev-ref", "HEAD"], &[], None)?;

    let current_branch = current_branch.stdout.trim().to_string();

    // Get recent commits from current branch
    let current_log = executor.execute("git", &["log", "--oneline", "-10", "HEAD"], &[], None)?;

    let current_commits: Vec<String> = current_log
        .stdout
        .lines()
        .map(std::string::ToString::to_string)
        .collect();

    // Get recent commits from upstream branch
    let upstream_log = executor.execute(
        "git",
        &["log", "--oneline", "-10", upstream_branch],
        &[],
        None,
    )?;

    let upstream_commits: Vec<String> = upstream_log
        .stdout
        .lines()
        .map(std::string::ToString::to_string)
        .collect();

    // Count diverging commits
    let diverging = executor.execute(
        "git",
        &[
            "rev-list",
            "--count",
            "--left-right",
            &format!("HEAD...{upstream_branch}"),
        ],
        &[],
        None,
    )?;

    let diverging_count = diverging
        .stdout
        .split_whitespace()
        .map(|s| s.parse::<usize>().unwrap_or(0))
        .sum::<usize>();

    Ok(BranchInfo {
        current_branch,
        upstream_branch: upstream_branch.to_string(),
        current_commits,
        upstream_commits,
        diverging_count,
    })
}

/// Collect conflict information from all conflicted files.
///
/// This function reads all conflicted files and builds a map of
/// file paths to their conflict information.
///
/// # Arguments
///
/// * `conflicted_paths` - List of paths to conflicted files
///
/// # Returns
///
/// Returns `Ok(HashMap)` mapping file paths to conflict information,
/// or an error if a file cannot be read.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn collect_conflict_info_with_workspace(
    workspace: &dyn Workspace,
    conflicted_paths: &[String],
) -> std::io::Result<HashMap<String, FileConflict>> {
    let conflicts: std::io::Result<Vec<(String, FileConflict)>> = conflicted_paths
        .iter()
        .map(|path| {
            let current_content = workspace.read(Path::new(path))?;
            let conflict_content = extract_conflict_sections_from_content(&current_content);
            Ok((
                path.clone(),
                FileConflict {
                    conflict_content,
                    current_content,
                },
            ))
        })
        .collect();

    let result: HashMap<String, FileConflict> = conflicts?.into_iter().collect();

    Ok(result)
}

fn extract_conflict_sections_from_content(content: &str) -> String {
    let lines: Vec<&str> = content.lines().collect();

    // Find all conflict markers and extract sections between them
    let conflict_sections: Vec<String> = lines
        .iter()
        .enumerate()
        .filter(|(_, line)| line.trim_start().starts_with("<<<<<<<"))
        .filter_map(|(start_idx, _)| {
            // Find the ======= line
            let equals_idx = lines
                .get(start_idx + 1..)?
                .iter()
                .position(|line| line.trim_start().starts_with("======="))
                .map(|i| start_idx + 1 + i);

            // Find the >>>>>>> line after =======
            let end_idx = equals_idx.and_then(|eq_idx| {
                lines
                    .get(eq_idx + 1..)?
                    .iter()
                    .position(|line| line.trim_start().starts_with(">>>>>>>"))
                    .map(|i| eq_idx + 1 + i)
            });

            // Extract the full conflict section
            let end = end_idx.unwrap_or(lines.len() - 1) + 1;
            Some(lines.get(start_idx..end)?.join("\n"))
        })
        .collect();

    if conflict_sections.is_empty() {
        String::new()
    } else {
        conflict_sections.join("\n\n")
    }
}

#[cfg(test)]
mod tests;

#[cfg(test)]
mod io_tests;
