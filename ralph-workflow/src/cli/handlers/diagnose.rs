//! Diagnostic command handler.
//!
//! This module provides comprehensive diagnostic output for troubleshooting
//! Ralph configuration and environment issues.
//!
//! # Architecture Note
//!
//! This module operates at the CLI layer (pre-pipeline) and uses the Workspace
//! abstraction for diagnostic file reads. This keeps diagnostics testable and
//! consistent with the pipeline's filesystem access rules.

use crate::agents::{AgentRegistry, ConfigSource};
use crate::checkpoint::load_checkpoint_with_workspace;
use crate::config::Config;
use crate::diagnostics::run_diagnostics;
use crate::executor::ProcessExecutor;
use crate::guidelines::{CheckSeverity, ReviewGuidelines};
use crate::language_detector;
use crate::logger::Colors;
use crate::workspace::Workspace;
use std::path::{Path, PathBuf};

/// Handle --diagnose command.
///
/// Outputs comprehensive diagnostic information including:
/// - System information (OS, architecture, working directory)
/// - Git status and configuration
/// - Agent configuration and availability
/// - PROMPT.md validation
/// - Checkpoint status
/// - Project stack detection
///
/// This output is designed to be copy-pasted into bug reports.
///
/// # Arguments
///
/// * `colors` - Color configuration for output formatting
/// * `config` - The current Ralph configuration
/// * `registry` - The agent registry
/// * `config_path` - Path to the unified config file
/// * `config_sources` - List of configuration sources that were loaded
/// * `workspace` - Workspace for explicit file operations
pub fn handle_diagnose(
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

    println!(
        "{}=== Ralph Diagnostic Report ==={}",
        colors.bold(),
        colors.reset()
    );
    println!();

    print_system_info(colors);
    print_git_info(colors, executor);
    print_config_info(colors, config, config_path, config_sources, workspace);
    print_agent_chain_info(colors, registry);
    print_agent_availability(colors, registry);
    print_prompt_status(colors, workspace);
    print_checkpoint_status(colors, workspace);
    print_project_stack(colors, workspace);
    print_recent_logs(colors, workspace);

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

    println!();
    println!(
        "{}Copy this output for bug reports: https://github.com/anthropics/ralph/issues{}",
        colors.dim(),
        colors.reset()
    );
}

/// Print system information section.
fn print_system_info(colors: Colors) {
    println!("{}System:{}", colors.bold(), colors.reset());
    println!("  OS: {} {}", std::env::consts::OS, std::env::consts::ARCH);
    if let Ok(cwd) = std::env::current_dir() {
        println!("  Working directory: {}", cwd.display());
    }
    if let Ok(shell) = std::env::var("SHELL") {
        println!("  Shell: {shell}");
    }
    println!();
}

/// Print git information section.
fn print_git_info(colors: Colors, executor: &dyn ProcessExecutor) {
    println!("{}Git:{}", colors.bold(), colors.reset());
    if let Ok(output) = executor.execute("git", &["--version"], &[], None) {
        println!("  Version: {}", output.stdout.trim());
    }
    let is_repo = executor
        .execute("git", &["rev-parse", "--git-dir"], &[], None)
        .map(|o| o.status.success())
        .unwrap_or(false);
    println!("  In git repo: {}", if is_repo { "yes" } else { "no" });
    if is_repo {
        if let Ok(output) = executor.execute("git", &["branch", "--show-current"], &[], None) {
            println!("  Current branch: {}", output.stdout.trim());
        }
        // Check for uncommitted changes
        if let Ok(output) = executor.execute("git", &["status", "--porcelain"], &[], None) {
            let changes = output.stdout.lines().count();
            println!("  Uncommitted changes: {changes}");
        }
    }
    println!();
}

/// Print configuration information section.
fn print_config_info(
    colors: Colors,
    config: &Config,
    config_path: &Path,
    config_sources: &[ConfigSource],
    workspace: &dyn Workspace,
) {
    println!("{}Configuration:{}", colors.bold(), colors.reset());
    println!("  Unified config: {}", config_path.display());
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
    println!("  Config exists: {exists_status}");
    println!(
        "  Review depth: {:?} ({})",
        config.review_depth,
        config.review_depth.description()
    );
    // Note: Legacy agent config files (.agent/agents.toml, ~/.config/ralph/agents.toml)
    // are no longer supported. Use unified config file instead.
    if !config_sources.is_empty() {
        println!("  Loaded sources:");
        for src in config_sources {
            println!(
                "    - {} ({} agents)",
                src.path.display(),
                src.agents_loaded
            );
        }
    }
    println!();
}

/// Print agent chain configuration section.
fn print_agent_chain_info(colors: Colors, registry: &AgentRegistry) {
    println!("{}Agent Drains:{}", colors.bold(), colors.reset());
    let resolved = registry.resolved_drains();
    for drain in crate::agents::AgentDrain::all() {
        if let Some(binding) = resolved.binding(drain) {
            println!(
                "  {} -> {} {:?}",
                drain.as_str(),
                binding.chain_name,
                binding.agents
            );
        }
    }
    println!("  Max retries: {}", resolved.max_retries);
    println!("  Retry delay: {}ms", resolved.retry_delay_ms);
    println!();
}

/// Print agent availability section.
fn print_agent_availability(colors: Colors, registry: &AgentRegistry) {
    println!("{}Agent Availability:{}", colors.bold(), colors.reset());
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
        println!(
            "  {}{}{} {} (parser: {}, cmd: {})",
            status_color,
            status_icon,
            colors.reset(),
            display_name,
            cfg.json_parser,
            cfg.cmd.split_whitespace().next().unwrap_or(&cfg.cmd)
        );
    }
    println!();
}

