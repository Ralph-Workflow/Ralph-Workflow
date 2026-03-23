//! CLI boundary module for I/O operations.
//!
//! This module contains CLI handlers that perform console I/O.
//! According to the Boundary-First Architecture pattern, all I/O
//! operations (including console output) should live in boundary modules.
//!
//! See `docs/plans/2026-03-16-functional-rust-refactoring-plan.md` for details.

use std::fs;
use std::io::{self, IsTerminal, Write};
use std::path::Path;

use crate::agents::{AgentRegistry, ConfigSource};
use crate::checkpoint::load_checkpoint_with_workspace;
use crate::cli::diagnostics_domain::{self, GitDiagnostics};
use crate::config::Config;
use crate::diagnostics::run_diagnostics;
use crate::executor::ProcessExecutor;
use crate::logger::Colors;
use crate::templates::{get_template, list_templates};
use crate::workspace::Workspace;

// =============================================================================
// IO module
// =============================================================================

pub fn create_dir_all(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)
}

pub fn write(path: &Path, contents: &str) -> io::Result<()> {
    fs::write(path, contents)
}

pub fn exists(path: &Path) -> bool {
    path.exists()
}

// =============================================================================
// Terminal module
// =============================================================================

pub fn is_terminal() -> bool {
    io::stdin().is_terminal() && io::stdout().is_terminal()
}

pub fn stdout_is_terminal() -> bool {
    io::stdout().is_terminal()
}

pub fn stderr_is_terminal() -> bool {
    io::stderr().is_terminal()
}

pub fn stdout() -> io::Stdout {
    io::stdout()
}

pub fn stderr() -> io::Stderr {
    io::stderr()
}

pub fn flush_stdout() -> std::io::Result<()> {
    io::stdout().flush()
}

pub fn read_line() -> Option<String> {
    io::stdin().lines().next().and_then(|r| r.ok())
}

pub fn exit_with_code(code: i32) -> ! {
    std::process::exit(code)
}

// =============================================================================
// Template Selection module
// =============================================================================

pub type TemplateSelectionResult = Option<String>;

fn display_template_list(colors: Colors, templates: &[(&str, &str)]) {
    let mut stdout = stdout();
    templates.iter().for_each(|(name, description)| {
        let _ = writeln!(
            stdout,
            "  {}{}{}  {}{}{}",
            colors.cyan(),
            name,
            colors.reset(),
            colors.dim(),
            description,
            colors.reset()
        );
    });
}

fn prompt_for_template_name(colors: Colors) -> Option<String> {
    let mut stdout = stdout();
    let _ = writeln!(stdout);
    let _ = writeln!(stdout, "Available templates:");

    let templates = list_templates();
    display_template_list(colors, &templates);

    let _ = writeln!(stdout);
    let _ = write!(
        stdout,
        "Select template {}[default: feature-spec]{}: ",
        colors.dim(),
        colors.reset()
    );
    if flush_stdout().is_err() {
        return None;
    }

    let template_input = read_line();
    let binding = template_input.unwrap_or_default();
    let template_name = binding.trim();

    Some(template_name.to_string())
}

fn prompt_for_template_creation_consent(colors: Colors) -> Option<String> {
    let mut stdout = stdout();
    let _ = writeln!(stdout);
    let _ = writeln!(
        stdout,
        "{}PROMPT.md not found.{}",
        colors.yellow(),
        colors.reset()
    );
    let _ = writeln!(stdout);
    let _ = writeln!(
        stdout,
        "PROMPT.md contains your task specification for the AI agents."
    );
    let _ = write!(
        stdout,
        "Would you like to create one from a template? [Y/n]: "
    );
    if flush_stdout().is_err() {
        return None;
    }
    Some(read_line().unwrap_or_default())
}

fn resolve_template_selection(template_input: &str, colors: Colors) -> TemplateSelectionResult {
    let templates = list_templates();
    match diagnostics_domain::resolve_selected_template(template_input, &templates) {
        diagnostics_domain::TemplateSelectionOutcome::Selected(selected) => Some(selected),
        diagnostics_domain::TemplateSelectionOutcome::UseDefault { default } => {
            let mut stdout = stdout();
            let _ = writeln!(
                stdout,
                "{}Unknown template. Using {} as default.{}",
                colors.yellow(),
                default,
                colors.reset()
            );
            Some(default)
        }
    }
}

