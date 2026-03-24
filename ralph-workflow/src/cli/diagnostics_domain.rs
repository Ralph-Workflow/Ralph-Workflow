//! Pure domain functions for diagnostic information.
//!
//! This module contains the policy logic extracted from boundary functions.
//! These functions are pure and testable without I/O.

use crate::agents::{AgentDrain, AgentRegistry};
use crate::checkpoint::load_checkpoint_with_workspace;
use crate::config::Config;
use crate::guidelines::{CheckSeverity, ReviewGuidelines};
use crate::language_detector;
use crate::workspace::Workspace;

/// Git diagnostic information.
#[derive(Debug, Clone)]
pub(super) struct GitDiagnostics {
    pub version: Option<String>,
    pub is_repo: bool,
    pub branch: Option<String>,
    pub uncommitted_changes: Option<usize>,
}

/// Plan which git commands to execute based on repository state.
#[derive(Debug, Clone)]
enum GitCommandPlan {
    Full,
    None,
}

/// Determine which git commands to run based on initial version check.
///
/// This is the policy decision: "should we run more git commands?"
fn plan_git_commands(version_available: bool) -> GitCommandPlan {
    if !version_available {
        return GitCommandPlan::None;
    }
    GitCommandPlan::Full
}

/// Determine if we should check for branch (requires repo check first).
fn should_check_branch(is_repo: bool) -> bool {
    is_repo
}

/// Determine if we should check for uncommitted changes (requires repo check first).
fn should_check_uncommitted(is_repo: bool) -> bool {
    is_repo
}

