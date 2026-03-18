//! Diagnostic command handler - boundary module.
//!
//! This module provides diagnostic output for troubleshooting Ralph configuration.
//! It follows the Boundary-First Architecture pattern:
//! - Pure formatting logic is in `../../diagnostics_domain.rs`
//! - This module contains thin I/O boundary functions that write to `std::io::Write`
//!
//! See `docs/plans/2026-03-16-functional-rust-refactoring-plan.md` for details.

use crate::agents::{AgentRegistry, ConfigSource};
use crate::checkpoint::load_checkpoint_with_workspace;
use crate::cli::diagnostics_domain::{self, ConfigExistsStatus, GitCommandPlan, GitDiagnostics};
use crate::config::Config;
use crate::diagnostics::run_diagnostics;
use crate::executor::ProcessExecutor;
use crate::guidelines::{CheckSeverity, ReviewGuidelines};
use crate::language_detector;
use crate::logger::Colors;
use crate::workspace::Workspace;
use itertools::Itertools;
use std::io::Write;
use std::path::{Path, PathBuf};

/// Groups config path and sources together to reduce function argument count.
pub struct ConfigInfo<'a> {
    pub path: &'a Path,
    pub sources: &'a [ConfigSource],
}

/// Handle --diagnose command.
///
/// Writes comprehensive diagnostic information to the provided writer.
/// This output is designed to be copy-pasted into bug reports.
///
/// # Arguments
///
/// * `writer` - Output destination (stdout, test buffer, etc.)
/// * `colors` - Color configuration for output formatting
/// * `config` - The current Ralph configuration
/// * `registry` - The agent registry
/// * `config_info` - Path to the unified config file and list of config sources
/// * `executor` - Process executor for running git commands
/// * `workspace` - Workspace for explicit file operations
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
    // Gather diagnostics using the diagnostics module
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

    // Use diagnostic data to suppress dead code warnings
    let _ = report.agents.total_agents;
    let _ = report.agents.available_agents;
    let _ = report.agents.unavailable_agents;
    for status in &report.agents.agent_status {
        let _ = (
            &status.name,
            &status.display_name,
            status.available,
            &status.json_parser,
            &status.command,
        );
    }
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

/// Write system information section.
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

/// Collect git diagnostic information using pure policy decisions.
///
/// This is a thin boundary function that:
/// 1. Gets the initial version check result
/// 2. Uses pure policy functions to decide what commands to run
/// 3. Executes the planned commands
fn collect_git_info(executor: &dyn ProcessExecutor) -> GitDiagnostics {
    // Step 1: Get initial version check (always needed)
    let version = executor
        .execute("git", &["--version"], &[], None)
        .ok()
        .map(|o| o.stdout.trim().to_string());

    // Step 2: Use pure policy to decide what to do next
    let version_available = version.is_some();
    let plan = diagnostics_domain::plan_git_commands(version_available);

    // Step 3: Execute based on plan
    match plan {
        GitCommandPlan::None => GitDiagnostics {
            version: None,
            is_repo: false,
            branch: None,
            uncommitted_changes: None,
        },
        GitCommandPlan::VersionOnly => GitDiagnostics {
            version,
            is_repo: false,
            branch: None,
            uncommitted_changes: None,
        },
        GitCommandPlan::Partial | GitCommandPlan::Full => {
            let is_repo = executor
                .execute("git", &["rev-parse", "--git-dir"], &[], None)
                .map(|o| o.status.success())
                .unwrap_or(false);

            // Use pure policy to decide whether to check branch
            let branch = if diagnostics_domain::should_check_branch(is_repo) {
                executor
                    .execute("git", &["branch", "--show-current"], &[], None)
                    .ok()
                    .map(|o| o.stdout.trim().to_string())
            } else {
                None
            };

            // Use pure policy to decide whether to check uncommitted changes
            let uncommitted_changes = if diagnostics_domain::should_check_uncommitted(is_repo) {
                executor
                    .execute("git", &["status", "--porcelain"], &[], None)
                    .ok()
                    .map(|o| o.stdout.lines().count())
            } else {
                None
            };

            diagnostics_domain::build_git_diagnostics(version, is_repo, branch, uncommitted_changes)
        }
    }
}

/// Format git diagnostic information as lines (delegates to pure domain function).
fn format_git_info_lines(diagnostics: &GitDiagnostics) -> Vec<String> {
    diagnostics_domain::format_git_info_lines(diagnostics)
}