/// Print PROMPT.md status section.
fn print_prompt_status(colors: Colors, workspace: &dyn Workspace) {
    println!("{}PROMPT.md:{}", colors.bold(), colors.reset());
    let prompt_path = Path::new("PROMPT.md");
    if workspace.exists(prompt_path) {
        if let Ok(content) = workspace.read(prompt_path) {
            println!("  Exists: yes");
            println!("  Size: {} bytes", content.len());
            println!("  Lines: {}", content.lines().count());
            let has_goal = content.contains("## Goal") || content.contains("# Goal");
            let has_acceptance =
                content.contains("## Acceptance") || content.contains("Acceptance Criteria");
            println!(
                "  Has Goal section: {}",
                if has_goal { "yes" } else { "no" }
            );
            println!(
                "  Has Acceptance section: {}",
                if has_acceptance { "yes" } else { "no" }
            );
        }
    } else {
        println!("  Exists: no");
    }
    println!();
}

/// Print checkpoint status section.
fn print_checkpoint_status(colors: Colors, workspace: &dyn Workspace) {
    println!("{}Checkpoint:{}", colors.bold(), colors.reset());
    if crate::checkpoint::checkpoint_exists_with_workspace(workspace) {
        println!("  Exists: yes");
        if let Ok(Some(cp)) = load_checkpoint_with_workspace(workspace) {
            println!("  Phase: {:?}", cp.phase);
            println!("  Developer agent: {}", cp.developer_agent);
            println!("  Reviewer agent: {}", cp.reviewer_agent);
            println!(
                "  Iterations: {}/{} dev, {}/{} review",
                cp.iteration, cp.total_iterations, cp.reviewer_pass, cp.total_reviewer_passes
            );
        }
    } else {
        println!("  Exists: no (no interrupted run to resume)");
    }
    println!();
}

/// Print project stack detection section.
fn print_project_stack(colors: Colors, workspace: &dyn Workspace) {
    println!("{}Project Stack:{}", colors.bold(), colors.reset());
    match language_detector::detect_stack(workspace.root()) {
        Ok(stack) => {
            println!("  Primary language: {}", stack.primary_language);
            if !stack.secondary_languages.is_empty() {
                println!("  Secondary languages: {:?}", stack.secondary_languages);
            }
            if !stack.frameworks.is_empty() {
                println!("  Frameworks: {:?}", stack.frameworks);
            }
            if let Some(pm) = &stack.package_manager {
                println!("  Package manager: {pm}");
            }
            if let Some(tf) = &stack.test_framework {
                println!("  Test framework: {tf}");
            }

            // Show language type indicators
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
                println!("  Language flags: {}", language_types.join(", "));
            }

            // Show review guidelines summary
            let guidelines = ReviewGuidelines::for_stack(&stack);
            println!("  Review checks: {} total", guidelines.total_checks());

            // Show severity breakdown
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
                println!("  Check severities: {critical_count} critical, {high_count} high");
            }

            // Show first few critical checks as examples
            let critical_checks: Vec<_> = all_checks
                .iter()
                .filter(|c| matches!(c.severity, CheckSeverity::Critical))
                .take(3)
                .collect();
            if !critical_checks.is_empty() {
                println!("  Critical checks (sample):");
                for check in critical_checks {
                    println!("    - {}", check.check);
                }
            }
        }
        Err(e) => {
            println!("  Detection failed: {e}");
        }
    }
    println!();
}

/// Print recent log entries section.
fn print_recent_logs(colors: Colors, workspace: &dyn Workspace) {
    // Try to find logs from current run (per-run logging)
    // First try to get log_run_id from checkpoint, then try lexicographic sort
    let log_path = crate::checkpoint::load_checkpoint_with_workspace(workspace)
        .ok()
        .flatten()
        .map_or_else(
            || {
                // No checkpoint exists, try to find latest run directory
                find_latest_run_log_directory(workspace)
                    .unwrap_or_else(|| PathBuf::from(".agent/logs/pipeline.log"))
            },
            |checkpoint| {
                // Use log_run_id from checkpoint to find per-run logs
                checkpoint.log_run_id.map_or_else(
                    || {
                        // Older checkpoint without log_run_id, try to find latest run directory
                        find_latest_run_log_directory(workspace)
                            .unwrap_or_else(|| PathBuf::from(".agent/logs/pipeline.log"))
                    },
                    |log_run_id| PathBuf::from(format!(".agent/logs-{log_run_id}/pipeline.log")),
                )
            },
        );

    if workspace.exists(&log_path) {
        println!(
            "{}Recent Log Entries (last 10):{}",
            colors.bold(),
            colors.reset()
        );
        if let Ok(content) = workspace.read(&log_path) {
            let lines: Vec<&str> = content.lines().collect();
            let start = lines.len().saturating_sub(10);
            for line in &lines[start..] {
                println!("  {line}");
            }
        }
    } else {
        println!("{}No log file found{}", colors.yellow(), colors.reset());
    }
}

/// Find the latest run log directory by lexicographic sort.
///
/// Returns None if no run directories are found.
fn find_latest_run_log_directory(workspace: &dyn Workspace) -> Option<PathBuf> {
    // Read .agent directory to find logs-* directories
    let agent_dir = Path::new(".agent");
    if !workspace.is_dir(agent_dir) {
        return None;
    }

    let entries = workspace.read_dir(agent_dir).ok()?;

    // Find all logs-* directories and sort them
    let mut log_dirs: Vec<_> = entries
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

    // Return the pipeline.log path for the latest directory
    log_dirs
        .last()
        .map(|dir_name| PathBuf::from(format!(".agent/{dir_name}/pipeline.log")))
}
