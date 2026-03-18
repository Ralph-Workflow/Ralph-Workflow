//! Banner and UI output utilities.
//!
//! This module contains presentation logic for the pipeline's visual output,
//! including the welcome banner and the final summary display.

use crate::io::terminal::{write_banner_to, BannerOutput};
use crate::logger::Colors;
use crate::logger::Loggable;

/// Summary data for pipeline completion display.
///
/// All metrics MUST derive from the final `PipelineState.metrics` to ensure
/// consistency and prevent drift between runtime counters and actual progress.
///
/// # Single Source of Truth
///
/// The reducer is the authoritative source for all execution statistics.
/// This struct is purely a presentation layer that receives reducer-derived
/// metrics and formats them for display.
///
/// Decouples the banner presentation logic from the actual pipeline types.
pub struct PipelineSummary {
    /// Total elapsed time formatted as "Xm YYs"
    pub total_time: String,
    /// Number of developer iterations completed (from reducer metrics)
    pub dev_runs_completed: usize,
    /// Total configured developer iterations (from reducer metrics)
    pub dev_runs_total: usize,
    /// Number of review passes completed (from reducer metrics)
    pub review_passes_completed: usize,
    /// Total configured review passes (from reducer metrics)
    pub review_passes_total: usize,
    /// Number of reviewer runs completed (from reducer metrics)
    pub review_runs: usize,
    /// Number of commits created during pipeline (from reducer metrics)
    pub changes_detected: usize,
    /// Whether isolation mode is enabled
    pub isolation_mode: bool,
    /// Whether to show verbose output
    pub verbose: bool,
    /// Optional review metrics summary
    pub review_summary: Option<ReviewSummary>,
}

/// Review metrics summary for display.
pub struct ReviewSummary {
    /// One-line summary of review results
    pub summary: String,
    /// Number of unresolved issues
    pub unresolved_count: usize,
    /// Number of unresolved blocking issues
    pub blocking_count: usize,
    /// Optional detailed breakdown (for verbose mode)
    pub detailed_breakdown: Option<String>,
    /// Optional sample unresolved issues (for verbose mode)
    pub samples: Vec<String>,
}

/// Print the welcome banner for the Ralph pipeline.
///
/// Displays a styled ASCII box with the pipeline name and agent information.
///
/// # Arguments
///
/// * `colors` - Color configuration for terminal output
/// * `developer_agent` - Name of the developer agent
/// * `reviewer_agent` - Name of the reviewer agent
pub fn print_welcome_banner(colors: Colors, developer_agent: &str, reviewer_agent: &str) {
    let _ = print_welcome_banner_to(colors, developer_agent, reviewer_agent, std::io::stdout());
}

pub fn print_welcome_banner_to<W: BannerOutput>(
    colors: Colors,
    developer_agent: &str,
    reviewer_agent: &str,
    output: W,
) -> std::io::Result<()> {
    let content = build_welcome_banner_content(colors, developer_agent, reviewer_agent);
    write_banner_to(output, &content)
}

fn build_welcome_banner_content(
    colors: Colors,
    developer_agent: &str,
    reviewer_agent: &str,
) -> String {
    let lines = [
        "",
        &format!(
            "{}{}╭────────────────────────────────────────────────────────────╮{}",
            colors.bold(),
            colors.cyan(),
            colors.reset()
        ),
        &format!(
            "{}{}│{}  {}{}🤖 Ralph{} {}─ PROMPT-driven agent orchestrator{}              {}{}│{}",
            colors.bold(),
            colors.cyan(),
            colors.reset(),
            colors.bold(),
            colors.white(),
            colors.reset(),
            colors.dim(),
            colors.reset(),
            colors.bold(),
            colors.cyan(),
            colors.reset()
        ),
        &format!(
            "{}{}│{}  {}{} × {} pipeline for autonomous development{}                 {}{}│{}",
            colors.bold(),
            colors.cyan(),
            colors.reset(),
            colors.dim(),
            developer_agent,
            reviewer_agent,
            colors.reset(),
            colors.bold(),
            colors.cyan(),
            colors.reset()
        ),
        &format!(
            "{}{}╰────────────────────────────────────────────────────────────╯{}",
            colors.bold(),
            colors.cyan(),
            colors.reset()
        ),
        "",
        "",
    ];
    lines.join("\n")
}