/// Write git information section.
fn write_git_info<W: Write>(writer: &mut W, colors: Colors, diagnostics: &GitDiagnostics) {
    let _ = writeln!(writer, "{}Git:{}", colors.bold(), colors.reset());
    let lines = format_git_info_lines(diagnostics);
    for line in lines {
        let _ = writeln!(writer, "{line}");
    }
    let _ = writeln!(writer);
}

/// Write configuration information section.
fn write_config_info<W: Write>(
    writer: &mut W,
    colors: Colors,
    config: &Config,
    config_path: &Path,
    config_sources: &[ConfigSource],
    workspace: &dyn Workspace,
) {
    let _ = writeln!(writer, "{}Configuration:{}", colors.bold(), colors.reset());
    let _ = writeln!(writer, "  Unified config: {}", config_path.display());

    // Use pure domain function to determine config exists status
    let exists_status = diagnostics_domain::determine_config_exists(
        config_path.is_absolute(),
        workspace,
        config_path,
    );
    let exists_str = match exists_status {
        ConfigExistsStatus::Yes => "yes",
        ConfigExistsStatus::No => "no",
        ConfigExistsStatus::Unknown(s) => &s,
    };
    let _ = writeln!(writer, "  Config exists: {exists_str}");
    let _ = writeln!(
        writer,
        "  Review depth: {:?} ({})",
        config.review_depth,
        config.review_depth.description()
    );
    if !config_sources.is_empty() {
        let _ = writeln!(writer, "  Loaded sources:");
        for src in config_sources {
            let _ = writeln!(
                writer,
                "    - {} ({} agents)",
                src.path.display(),
                src.agents_loaded
            );
        }
    }
    let _ = writeln!(writer);
}

/// Write agent chain configuration section.
fn write_agent_chain_info<W: Write>(writer: &mut W, colors: Colors, registry: &AgentRegistry) {
    let _ = writeln!(writer, "{}Agent Drains:{}", colors.bold(), colors.reset());

    // Use pure domain function to get drain bindings
    let bindings = diagnostics_domain::get_drain_bindings(registry);
    let resolved = registry.resolved_drains();

    for binding in bindings {
        let _ = writeln!(
            writer,
            "  {} -> {} {:?}",
            binding.drain.as_str(),
            binding.chain_name,
            binding.agents
        );
    }
    let _ = writeln!(writer, "  Max retries: {}", resolved.max_retries);
    let _ = writeln!(writer, "  Retry delay: {}ms", resolved.retry_delay_ms);
    let _ = writeln!(writer);
}

/// Write agent availability section.
fn write_agent_availability<W: Write>(writer: &mut W, colors: Colors, registry: &AgentRegistry) {
    let _ = writeln!(
        writer,
        "{}Agent Availability:{}",
        colors.bold(),
        colors.reset()
    );

    // Use pure domain function to get sorted agent availability
    let agents = diagnostics_domain::get_sorted_agent_availability(registry);

    for agent in agents {
        let status_color = if agent.available {
            colors.green()
        } else {
            colors.red()
        };
        let status_icon = if agent.available { "✓" } else { "✗" };
        let _ = writeln!(
            writer,
            "  {}{}{} {} (parser: {}, cmd: {})",
            status_color,
            status_icon,
            colors.reset(),
            agent.name,
            agent.json_parser,
            agent
                .command
                .split_whitespace()
                .next()
                .unwrap_or(&agent.command)
        );
    }
    let _ = writeln!(writer);
}

/// Write PROMPT.md status section.
fn write_prompt_status<W: Write>(writer: &mut W, colors: Colors, workspace: &dyn Workspace) {
    let _ = writeln!(writer, "{}PROMPT.md:{}", colors.bold(), colors.reset());
    let prompt_path = Path::new("PROMPT.md");
    if workspace.exists(prompt_path) {
        if let Ok(content) = workspace.read(prompt_path) {
            // Use pure domain function to analyze content
            let analysis = diagnostics_domain::analyze_prompt_content(&content);
            let _ = writeln!(writer, "  Exists: yes");
            let _ = writeln!(writer, "  Size: {} bytes", analysis.size_bytes.unwrap_or(0));
            let _ = writeln!(writer, "  Lines: {}", analysis.line_count.unwrap_or(0));
            let _ = writeln!(
                writer,
                "  Has Goal section: {}",
                if analysis.has_goal_section {
                    "yes"
                } else {
                    "no"
                }
            );
            let _ = writeln!(
                writer,
                "  Has Acceptance section: {}",
                if analysis.has_acceptance_section {
                    "yes"
                } else {
                    "no"
                }
            );
        }
    } else {
        let _ = writeln!(writer, "  Exists: no");
    }
    let _ = writeln!(writer);
}