/// Build GitDiagnostics from command outputs.
fn build_git_diagnostics(
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
pub(super) fn format_git_info_lines(diagnostics: &GitDiagnostics) -> Vec<String> {
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
enum ConfigExistsStatus {
    Yes,
    No,
    Unknown(String),
}

/// Determine config file existence status.
fn determine_config_exists(
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
struct PromptAnalysis {
    pub size_bytes: Option<usize>,
    pub line_count: Option<usize>,
    pub has_goal_section: bool,
    pub has_acceptance_section: bool,
}

/// Analyze PROMPT.md content for key sections.
fn analyze_prompt_content(content: &str) -> PromptAnalysis {
    let has_goal = content.contains("## Goal") || content.contains("# Goal");
    let has_acceptance =
        content.contains("## Acceptance") || content.contains("Acceptance Criteria");

    PromptAnalysis {
        size_bytes: Some(content.len()),
        line_count: Some(content.lines().count()),
        has_goal_section: has_goal,
        has_acceptance_section: has_acceptance,
    }
}

/// Agent availability display info.
#[derive(Debug, Clone)]
struct AgentAvailabilityInfo {
    pub name: String,
    pub available: bool,
    pub json_parser: bool,
    pub command: String,
}

/// Get sorted list of agent availability info.
fn get_sorted_agent_availability(
    registry: &crate::agents::AgentRegistry,
) -> Vec<AgentAvailabilityInfo> {
    use itertools::Itertools;

    let all_agents = registry.list();
    all_agents
        .into_iter()
        .map(|(name, cfg)| AgentAvailabilityInfo {
            name: name.to_string(),
            available: registry.is_agent_available(name),
            json_parser: !matches!(
                cfg.json_parser,
                crate::agents::parser::JsonParserType::Generic
            ),
            command: cfg.cmd.clone(),
        })
        .sorted_by(|a, b| a.name.cmp(&b.name))
        .collect()
}

/// Agent drain display info.
#[derive(Debug, Clone)]
pub(super) struct DrainBindingInfo {
    pub drain: AgentDrain,
    pub chain_name: String,
    pub agents: Vec<String>,
}

/// Get all drain bindings as display info.
pub(super) fn get_drain_bindings(registry: &crate::agents::AgentRegistry) -> Vec<DrainBindingInfo> {
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

/// Resolve the checkpoint log path or find the latest run log directory.
fn find_log_path(workspace: &dyn Workspace) -> Option<std::path::PathBuf> {
    let checkpoint = load_checkpoint_with_workspace(workspace).ok().flatten()?;

    if let Some(log_run_id) = checkpoint.log_run_id {
        return Some(std::path::PathBuf::from(format!(
            ".agent/logs-{log_run_id}/pipeline.log"
        )));
    }

    find_latest_run_log_path(workspace)
}

/// Find the latest run log path by lexicographic sort.
fn find_latest_run_log_path(workspace: &dyn Workspace) -> Option<std::path::PathBuf> {
    use itertools::Itertools;
    use std::path::Path;

    let agent_dir = Path::new(".agent");
    if !workspace.is_dir(agent_dir) {
        return None;
    }

    let entries = workspace.read_dir(agent_dir).ok()?;

    entries
        .into_iter()
        .filter(|entry| {
            entry
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|s| s.starts_with("logs-") && entry.is_dir())
        })
        .filter_map(|entry| {
            entry
                .file_name()
                .and_then(|n| n.to_str())
                .map(std::string::ToString::to_string)
        })
        .sorted()
        .last()
        .map(|dir_name| Path::new(".agent").join(dir_name).join("pipeline.log"))
}

/// Format recent log lines from content string (last 10 lines).
fn format_recent_log_lines(content: &str) -> Vec<String> {
    let lines: Vec<&str> = content.lines().collect();
    let start = lines.len().saturating_sub(10);
    lines[start..].iter().map(|l| format!("  {l}")).collect()
}

/// Format configuration info section lines.
pub(super) fn format_config_section_lines(
    config: &Config,
    config_path: &std::path::Path,
    config_sources: &[crate::agents::ConfigSource],
    workspace: &dyn Workspace,
) -> Vec<String> {
    let exists_status = determine_config_exists(config_path.is_absolute(), workspace, config_path);
    let exists_str: String = match exists_status {
        ConfigExistsStatus::Yes => "yes".to_string(),
        ConfigExistsStatus::No => "no".to_string(),
        ConfigExistsStatus::Unknown(s) => s,
    };

    let base_lines = [
        format!("  Unified config: {}", config_path.display()),
        format!("  Config exists: {exists_str}"),
        format!(
            "  Review depth: {:?} ({})",
            config.review_depth,
            config.review_depth.description()
        ),
    ];

    if config_sources.is_empty() {
        return base_lines.to_vec();
    }

    let source_lines: Vec<String> = std::iter::once("  Loaded sources:".to_string())
        .chain(config_sources.iter().map(|src| {
            format!(
                "    - {} ({} agents)",
                src.path.display(),
                src.agents_loaded
            )
        }))
        .collect();

    base_lines.into_iter().chain(source_lines).collect()
}

/// Format agent availability section lines.
pub(super) fn format_agent_availability_section(registry: &AgentRegistry) -> Vec<String> {
    let agents = get_sorted_agent_availability(registry);
    agents
        .into_iter()
        .map(|agent| {
            let status_icon = if agent.available { "✓" } else { "✗" };
            let command_name = agent
                .command
                .split_whitespace()
                .next()
                .unwrap_or(&agent.command);
            format!(
                "  {status_icon} {} (parser: {}, cmd: {})",
                agent.name, agent.json_parser, command_name
            )
        })
        .collect()
}

/// Format PROMPT.md status section lines.
pub(super) fn format_prompt_status_section(workspace: &dyn Workspace) -> Vec<String> {
    use std::path::Path;

    let prompt_path = Path::new("PROMPT.md");

    if !workspace.exists(prompt_path) {
        return vec!["  Exists: no".to_string()];
    }

    let Ok(content) = workspace.read(prompt_path) else {
        return vec!["  Exists: no".to_string()];
    };

    let analysis = analyze_prompt_content(&content);
    [
        Some("  Exists: yes".to_string()),
        Some(format!(
            "  Size: {} bytes",
            analysis.size_bytes.unwrap_or(0)
        )),
        Some(format!("  Lines: {}", analysis.line_count.unwrap_or(0))),
        Some(format!(
            "  Has Goal section: {}",
            if analysis.has_goal_section {
                "yes"
            } else {
                "no"
            }
        )),
        Some(format!(
            "  Has Acceptance section: {}",
            if analysis.has_acceptance_section {
                "yes"
            } else {
                "no"
            }
        )),
    ]
    .into_iter()
    .flatten()
    .collect()
}

/// Format project stack section lines.
pub(super) fn format_project_stack_section(workspace: &dyn Workspace) -> Vec<String> {
    let root = workspace.root();
    let stack = match language_detector::detect_stack(root) {
        Ok(s) => s,
        Err(e) => return vec![format!("  Detection failed: {e}")],
    };

    let secondary = (!stack.secondary_languages.is_empty())
        .then_some(vec![format!(
            "  Secondary languages: {:?}",
            stack.secondary_languages
        )])
        .unwrap_or_default();

    let frameworks = (!stack.frameworks.is_empty())
        .then_some(vec![format!("  Frameworks: {:?}", stack.frameworks)])
        .unwrap_or_default();

    let package_manager = stack
        .package_manager
        .as_ref()
        .map(|pm| vec![format!("  Package manager: {pm}")])
        .unwrap_or_default();

    let test_framework = stack
        .test_framework
        .as_ref()
        .map(|tf| vec![format!("  Test framework: {tf}")])
        .unwrap_or_default();

    let language_types: Vec<&str> = [
        if stack.is_rust() { Some("Rust") } else { None },
        if stack.is_python() {
            Some("Python")
        } else {
            None
        },
        if stack.is_javascript_or_typescript() {
            Some("JS/TS")
        } else {
            None
        },
        if stack.is_go() { Some("Go") } else { None },
    ]
    .into_iter()
    .flatten()
    .collect();
    let language_flags = (!language_types.is_empty())
        .then_some(vec![format!(
            "  Language flags: {}",
            language_types.join(", ")
        )])
        .unwrap_or_default();

    let guidelines = ReviewGuidelines::for_stack(&stack);

    let all_checks = guidelines.get_all_checks();
    let critical_count = all_checks
        .iter()
        .filter(|c| matches!(c.severity, CheckSeverity::Critical))
        .count();
    let high_count = all_checks
        .iter()
        .filter(|c| matches!(c.severity, CheckSeverity::High))
        .count();

    let severity_line = (critical_count > 0 || high_count > 0)
        .then_some(vec![format!(
            "  Check severities: {critical_count} critical, {high_count} high"
        )])
        .unwrap_or_default();

    let critical_checks_lines: Vec<String> = all_checks
        .iter()
        .filter(|c| matches!(c.severity, CheckSeverity::Critical))
        .take(3)
        .map(|check| format!("    - {}", check.check))
        .collect();

    let checks_section = if critical_checks_lines.is_empty() {
        vec![]
    } else {
        std::iter::once("  Critical checks (sample):".to_string())
            .chain(critical_checks_lines)
            .collect()
    };

    [
        vec![format!("  Primary language: {}", stack.primary_language)],
        secondary,
        frameworks,
        package_manager,
        test_framework,
        language_flags,
        vec![format!(
            "  Review checks: {} total",
            guidelines.total_checks()
        )],
        severity_line,
        checks_section,
    ]
    .into_iter()
    .flatten()
    .collect()
}

/// Determine if template selection should use the default.
fn should_use_default_template(input: &str) -> bool {
    input.trim().is_empty()
}

/// Resolve the template name from user input.
fn resolve_template_name(input: &str) -> &str {
    if should_use_default_template(input) {
        "feature-spec"
    } else {
        input.trim()
    }
}

/// Result of template validation.
#[derive(Debug, Clone)]
pub(super) enum TemplateValidation {
    Valid,
    Unknown,
}

pub(super) fn validate_template_name(template_name: &str) -> TemplateValidation {
    use crate::templates::get_template;

    if get_template(template_name).is_some() {
        TemplateValidation::Valid
    } else {
        TemplateValidation::Unknown
    }
}

/// Determine if user declined the template selection.
fn did_user_decline_template(response: &str) -> bool {
    let response = response.trim().to_lowercase();
    response == "n" || response == "no" || response == "skip"
}

/// Init action based on file existence state.
#[derive(Debug, Clone, Copy)]
pub(super) enum InitFileState {
    BothExist,
    ConfigOnly,
    PromptOnly,
    NeitherExists,
}

/// Determine the init action based on config and prompt file existence.
pub(super) fn determine_init_action(
    config_exists: bool,
    prompt_exists: bool,
    _template_arg: Option<&str>,
) -> InitFileState {
    if config_exists && prompt_exists {
        InitFileState::BothExist
    } else if config_exists {
        InitFileState::ConfigOnly
    } else if prompt_exists {
        InitFileState::PromptOnly
    } else {
        InitFileState::NeitherExists
    }
}

/// Action to take for init when config exists but prompt doesn't.
#[derive(Debug, Clone)]
enum ConfigOnlyAction {
    CreateFromTemplate(String),
    CreateMinimal,
    Skip,
}

/// Decide the action when config exists but prompt doesn't.
fn decide_config_only_action(can_prompt: bool, template_name: Option<String>) -> ConfigOnlyAction {
    if can_prompt {
        if let Some(name) = template_name {
            return ConfigOnlyAction::CreateFromTemplate(name);
        }
        ConfigOnlyAction::Skip
    } else {
        ConfigOnlyAction::CreateMinimal
    }
}

/// Action to take for init when neither config nor prompt exists.
#[derive(Debug, Clone)]
enum NeitherExistsAction {
    CreateFromTemplate(String),
    CreateMinimal,
    Skip,
}

/// Decide the action when neither config nor prompt exists.
fn decide_neither_exists_action(
    can_prompt: bool,
    template_name: Option<String>,
) -> NeitherExistsAction {
    if can_prompt {
        if let Some(name) = template_name {
            return NeitherExistsAction::CreateFromTemplate(name);
        }
        NeitherExistsAction::Skip
    } else {
        NeitherExistsAction::CreateMinimal
    }
}

/// Result of git version command execution.
struct GitVersionResult {
    pub version: Option<String>,
    pub available: bool,
}

/// Execute git version command and extract version string.
fn get_git_version_result(
    executor_output: Option<crate::executor::ProcessOutput>,
) -> GitVersionResult {
    let version = executor_output.map(|o| o.stdout.trim().to_string());
    GitVersionResult {
        available: version.is_some(),
        version,
    }
}

/// Raw git execution results for domain processing.
pub(super) struct GitRawResults {
    pub version_output: Option<crate::executor::ProcessOutput>,
    pub rev_parse_output: Option<crate::executor::ProcessOutput>,
    pub branch_output: Option<crate::executor::ProcessOutput>,
    pub status_output: Option<crate::executor::ProcessOutput>,
}

/// Determine if template selection prompt should be offered.
pub(super) fn should_offer_template_prompt(is_terminal: bool) -> bool {
    is_terminal
}

#[derive(Debug)]
pub(super) enum TemplatePromptResponseDecision {
    Declined,
    Selected,
}

pub(super) fn evaluate_template_creation_response(
    response: &str,
) -> TemplatePromptResponseDecision {
    if did_user_decline_template(response) {
        TemplatePromptResponseDecision::Declined
    } else {
        TemplatePromptResponseDecision::Selected
    }
}

/// Resolve the selected template, returning the final template to use.
#[derive(Debug)]
pub(super) enum TemplateSelectionOutcome {
    Selected(String),
    UseDefault { default: String },
}

/// Resolve selected template from user input, handling unknown templates.
pub(super) fn resolve_selected_template(
    input: &str,
    templates: &[(&str, &str)],
) -> TemplateSelectionOutcome {
    let resolved = resolve_template_name(input);
    let template_exists = templates.iter().any(|(name, _)| *name == resolved);

    if template_exists {
        TemplateSelectionOutcome::Selected(resolved.to_string())
    } else {
        TemplateSelectionOutcome::UseDefault {
            default: "feature-spec".to_string(),
        }
    }
}

/// Result of create prompt from template operation.
#[derive(Debug)]
pub(super) enum CreatePromptResult {
    SkippedBecauseExists,
    Created,
    UnknownTemplateError,
}

/// Determine result of trying to create prompt from template.
pub(super) fn determine_create_prompt_result(
    validation: &TemplateValidation,
    prompt_exists: bool,
) -> CreatePromptResult {
    if matches!(validation, TemplateValidation::Unknown) {
        return CreatePromptResult::UnknownTemplateError;
    }
    if prompt_exists {
        return CreatePromptResult::SkippedBecauseExists;
    }
    CreatePromptResult::Created
}

/// Compute log section content from workspace state.
#[derive(Debug)]
pub(super) enum ComputeLogSection {
    NotFound,
    Empty,
    Content(Vec<String>),
}

/// Compute what the log section should show.
pub(super) fn compute_log_section(workspace: &dyn Workspace) -> ComputeLogSection {
    let log_path = match find_log_path(workspace) {
        Some(p) => p,
        None => return ComputeLogSection::NotFound,
    };

    if !workspace.exists(&log_path) {
        return ComputeLogSection::NotFound;
    }

    let content = match workspace.read(&log_path) {
        Ok(c) => c,
        Err(_) => return ComputeLogSection::Empty,
    };

    let lines = format_recent_log_lines(&content);
    if lines.is_empty() {
        ComputeLogSection::Empty
    } else {
        ComputeLogSection::Content(lines)
    }
}

/// Action for config_only init flow.
#[derive(Debug)]
pub(super) enum ConfigOnlyNextAction {
    CreateFromTemplate(String),
    CreateMinimal,
    Skip,
}

/// Determine next action for config-only flow.
pub(super) fn determine_config_only_next_action(
    can_prompt: bool,
    template_name: Option<String>,
) -> ConfigOnlyNextAction {
    match decide_config_only_action(can_prompt, template_name) {
        ConfigOnlyAction::CreateFromTemplate(name) => {
            ConfigOnlyNextAction::CreateFromTemplate(name)
        }
        ConfigOnlyAction::CreateMinimal => ConfigOnlyNextAction::CreateMinimal,
        ConfigOnlyAction::Skip => ConfigOnlyNextAction::Skip,
    }
}

/// Action for neither_exists init flow.
#[derive(Debug)]
pub(super) enum NeitherExistsNextAction {
    CreateFromTemplate(String),
    CreateMinimal,
    Skip,
}

/// Determine next action for neither-exists flow.
pub(super) fn determine_neither_exists_next_action(
    can_prompt: bool,
    template_name: Option<String>,
) -> NeitherExistsNextAction {
    match decide_neither_exists_action(can_prompt, template_name) {
        NeitherExistsAction::CreateFromTemplate(name) => {
            NeitherExistsNextAction::CreateFromTemplate(name)
        }
        NeitherExistsAction::CreateMinimal => NeitherExistsNextAction::CreateMinimal,
        NeitherExistsAction::Skip => NeitherExistsNextAction::Skip,
    }
}

pub(super) fn compute_git_diagnostics_from_raw_results(
    results: GitRawResults,
    is_repo: bool,
) -> GitDiagnostics {
    let version_result = get_git_version_result(results.version_output);
    let plan = plan_git_commands(version_result.available);

    match plan {
        GitCommandPlan::None => GitDiagnostics {
            version: None,
            is_repo: false,
            branch: None,
            uncommitted_changes: None,
        },
        GitCommandPlan::Full => {
            let branch = results
                .branch_output
                .filter(|_| should_check_branch(is_repo))
                .map(|o| o.stdout.trim().to_string());

            let uncommitted_changes = results
                .status_output
                .filter(|_| should_check_uncommitted(is_repo))
                .map(|o| o.stdout.lines().count());

            build_git_diagnostics(version_result.version, is_repo, branch, uncommitted_changes)
        }
    }
}
