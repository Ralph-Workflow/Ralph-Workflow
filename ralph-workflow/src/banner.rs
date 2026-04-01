//! Banner and UI output utilities.
//!
//! This module contains presentation logic for the pipeline's visual output,
//! including the welcome banner and the final summary display.

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
    /// Number of connectivity interruptions (offline windows) during the run.
    /// None means no connectivity interruptions occurred.
    pub connectivity_interruptions: Option<usize>,
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

pub fn print_welcome_banner_to<W: std::io::Write>(
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

pub fn print_final_summary_to<L: Loggable, W: std::io::Write>(
    colors: Colors,
    summary: &PipelineSummary,
    logger: &L,
    output: W,
) -> std::io::Result<()> {
    logger.header("Pipeline Complete", crate::logger::Colors::green);

    let content = build_final_summary_content(colors, summary);

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

    // Log connectivity interruptions if any occurred
    if let Some(interruptions) = summary.connectivity_interruptions {
        if interruptions > 0 {
            logger.info(&format!(
                "Connectivity interruptions: {} (workflow paused {} times due to offline detection; \
                 no continuation or retry budget was consumed during offline windows)",
                interruptions, interruptions
            ));
        }
    }

    Ok(())
}

fn write_banner_to<W: std::io::Write>(mut output: W, content: &str) -> std::io::Result<()> {
    output.write_all(content.as_bytes())
}

fn build_final_summary_content(colors: Colors, summary: &PipelineSummary) -> String {
    let base_lines: Vec<String> = vec![
        "".to_string(),
        format!(
            "{}{}📊 Summary{}",
            colors.bold(),
            colors.white(),
            colors.reset()
        ),
        format!(
            "{}──────────────────────────────────{}",
            colors.dim(),
            colors.reset()
        ),
        format!(
            "  {}⏱{}  Total time:      {}{}{}",
            colors.cyan(),
            colors.reset(),
            colors.bold(),
            summary.total_time,
            colors.reset()
        ),
        format!(
            "  {}🔄{}  Dev runs:        {}{}{}/{}",
            colors.blue(),
            colors.reset(),
            colors.bold(),
            summary.dev_runs_completed,
            colors.reset(),
            summary.dev_runs_total
        ),
        format!(
            "  {}🔍{}  Review passes:   {}{}{}/{}",
            colors.magenta(),
            colors.reset(),
            colors.bold(),
            summary.review_passes_completed,
            colors.reset(),
            summary.review_passes_total
        ),
        format!(
            "  {}📝{}  Changes detected: {}{}{}",
            colors.green(),
            colors.reset(),
            colors.bold(),
            summary.changes_detected,
            colors.reset()
        ),
    ];

    let verbose_line: Vec<String> = if summary.verbose {
        vec![format!(
            "  {}  {}  (Total runs:     {}{}{}){}",
            colors.dim(),
            colors.magenta(),
            colors.bold(),
            summary.review_runs,
            colors.reset(),
            colors.reset()
        )]
    } else {
        Vec::new()
    };

    let review_lines: Vec<String> = summary
        .review_summary
        .as_ref()
        .map_or(Vec::new(), |review| {
            build_review_summary_content(colors, summary.verbose, review)
                .lines()
                .map(String::from)
                .collect()
        });

    let output_files_lines: Vec<String> =
        build_output_files_content(colors, summary.isolation_mode)
            .lines()
            .map(String::from)
            .collect();

    base_lines
        .into_iter()
        .chain(verbose_line)
        .chain(std::iter::once("".to_string()))
        .chain(review_lines)
        .chain(std::iter::once("".to_string()))
        .chain(output_files_lines)
        .collect::<Vec<_>>()
        .join("\n")
}

fn build_review_summary_content(colors: Colors, verbose: bool, review: &ReviewSummary) -> String {
    if review.unresolved_count == 0 && review.blocking_count == 0 {
        return [format!(
            "  {}✓{}   Review result:   {}{}{}",
            colors.green(),
            colors.reset(),
            colors.bold(),
            review.summary,
            colors.reset()
        )]
        .join("\n");
    }

    let base_line: Vec<String> = vec![format!(
        "  {}🔎{}  Review summary:  {}{}{}",
        colors.yellow(),
        colors.reset(),
        colors.bold(),
        review.summary,
        colors.reset()
    )];

    let unresolved_line: Vec<String> = if review.unresolved_count > 0 {
        vec![format!(
            "  {}⚠{}   Unresolved:      {}{}{} issues remaining",
            colors.red(),
            colors.reset(),
            colors.bold(),
            review.unresolved_count,
            colors.reset()
        )]
    } else {
        Vec::new()
    };

    let verbose_lines: Vec<String> = if verbose {
        let breakdown_lines: Vec<String> =
            review
                .detailed_breakdown
                .as_ref()
                .map_or_else(Vec::<String>::new, |breakdown| {
                    let line_strs: Vec<&str> = breakdown.lines().collect();
                    let dimmed: Vec<String> = line_strs
                        .iter()
                        .map(|line| {
                            format!("      {}{}{}", colors.dim(), line.trim(), colors.reset())
                        })
                        .collect();
                    std::iter::once(format!(
                        "  {}📊{}  Breakdown:",
                        colors.dim(),
                        colors.reset()
                    ))
                    .chain(dimmed)
                    .collect()
                });

        let sample_lines: Vec<String> = if !review.samples.is_empty() {
            std::iter::once(format!(
                "  {}🧾{}  Unresolved samples:",
                colors.dim(),
                colors.reset()
            ))
            .chain(
                review
                    .samples
                    .iter()
                    .map(|s| format!("      {}- {}{}", colors.dim(), s, colors.reset())),
            )
            .collect()
        } else {
            Vec::new()
        };

        breakdown_lines.into_iter().chain(sample_lines).collect()
    } else {
        Vec::new()
    };

    let blocking_line: Vec<String> = if review.blocking_count > 0 {
        vec![format!(
            "  {}🚨{}  BLOCKING:        {}{}{} critical/high issues unresolved",
            colors.red(),
            colors.reset(),
            colors.bold(),
            review.blocking_count,
            colors.reset()
        )]
    } else {
        Vec::new()
    };

    base_line
        .into_iter()
        .chain(unresolved_line)
        .chain(verbose_lines)
        .chain(blocking_line)
        .collect::<Vec<_>>()
        .join("\n")
}

fn build_output_files_content(colors: Colors, isolation_mode: bool) -> String {
    let base_lines: Vec<String> = vec![
        format!(
            "{}{}📁 Output Files{}",
            colors.bold(),
            colors.white(),
            colors.reset()
        ),
        format!(
            "{}──────────────────────────────────{}",
            colors.dim(),
            colors.reset()
        ),
        format!(
            "  → {}PROMPT.md{}           Goal definition",
            colors.cyan(),
            colors.reset()
        ),
        format!(
            "  → {}.agent/STATUS.md{}    Current status",
            colors.cyan(),
            colors.reset()
        ),
    ];

    let isolation_lines: Vec<String> = if !isolation_mode {
        vec![
            format!(
                "  → {}.agent/ISSUES.md{}    Review findings",
                colors.cyan(),
                colors.reset()
            ),
            format!(
                "  → {}.agent/NOTES.md{}     Progress notes",
                colors.cyan(),
                colors.reset()
            ),
        ]
    } else {
        Vec::new()
    };

    base_lines
        .into_iter()
        .chain(isolation_lines)
        .chain(std::iter::once(format!(
            "  → {}.agent/logs/{}        Detailed logs",
            colors.cyan(),
            colors.reset()
        )))
        .collect::<Vec<_>>()
        .join("\n")
}