/// Print the final summary after pipeline completion.
///
/// Displays statistics about the pipeline run including timing, run counts,
/// and review metrics if available.
///
/// # Arguments
///
/// * `colors` - Color configuration for terminal output
/// * `summary` - Pipeline summary data
/// * `logger` - Logger for final success message (via Loggable trait)
pub fn print_final_summary<L: Loggable>(colors: Colors, summary: &PipelineSummary, logger: &L) {
    let _ = print_final_summary_to(colors, summary, logger, std::io::stdout());
}

pub fn print_final_summary_to<L: Loggable, W: BannerOutput>(
    colors: Colors,
    summary: &PipelineSummary,
    logger: &L,
    output: W,
) -> std::io::Result<()> {
    logger.header("Pipeline Complete", crate::logger::Colors::green);

    let mut content = String::new();
    content.push('\n');
    content.push_str(&format!(
        "{}{}📊 Summary{}",
        colors.bold(),
        colors.white(),
        colors.reset()
    ));
    content.push('\n');
    content.push_str(&format!(
        "{}──────────────────────────────────{}",
        colors.dim(),
        colors.reset()
    ));
    content.push('\n');
    content.push_str(&format!(
        "  {}⏱{}  Total time:      {}{}{}",
        colors.cyan(),
        colors.reset(),
        colors.bold(),
        summary.total_time,
        colors.reset()
    ));
    content.push('\n');
    content.push_str(&format!(
        "  {}🔄{}  Dev runs:        {}{}{}/{}",
        colors.blue(),
        colors.reset(),
        colors.bold(),
        summary.dev_runs_completed,
        colors.reset(),
        summary.dev_runs_total
    ));
    content.push('\n');
    content.push_str(&format!(
        "  {}🔍{}  Review passes:   {}{}{}/{}",
        colors.magenta(),
        colors.reset(),
        colors.bold(),
        summary.review_passes_completed,
        colors.reset(),
        summary.review_passes_total
    ));
    if summary.verbose {
        content.push_str(&format!(
            "  {}  {}  (Total runs:     {}{}{}){}",
            colors.dim(),
            colors.magenta(),
            colors.bold(),
            summary.review_runs,
            colors.reset(),
            colors.reset()
        ));
        content.push('\n');
    }
    content.push_str(&format!(
        "  {}📝{}  Changes detected: {}{}{}",
        colors.green(),
        colors.reset(),
        colors.bold(),
        summary.changes_detected,
        colors.reset()
    ));
    content.push('\n');

    // Review metrics
    if let Some(ref review) = summary.review_summary {
        content.push_str(&build_review_summary_content(
            colors,
            summary.verbose,
            review,
        ));
    }
    content.push('\n');

    content.push_str(&build_output_files_content(colors, summary.isolation_mode));

    write_banner_to(output, &content)?;

    // Use the Loggable trait's success method
    logger.success("Ralph pipeline completed successfully!");

    // Log additional status messages via Loggable trait
    if summary.review_runs > 0 {
        logger.info(&format!("Completed {} review run(s)", summary.review_runs));
    }
    if summary.changes_detected > 0 {
        logger.info(&format!("Detected {} change(s)", summary.changes_detected));
    }
    if summary.isolation_mode {
        logger.info("Running in isolation mode");
    }

    // Log warnings for unresolved issues if present
    if let Some(ref review) = summary.review_summary {
        if review.unresolved_count > 0 {
            logger.warn(&format!(
                "{} unresolved issue(s) remaining",
                review.unresolved_count
            ));
        }
        if review.blocking_count > 0 {
            logger.error(&format!(
                "{} blocking issue(s) unresolved",
                review.blocking_count
            ));
        }
    }

    Ok(())
}

