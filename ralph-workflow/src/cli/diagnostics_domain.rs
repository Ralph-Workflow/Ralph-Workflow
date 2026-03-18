//! Pure domain functions for diagnostic information.
//!
//! This module contains the policy logic extracted from boundary functions.
//! These functions are pure and testable without I/O.

use crate::agents::AgentDrain;

/// Git diagnostic information.
#[derive(Debug, Clone)]
pub struct GitDiagnostics {
    pub version: Option<String>,
    pub is_repo: bool,
    pub branch: Option<String>,
    pub uncommitted_changes: Option<usize>,
}

/// Plan which git commands to execute based on repository state.
#[derive(Debug, Clone)]
pub enum GitCommandPlan {
    /// Full diagnostics: version, repo check, branch, and uncommitted changes.
    Full,
    /// Partial diagnostics: version and repo check only.
    Partial,
    /// Version check only.
    VersionOnly,
    /// No commands needed (git not available).
    None,
}

/// Determine which git commands to run based on initial version check.
///
/// This is the policy decision: "should we run more git commands?"
pub fn plan_git_commands(version_available: bool) -> GitCommandPlan {
    if !version_available {
        return GitCommandPlan::None;
    }
    GitCommandPlan::Full
}

/// Determine if we should check for branch (requires repo check first).
pub fn should_check_branch(is_repo: bool) -> bool {
    is_repo
}

/// Determine if we should check for uncommitted changes (requires repo check first).
pub fn should_check_uncommitted(is_repo: bool) -> bool {
    is_repo
}

/// Build GitDiagnostics from command outputs.
pub fn build_git_diagnostics(
    version: Option<String>,
    is_repo: bool,
    branch: Option<String>,
    uncommitted_changes: Option<usize>,
) -> GitDiagnostics {
    GitDiagnostics {
        version,
        is_repo,
        branch,
        uncommitted_changes,
    }
}

/// Format git diagnostic information as lines.
pub fn format_git_info_lines(diagnostics: &GitDiagnostics) -> Vec<String> {
    let version_line = diagnostics
        .version
        .as_ref()
        .map(|v| format!("  Version: {v}"));

    let repo_line = Some(format!(
        "  In git repo: {}",
        if diagnostics.is_repo { "yes" } else { "no" }
    ));

    let branch_line = diagnostics
        .branch
        .as_ref()
        .map(|b| format!("  Current branch: {b}"));

    let changes_line = diagnostics
        .uncommitted_changes
        .map(|c| format!("  Uncommitted changes: {c}"));

    [version_line, repo_line, branch_line, changes_line]
        .into_iter()
        .flatten()
        .collect()
}

/// Config existence status.
#[derive(Debug, Clone)]
pub enum ConfigExistsStatus {
    Yes,
    No,
    Unknown(String),
}

/// Determine config file existence status.
pub fn determine_config_exists(
    config_path_is_absolute: bool,
    workspace_root: &dyn crate::workspace::Workspace,
    config_path: &std::path::Path,
) -> ConfigExistsStatus {
    if config_path_is_absolute {
        config_path
            .strip_prefix(workspace_root.root())
            .ok()
            .map_or_else(
                || ConfigExistsStatus::Unknown("unknown (outside workspace)".to_string()),
                |relative| {
                    if workspace_root.exists(relative) {
                        ConfigExistsStatus::Yes
                    } else {
                        ConfigExistsStatus::No
                    }
                },
            )
    } else if workspace_root.exists(config_path) {
        ConfigExistsStatus::Yes
    } else {
        ConfigExistsStatus::No
    }
}

/// PROMPT.md analysis result.
#[derive(Debug, Clone)]
pub struct PromptAnalysis {
    pub exists: bool,
    pub size_bytes: Option<usize>,
    pub line_count: Option<usize>,
    pub has_goal_section: bool,
    pub has_acceptance_section: bool,
}

/// Analyze PROMPT.md content for key sections.
pub fn analyze_prompt_content(content: &str) -> PromptAnalysis {
    let has_goal = content.contains("## Goal") || content.contains("# Goal");
    let has_acceptance =
        content.contains("## Acceptance") || content.contains("Acceptance Criteria");

    PromptAnalysis {
        exists: true,
        size_bytes: Some(content.len()),
        line_count: Some(content.lines().count()),
        has_goal_section: has_goal,
        has_acceptance_section: has_acceptance,
    }
}

/// Agent availability display info.
#[derive(Debug, Clone)]
pub struct AgentAvailabilityInfo {
    pub name: String,
    pub available: bool,
    pub json_parser: bool,
    pub command: String,
}

/// Get sorted list of agent availability info.
pub fn get_sorted_agent_availability(
    registry: &crate::agents::AgentRegistry,
) -> Vec<AgentAvailabilityInfo> {
    use itertools::Itertools;

    let all_agents = registry.list();
    let mut sorted: Vec<_> = all_agents
        .into_iter()
        .map(|(name, cfg)| AgentAvailabilityInfo {
            name: name.to_string(),
            available: registry.is_agent_available(&name),
            json_parser: !matches!(
                cfg.json_parser,
                crate::agents::parser::JsonParserType::Generic
            ),
            command: cfg.cmd,
        })
        .sorted_by(|a, b| a.name.cmp(&b.name))
        .collect();
    sorted
}

/// Agent drain display info.
#[derive(Debug, Clone)]
pub struct DrainBindingInfo {
    pub drain: AgentDrain,
    pub chain_name: String,
    pub agents: Vec<String>,
}

/// Get all drain bindings as display info.
pub fn get_drain_bindings(registry: &crate::agents::AgentRegistry) -> Vec<DrainBindingInfo> {
    let resolved = registry.resolved_drains();
    crate::agents::AgentDrain::all()
        .into_iter()
        .filter_map(|drain| {
            resolved.binding(drain).map(|binding| DrainBindingInfo {
                drain,
                chain_name: binding.chain_name.clone(),
                agents: binding.agents.clone(),
            })
        })
        .collect()
}
