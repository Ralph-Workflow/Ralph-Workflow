//! Diagnostic command handler - boundary module.
//!
//! This module provides diagnostic output for troubleshooting Ralph configuration.
//! It follows the Boundary-First Architecture pattern:
//! - Pure formatting logic is in `diagnose_format.rs`
//! - This module contains thin I/O boundary functions that write to `std::io::Write`
//!
//! See `docs/plans/2026-03-16-functional-rust-refactoring-plan.md` for details.

use crate::agents::{AgentRegistry, ConfigSource};
use crate::checkpoint::load_checkpoint_with_workspace;
use crate::config::Config;
use crate::diagnostics::run_diagnostics;
use crate::executor::ProcessExecutor;
use crate::guidelines::{CheckSeverity, ReviewGuidelines};
use crate::language_detector;
use crate::logger::Colors;
use crate::workspace::Workspace;
use std::io::Write;
use std::path::{Path, PathBuf};

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
/// * `config_path` - Path to the unified config file
/// * `config_sources` - List of configuration sources that were loaded
/// * `executor` - Process executor for running git commands
/// * `workspace` - Workspace for explicit file operations
pub fn handle_diagnose<W: Write>(
    mut writer: W,
    colors: Colors,
    config: &Config,
    registry: &AgentRegistry,
    config_path: &Path,
    config_sources: &[ConfigSource],
    executor: &dyn ProcessExecutor,
    workspace: &dyn Workspace,
) {
    // Gather diagnostics using the diagnostics module
    let report = run_diagnostics(registry);

    let _ = write!(
        writer,
        "{}=== Ralph Diagnostic Report ==={}\\n\\n",
        colors.bold(),
        colors.reset()
    );

    write_system_info(&mut writer, colors);
    write_git_info(&mut writer, colors, executor);
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

    let _ = write!(writer, "\n");
    let _ = write!(
        writer,
        "{}Copy this output for bug reports: https://github.com/anthropics/ralph/issues{}\\n",
        colors.dim(),
        colors.reset()
    );
}

/// Write system information section.
fn write_system_info<W: Write>(writer: &mut W, colors: Colors) {
    let _ = write!(writer, "{}System:{}\n", colors.bold(), colors.reset());
    let _ = write!(
        writer,
        "  OS: {} {}\n",
        std::env::consts::OS,
        std::env::consts::ARCH
    );
    if let Ok(cwd) = std::env::current_dir() {
        let _ = write!(writer, "  Working directory: {}\\n", cwd.display());
    }
    if let Ok(shell) = std::env::var("SHELL") {
        let _ = write!(writer, "  Shell: {shell}\\n");
    }
    let _ = write!(writer, "\n");
}

/// Write git information section.
fn write_git_info<W: Write>(writer: &mut W, colors: Colors, executor: &dyn ProcessExecutor) {
    let _ = write!(writer, "{}Git:{}\n", colors.bold(), colors.reset());
    if let Ok(output) = executor.execute("git", &["--version"], &[], None) {
        let _ = write!(writer, "  Version: {}\\n", output.stdout.trim());
    }
    let is_repo = executor
        .execute("git", &["rev-parse", "--git-dir"], &[], None)
        .map(|o| o.status.success())
        .unwrap_or(false);
    let _ = write!(
        writer,
        "  In git repo: {}\\n",
        if is_repo { "yes" } else { "no" }
    );
    if is_repo {
        if let Ok(output) = executor.execute("git", &["branch", "--show-current"], &[], None) {
            let _ = write!(writer, "  Current branch: {}\\n", output.stdout.trim());
        }
        if let Ok(output) = executor.execute("git", &["status", "--porcelain"], &[], None) {
            let changes = output.stdout.lines().count();
            let _ = write!(writer, "  Uncommitted changes: {changes}\\n");
        }
    }
    let _ = write!(writer, "\n");
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
    let _ = write!(
        writer,
        "{}Configuration:{}\n",
        colors.bold(),
        colors.reset()
    );
    let _ = write!(writer, "  Unified config: {}\\n", config_path.display());
    let exists_status = if config_path.is_absolute() {
        config_path.strip_prefix(workspace.root()).ok().map_or_else(
            || "unknown (outside workspace)".to_string(),
            |relative| {
                if workspace.exists(relative) {
                    "yes".to_string()
                } else {
                    "no".to_string()
                }
            },
        )
    } else if workspace.exists(config_path) {
        "yes".to_string()
    } else {
        "no".to_string()
    };
    let _ = write!(writer, "  Config exists: {exists_status}\\n");
    let _ = write!(
        writer,
        "  Review depth: {:?} ({})\n",
        config.review_depth,
        config.review_depth.description()
    );
    if !config_sources.is_empty() {
        let _ = write!(writer, "  Loaded sources:\n");
        for src in config_sources {
            let _ = write!(
                writer,
                "    - {} ({} agents)\n",
                src.path.display(),
                src.agents_loaded
            );
        }
    }
    let _ = write!(writer, "\n");
}