fn build_review_summary_content(colors: Colors, verbose: bool, review: &ReviewSummary) -> String {
    let mut content = String::new();

    // No issues case
    if review.unresolved_count == 0 && review.blocking_count == 0 {
        content.push_str(&format!(
            "  {}✓{}   Review result:   {}{}{}",
            colors.green(),
            colors.reset(),
            colors.bold(),
            review.summary,
            colors.reset()
        ));
        content.push('\n');
        return content;
    }

    // Issues present
    content.push_str(&format!(
        "  {}🔎{}  Review summary:  {}{}{}",
        colors.yellow(),
        colors.reset(),
        colors.bold(),
        review.summary,
        colors.reset()
    ));
    content.push('\n');

    // Show unresolved count
    if review.unresolved_count > 0 {
        content.push_str(&format!(
            "  {}⚠{}   Unresolved:      {}{}{} issues remaining",
            colors.red(),
            colors.reset(),
            colors.bold(),
            review.unresolved_count,
            colors.reset()
        ));
        content.push('\n');
    }

    // Show detailed breakdown in verbose mode
    if verbose {
        if let Some(ref breakdown) = review.detailed_breakdown {
            content.push_str(&format!(
                "  {}📊{}  Breakdown:",
                colors.dim(),
                colors.reset()
            ));
            content.push('\n');
            let breakdown_lines: Vec<&str> = breakdown.lines().collect();
            let dimmed_lines: Vec<String> = breakdown_lines
                .iter()
                .map(|line| format!("      {}{}{}", colors.dim(), line.trim(), colors.reset()))
                .collect();
            content.push_str(&dimmed_lines.join("\n"));
            content.push('\n');
        }
        // Show sample unresolved issues
        if !review.samples.is_empty() {
            content.push_str(&format!(
                "  {}🧾{}  Unresolved samples:",
                colors.dim(),
                colors.reset()
            ));
            content.push('\n');
            let sample_lines: Vec<String> = review
                .samples
                .iter()
                .map(|s| format!("      {}- {}{}", colors.dim(), s, colors.reset()))
                .collect();
            content.push_str(&sample_lines.join("\n"));
            content.push('\n');
        }
    }

    // Highlight blocking issues
    if review.blocking_count > 0 {
        content.push_str(&format!(
            "  {}🚨{}  BLOCKING:        {}{}{} critical/high issues unresolved",
            colors.red(),
            colors.reset(),
            colors.bold(),
            review.blocking_count,
            colors.reset()
        ));
        content.push('\n');
    }

    content
}

fn build_output_files_content(colors: Colors, isolation_mode: bool) -> String {
    let mut content = String::new();
    content.push_str(&format!(
        "{}{}📁 Output Files{}",
        colors.bold(),
        colors.white(),
        colors.reset()
    ));
    content.push('\n');
    content.push_str(&format!(
        "{}──────────────────────────────────{}",
        colors.dim(),
        colors.reset()
    ));
    content.push('\n');
    content.push_str(&format!(
        "  → {}PROMPT.md{}           Goal definition",
        colors.cyan(),
        colors.reset()
    ));
    content.push('\n');
    content.push_str(&format!(
        "  → {}.agent/STATUS.md{}    Current status",
        colors.cyan(),
        colors.reset()
    ));
    content.push('\n');
    // Only show ISSUES.md and NOTES.md when NOT in isolation mode
    if !isolation_mode {
        content.push_str(&format!(
            "  → {}.agent/ISSUES.md{}    Review findings",
            colors.cyan(),
            colors.reset()
        ));
        content.push('\n');
        content.push_str(&format!(
            "  → {}.agent/NOTES.md{}     Progress notes",
            colors.cyan(),
            colors.reset()
        ));
        content.push('\n');
    }
    content.push_str(&format!(
        "  → {}.agent/logs/{}        Detailed logs",
        colors.cyan(),
        colors.reset()
    ));
    content.push('\n');
    content
}