#[must_use]
pub fn prompt_template_selection(colors: Colors) -> TemplateSelectionResult {
    if !diagnostics_domain::should_offer_template_prompt(is_terminal()) {
        return None;
    }

    let response = prompt_for_template_creation_consent(colors)?;

    match diagnostics_domain::evaluate_template_creation_response(&response) {
        diagnostics_domain::TemplatePromptResponseDecision::Declined => None,
        diagnostics_domain::TemplatePromptResponseDecision::Selected => {
            let template_input = prompt_for_template_name(colors)?;
            resolve_template_selection(&template_input, colors)
        }
    }
}

fn write_created_prompt_message(template_name: &str, colors: Colors) -> anyhow::Result<()> {
    let Some(template) = get_template(template_name) else {
        return Err(anyhow::anyhow!("Template '{template_name}' not found"));
    };
    let prompt_path = Path::new("PROMPT.md");
    write(prompt_path, template.content())?;

    let mut stdout = stdout();
    let _ = writeln!(stdout);
    let _ = writeln!(
        stdout,
        "{}Created PROMPT.md from template: {}{}{}",
        colors.green(),
        colors.bold(),
        template_name,
        colors.reset()
    );
    let _ = writeln!(stdout);
    let _ = writeln!(
        stdout,
        "Template: {}{}{}  {}",
        colors.cyan(),
        template.name(),
        colors.reset(),
        template.description()
    );
    let _ = writeln!(stdout);
    let _ = writeln!(stdout, "Next steps:");
    let _ = writeln!(stdout, " 1. Edit PROMPT.md with your task details");
    let _ = writeln!(stdout, " 2. Run ralph again with your commit message");
    Ok(())
}

pub fn create_prompt_from_template(template_name: &str, colors: Colors) -> anyhow::Result<()> {
    let prompt_path = Path::new("PROMPT.md");
    let validation = diagnostics_domain::validate_template_name(template_name);
    let prompt_exists = exists(prompt_path);

    match diagnostics_domain::determine_create_prompt_result(&validation, prompt_exists) {
        diagnostics_domain::CreatePromptResult::UnknownTemplateError => {
            let mut stdout = stdout();
            let _ = writeln!(
                stdout,
                "{}Unknown template: '{}'. Using feature-spec as default.{}",
                colors.yellow(),
                template_name,
                colors.reset()
            );
            create_prompt_from_template("feature-spec", colors)
        }
        diagnostics_domain::CreatePromptResult::SkippedBecauseExists => {
            let mut stdout = stdout();
            let _ = writeln!(
                stdout,
                "{}PROMPT.md already exists. Skipping creation.{}",
                colors.yellow(),
                colors.reset()
            );
            Ok(())
        }
        diagnostics_domain::CreatePromptResult::Created => {
            write_created_prompt_message(template_name, colors)
        }
    }
}

// =============================================================================
// Diagnose module
// =============================================================================

pub struct ConfigInfo<'a> {
    pub path: &'a Path,
    pub sources: &'a [ConfigSource],
}

pub fn handle_diagnose<W: Write>(
    mut writer: W,
    colors: Colors,
    config: &Config,
    registry: &AgentRegistry,
    config_info: ConfigInfo<'_>,
    executor: &dyn ProcessExecutor,
    workspace: &dyn Workspace,
) {
    let config_path = config_info.path;
    let config_sources = config_info.sources;
    let report = run_diagnostics(registry);

    let _ = write!(
        writer,
        "{}=== Ralph Diagnostic Report ==={}\\n\\n",
        colors.bold(),
        colors.reset()
    );

    write_system_info(&mut writer, colors);
    write_git_info(&mut writer, colors, &collect_git_info(executor));
    write_config_info(
        &mut writer,
        colors,
        config,
        config_path,
        config_sources,
        workspace,
    );
    write_agent_chain_info(&mut writer, colors, registry);
    write_agent_availability(&mut writer, colors, registry);
    write_prompt_status(&mut writer, colors, workspace);
    write_checkpoint_status(&mut writer, colors, workspace);
    write_project_stack(&mut writer, colors, workspace);
    write_recent_logs(&mut writer, colors, workspace);

    let _ = report.agents.total_agents;
    let _ = report.agents.available_agents;
    let _ = report.agents.unavailable_agents;
    report.agents.agent_status.iter().for_each(|status| {
        let _ = (
            &status.name,
            &status.display_name,
            status.available,
            &status.json_parser,
            &status.command,
        );
    });
    let _ = (
        &report.system.os,
        &report.system.arch,
        &report.system.working_directory,
        &report.system.shell,
        &report.system.git_version,
        report.system.git_repo,
        &report.system.git_branch,
        &report.system.uncommitted_changes,
    );

    let _ = writeln!(writer);
    let _ = write!(
        writer,
        "{}Copy this output for bug reports: https://github.com/anthropics/ralph/issues{}\\n",
        colors.dim(),
        colors.reset()
    );
}