/// Write checkpoint status section.
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

/// Write project stack detection section.
fn write_project_stack<W: Write>(writer: &mut W, colors: Colors, workspace: &dyn Workspace) {
    let _ = writeln!(writer, "{}Project Stack:{}", colors.bold(), colors.reset());
    match language_detector::detect_stack(workspace.root()) {
        Ok(stack) => {
            let _ = writeln!(writer, "  Primary language: {}", stack.primary_language);
            if !stack.secondary_languages.is_empty() {
                let _ = writeln!(
                    writer,
                    "  Secondary languages: {:?}",
                    stack.secondary_languages
                );
            }
            if !stack.frameworks.is_empty() {
                let _ = writeln!(writer, "  Frameworks: {:?}", stack.frameworks);
            }
            if let Some(pm) = &stack.package_manager {
                let _ = writeln!(writer, "  Package manager: {pm}");
            }
            if let Some(tf) = &stack.test_framework {
                let _ = writeln!(writer, "  Test framework: {tf}");
            }

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
            if !language_types.is_empty() {
                let _ = writeln!(writer, "  Language flags: {}", language_types.join(", "));
            }

            let guidelines = ReviewGuidelines::for_stack(&stack);
            let _ = writeln!(
                writer,
                "  Review checks: {} total",
                guidelines.total_checks()
            );

            let all_checks = guidelines.get_all_checks();
            let critical_count = all_checks
                .iter()
                .filter(|c| matches!(c.severity, CheckSeverity::Critical))
                .count();
            let high_count = all_checks
                .iter()
                .filter(|c| matches!(c.severity, CheckSeverity::High))
                .count();
            if critical_count > 0 || high_count > 0 {
                let _ = writeln!(
                    writer,
                    "  Check severities: {critical_count} critical, {high_count} high"
                );
            }

            let critical_checks: Vec<_> = all_checks
                .iter()
                .filter(|c| matches!(c.severity, CheckSeverity::Critical))
                .take(3)
                .collect();
            if !critical_checks.is_empty() {
                let _ = writeln!(writer, "  Critical checks (sample):");
                for check in critical_checks {
                    let _ = writeln!(writer, "    - {}", check.check);
                }
            }
        }
        Err(e) => {
            let _ = writeln!(writer, "  Detection failed: {e}");
        }
    }
    let _ = writeln!(writer);
}

/// Write recent log entries section.
fn write_recent_logs<W: Write>(writer: &mut W, colors: Colors, workspace: &dyn Workspace) {
    let log_path = crate::checkpoint::load_checkpoint_with_workspace(workspace)
        .ok()
        .flatten()
        .map_or_else(
            || {
                find_latest_run_log_directory(workspace)
                    .unwrap_or_else(|| PathBuf::from(".agent/logs/pipeline.log"))
            },
            |checkpoint| {
                checkpoint.log_run_id.map_or_else(
                    || {
                        find_latest_run_log_directory(workspace)
                            .unwrap_or_else(|| PathBuf::from(".agent/logs/pipeline.log"))
                    },
                    |log_run_id| PathBuf::from(format!(".agent/logs-{log_run_id}/pipeline.log")),
                )
            },
        );

    if workspace.exists(&log_path) {
        let _ = writeln!(
            writer,
            "{}Recent Log Entries (last 10):{}",
            colors.bold(),
            colors.reset()
        );
        if let Ok(content) = workspace.read(&log_path) {
            let lines: Vec<&str> = content.lines().collect();
            let start = lines.len().saturating_sub(10);
            for line in &lines[start..] {
                let _ = writeln!(writer, "  {line}");
            }
        }
    } else {
        let _ = writeln!(
            writer,
            "{}No log file found{}",
            colors.yellow(),
            colors.reset()
        );
    }
}

/// Find the latest run log directory by lexicographic sort.
///
/// Returns None if no run directories were found.
fn find_latest_run_log_directory(workspace: &dyn Workspace) -> Option<PathBuf> {
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
        .map(|dir_name| PathBuf::from(format!(".agent/{dir_name}/pipeline.log")))
}