/// Write agent chain configuration section.
fn write_agent_chain_info<W: Write>(writer: &mut W, colors: Colors, registry: &AgentRegistry) {
    let _ = write!(writer, "{}Agent Drains:{}\n", colors.bold(), colors.reset());
    let resolved = registry.resolved_drains();
    for drain in crate::agents::AgentDrain::all() {
        if let Some(binding) = resolved.binding(drain) {
            let _ = write!(
                writer,
                "  {} -> {} {:?}\n",
                drain.as_str(),
                binding.chain_name,
                binding.agents
            );
        }
    }
    let _ = write!(writer, "  Max retries: {}\n", resolved.max_retries);
    let _ = write!(writer, "  Retry delay: {}ms\n", resolved.retry_delay_ms);
    let _ = write!(writer, "\n");
}

/// Write agent availability section.
fn write_agent_availability<W: Write>(writer: &mut W, colors: Colors, registry: &AgentRegistry) {
    let _ = write!(
        writer,
        "{}Agent Availability:{}\n",
        colors.bold(),
        colors.reset()
    );
    let all_agents = registry.list();
    let mut sorted_agents: Vec<_> = all_agents.into_iter().collect();
    sorted_agents.sort_by(|(a, _), (b, _)| a.cmp(b));
    for (name, cfg) in sorted_agents {
        let available = registry.is_agent_available(name);
        let status_color = if available {
            colors.green()
        } else {
            colors.red()
        };
        let status_icon = if available { "✓" } else { "✗" };
        let display_name = registry.display_name(name);
        let _ = write!(
            writer,
            "  {}{}{} {} (parser: {}, cmd: {})\n",
            status_color,
            status_icon,
            colors.reset(),
            display_name,
            cfg.json_parser,
            cfg.cmd.split_whitespace().next().unwrap_or(&cfg.cmd)
        );
    }
    let _ = write!(writer, "\n");
}

/// Write PROMPT.md status section.
fn write_prompt_status<W: Write>(writer: &mut W, colors: Colors, workspace: &dyn Workspace) {
    let _ = write!(writer, "{}PROMPT.md:{}\n", colors.bold(), colors.reset());
    let prompt_path = Path::new("PROMPT.md");
    if workspace.exists(prompt_path) {
        if let Ok(content) = workspace.read(prompt_path) {
            let _ = write!(writer, "  Exists: yes\n");
            let _ = write!(writer, "  Size: {} bytes\n", content.len());
            let _ = write!(writer, "  Lines: {}\n", content.lines().count());
            let has_goal = content.contains("## Goal") || content.contains("# Goal");
            let has_acceptance =
                content.contains("## Acceptance") || content.contains("Acceptance Criteria");
            let _ = write!(
                writer,
                "  Has Goal section: {}\n",
                if has_goal { "yes" } else { "no" }
            );
            let _ = write!(
                writer,
                "  Has Acceptance section: {}\n",
                if has_acceptance { "yes" } else { "no" }
            );
        }
    } else {
        let _ = write!(writer, "  Exists: no\n");
    }
    let _ = write!(writer, "\n");
}