fn write_system_info<W: Write>(writer: &mut W, colors: Colors) {
    let _ = writeln!(writer, "{}System:{}", colors.bold(), colors.reset());
    let _ = writeln!(
        writer,
        "  OS: {} {}",
        std::env::consts::OS,
        std::env::consts::ARCH
    );
    if let Ok(cwd) = std::env::current_dir() {
        let _ = writeln!(writer, "  Working directory: {}", cwd.display());
    }
    if let Ok(shell) = std::env::var("SHELL") {
        let _ = writeln!(writer, "  Shell: {shell}");
    }
    let _ = writeln!(writer);
}

fn collect_git_info(executor: &dyn ProcessExecutor) -> GitDiagnostics {
    let results = diagnostics_domain::GitRawResults {
        version_output: executor.execute("git", &["--version"], &[], None).ok(),
        rev_parse_output: executor
            .execute("git", &["rev-parse", "--git-dir"], &[], None)
            .ok(),
        branch_output: executor
            .execute("git", &["branch", "--show-current"], &[], None)
            .ok(),
        status_output: executor
            .execute("git", &["status", "--porcelain"], &[], None)
            .ok(),
    };

    let is_repo = results
        .rev_parse_output
        .as_ref()
        .map(|o| o.status.success())
        .unwrap_or(false);

    diagnostics_domain::compute_git_diagnostics_from_raw_results(results, is_repo)
}

fn format_git_info_lines(diagnostics: &GitDiagnostics) -> Vec<String> {
    diagnostics_domain::format_git_info_lines(diagnostics)
}

fn write_git_info<W: Write>(writer: &mut W, colors: Colors, diagnostics: &GitDiagnostics) {
    let _ = writeln!(writer, "{}Git:{}", colors.bold(), colors.reset());
    let lines = format_git_info_lines(diagnostics);
    lines.into_iter().for_each(|line| {
        let _ = writeln!(writer, "{line}");
    });
    let _ = writeln!(writer);
}

fn write_config_info<W: Write>(
    writer: &mut W,
    colors: Colors,
    config: &Config,
    config_path: &Path,
    config_sources: &[ConfigSource],
    workspace: &dyn Workspace,
) {
    let _ = writeln!(writer, "{}Configuration:{}", colors.bold(), colors.reset());
    let lines = diagnostics_domain::format_config_section_lines(
        config,
        config_path,
        config_sources,
        workspace,
    );
    lines.into_iter().for_each(|line| {
        let _ = writeln!(writer, "{line}");
    });
    let _ = writeln!(writer);
}

fn write_agent_chain_info<W: Write>(writer: &mut W, colors: Colors, registry: &AgentRegistry) {
    let _ = writeln!(writer, "{}Agent Drains:{}", colors.bold(), colors.reset());

    let bindings = diagnostics_domain::get_drain_bindings(registry);
    let resolved = registry.resolved_drains();

    bindings.into_iter().for_each(|binding| {
        let _ = writeln!(
            writer,
            "  {} -> {} {:?}",
            binding.drain.as_str(),
            binding.chain_name,
            binding.agents
        );
    });
    let _ = writeln!(writer, "  Max retries: {}", resolved.max_retries);
    let _ = writeln!(writer, "  Retry delay: {}ms", resolved.retry_delay_ms);
    let _ = writeln!(writer);
}

fn write_agent_availability<W: Write>(writer: &mut W, colors: Colors, registry: &AgentRegistry) {
    let _ = writeln!(
        writer,
        "{}Agent Availability:{}",
        colors.bold(),
        colors.reset()
    );
    let lines = diagnostics_domain::format_agent_availability_section(registry);
    lines.into_iter().for_each(|line| {
        let _ = writeln!(writer, "{line}");
    });
    let _ = writeln!(writer);
}

fn write_prompt_status<W: Write>(writer: &mut W, colors: Colors, workspace: &dyn Workspace) {
    let _ = writeln!(writer, "{}PROMPT.md:{}", colors.bold(), colors.reset());
    let lines = diagnostics_domain::format_prompt_status_section(workspace);
    lines.into_iter().for_each(|line| {
        let _ = writeln!(writer, "{line}");
    });
    let _ = writeln!(writer);
}

fn write_checkpoint_status<W: Write>(writer: &mut W, colors: Colors, workspace: &dyn Workspace) {
    let _ = writeln!(writer, "{}Checkpoint:{}", colors.bold(), colors.reset());
    if crate::checkpoint::checkpoint_exists_with_workspace(workspace) {
        let _ = writeln!(writer, "  Exists: yes");
        if let Ok(Some(cp)) = load_checkpoint_with_workspace(workspace) {
            let _ = writeln!(writer, "  Phase: {:?}", cp.phase);
            let _ = writeln!(writer, "  Developer agent: {}", cp.developer_agent);
            let _ = writeln!(writer, "  Reviewer agent: {}", cp.reviewer_agent);
            let _ = writeln!(
                writer,
                "  Iterations: {}/{} dev, {}/{} review",
                cp.iteration, cp.total_iterations, cp.reviewer_pass, cp.total_reviewer_passes
            );
        }
    } else {
        let _ = writeln!(writer, "  Exists: no (no interrupted run to resume)");
    }
    let _ = writeln!(writer);
}

fn write_project_stack<W: Write>(writer: &mut W, colors: Colors, workspace: &dyn Workspace) {
    let _ = writeln!(writer, "{}Project Stack:{}", colors.bold(), colors.reset());
    let lines = diagnostics_domain::format_project_stack_section(workspace);
    lines.into_iter().for_each(|line| {
        let _ = writeln!(writer, "{line}");
    });
    let _ = writeln!(writer);
}

fn write_recent_logs<W: Write>(writer: &mut W, colors: Colors, workspace: &dyn Workspace) {
    match diagnostics_domain::compute_log_section(workspace) {
        diagnostics_domain::ComputeLogSection::NotFound => {
            let _ = writeln!(
                writer,
                "{}No log file found{}",
                colors.yellow(),
                colors.reset()
            );
        }
        diagnostics_domain::ComputeLogSection::Empty => {
            let _ = writeln!(
                writer,
                "{}No log file found{}",
                colors.yellow(),
                colors.reset()
            );
        }
        diagnostics_domain::ComputeLogSection::Content(lines) => {
            let _ = writeln!(
                writer,
                "{}Recent Log Entries (last 10):{}",
                colors.bold(),
                colors.reset()
            );
            lines.into_iter().for_each(|line| {
                let _ = writeln!(writer, "{line}");
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_template_by_name() {
        assert!(get_template("feature-spec").is_some());
        assert!(get_template("bug-fix").is_some());
        assert!(get_template("refactor").is_some());
        assert!(get_template("test").is_some());
        assert!(get_template("docs").is_some());
        assert!(get_template("quick").is_some());
        assert!(get_template("nonexistent").is_none());
    }

    #[test]
    fn test_template_has_required_content() {
        list_templates().into_iter().for_each(|(name, _)| {
            if let Some(template) = get_template(name) {
                let content = template.content();
                assert!(
                    content.contains("## Goal"),
                    "Template {name} missing Goal section"
                );
                assert!(
                    content.contains("Acceptance") || content.contains("## Acceptance Checks"),
                    "Template {name} missing Acceptance section"
                );
            }
        });
    }
}