/// Write checkpoint status section.
fn write_checkpoint_status<W: Write>(writer: &mut W, colors: Colors, workspace: &dyn Workspace) {
    let _ = write!(writer, "{}Checkpoint:{}\n", colors.bold(), colors.reset());
    if crate::checkpoint::checkpoint_exists_with_workspace(workspace) {
        let _ = write!(writer, "  Exists: yes\n");
        if let Ok(Some(cp)) = load_checkpoint_with_workspace(workspace) {
            let _ = write!(writer, "  Phase: {:?}\n", cp.phase);
            let _ = write!(writer, "  Developer agent: {}\n", cp.developer_agent);
            let _ = write!(writer, "  Reviewer agent: {}\n", cp.reviewer_agent);
            let _ = write!(
                writer,
                "  Iterations: {}/{} dev, {}/{} review\n",
                cp.iteration, cp.total_iterations, cp.reviewer_pass, cp.total_reviewer_passes
            );
        }
    } else {
        let _ = write!(writer, "  Exists: no (no interrupted run to resume)\n");
    }
    let _ = write!(writer, "\n");
}

/// Write project stack detection section.
fn write_project_stack<W: Write>(writer: &mut W, colors: Colors, workspace: &dyn Workspace) {
    let _ = write!(
        writer,
        "{}Project Stack:{}\n",
        colors.bold(),
        colors.reset()
    );
    match language_detector::detect_stack(workspace.root()) {
        Ok(stack) => {
            let _ = write!(writer, "  Primary language: {}\n", stack.primary_language);
            if !stack.secondary_languages.is_empty() {
                let _ = write!(
                    writer,
                    "  Secondary languages: {:?}\n",
                    stack.secondary_languages
                );
            }
            if !stack.frameworks.is_empty() {
                let _ = write!(writer, "  Frameworks: {:?}\n", stack.frameworks);
            }
            if let Some(pm) = &stack.package_manager {
                let _ = write!(writer, "  Package manager: {pm}\n");
            }
            if let Some(tf) = &stack.test_framework {
                let _ = write!(writer, "  Test framework: {tf}\n");
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
                let _ = write!(writer, "  Language flags: {}\n", language_types.join(", "));
            }

            let guidelines = ReviewGuidelines::for_stack(&stack);
            let _ = write!(
                writer,
                "  Review checks: {} total\n",
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
                let _ = write!(
                    writer,
                    "  Check severities: {critical_count} critical, {high_count} high\n"
                );
            }

            let critical_checks: Vec<_> = all_checks
                .iter()
                .filter(|c| matches!(c.severity, CheckSeverity::Critical))
                .take(3)
                .collect();
            if !critical_checks.is_empty() {
                let _ = write!(writer, "  Critical checks (sample):\n");
                for check in critical_checks {
                    let _ = write!(writer, "    - {}\n", check.check);
                }
            }
        }
        Err(e) => {
            let _ = write!(writer, "  Detection failed: {e}\n");
        }
    }
    let _ = write!(writer, "\n");
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
        let _ = write!(
            writer,
            "{}Recent Log Entries (last 10):{}\n",
            colors.bold(),
            colors.reset()
        );
        if let Ok(content) = workspace.read(&log_path) {
            let lines: Vec<&str> = content.lines().collect();
            let start = lines.len().saturating_sub(10);
            for line in &lines[start..] {
                let _ = write!(writer, "  {line}\n");
            }
        }
    } else {
        let _ = write!(
            writer,
            "{}No log file found{}\n",
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

    let mut log_dirs: Vec<String> = entries
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
        .collect();

    log_dirs.sort();

    log_dirs
        .last()
        .map(|dir_name| PathBuf::from(format!(".agent/{dir_name}/pipeline.log")))
}
