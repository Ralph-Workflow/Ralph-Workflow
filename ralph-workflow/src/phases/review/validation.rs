//! Review phase validation checks.
//!
//! This module contains pre-flight and post-flight validation logic for the review phase.
//! These checks verify that the environment is suitable for running the review agent
//! and help diagnose issues early.

use crate::agents::{contains_glm_model, is_glm_like_agent};
use crate::review_metrics::ReviewMetrics;
use crate::workspace::Workspace;
use std::path::Path;

/// Maximum number of files in .agent directory before warning about cleanup.
const MAX_AGENT_DIR_ENTRY_COUNT: usize = 1000;

/// Result of pre-flight validation
#[derive(Debug)]
pub enum PreflightResult {
    /// All checks passed
    Ok,
    /// Warning issued but can proceed
    Warning(String),
    /// Critical error that should halt execution
    Error(String),
}

/// Result of post-flight validation
#[derive(Debug)]
pub enum PostflightResult {
    /// ISSUES.md found and valid
    Valid,
    /// ISSUES.md missing or empty
    Missing(String),
    /// ISSUES.md has unexpected format
    Malformed(String),
}

/// Run pre-flight validation checks before starting a review pass.
///
/// These checks verify that the environment is suitable for running
/// the review agent and help diagnose issues early.
///
/// Uses workspace abstraction for file operations, enabling testing with
/// `MemoryWorkspace`.
pub fn pre_flight_review_check(
    workspace: &dyn Workspace,
    logger: &crate::logger::Logger,
    cycle: u32,
    reviewer_agent: &str,
    reviewer_model: Option<&str>,
) -> PreflightResult {
    let agent_dir = Path::new(".agent");
    let issues_path = Path::new(".agent/ISSUES.md");

    // Check 0: Agent compatibility warning (non-blocking)
    let is_problematic_reviewer = is_problematic_prompt_target(reviewer_agent, reviewer_model);

    if is_problematic_reviewer {
        logger.warn(&format!(
            "Note: Reviewer may have compatibility issues with review tasks. (agent='{}', model={})",
            reviewer_agent,
            reviewer_model.unwrap_or("none")
        ));
        logger.info("If review fails, consider these workarounds:");
        logger.info("  1. Use Claude/Codex as reviewer: ralph --reviewer-agent codex");
        logger.info("  2. Try generic parser: ralph --reviewer-json-parser generic");
        logger.info("  3. Skip review: RALPH_REVIEWER_REVIEWS=0 ralph");
        // Continue anyway - don't block execution
    }

    // Check 0.1: GLM-specific command validation (diagnostic only)
    if is_glm_like_agent(reviewer_agent) {
        // Log diagnostic info about GLM agent configuration
        logger.info(&format!(
            "GLM agent detected: '{reviewer_agent}'. Command will include '-p' flag for non-interactive mode."
        ));
        logger.info("Tip: Use --verbosity debug to see the full command being executed");
    }

    // Check 0.5: Check for existing ISSUES.md from previous failed run
    if workspace.exists(issues_path) {
        // Try to read to check if it has content
        match workspace.read(issues_path) {
            Ok(content) if !content.is_empty() => {
                logger.warn(&format!(
                    "ISSUES.md already exists from a previous run (size: {} bytes).",
                    content.len()
                ));
                logger
                    .info("The review agent will overwrite this file. If the previous run failed,");
                logger.info("consider checking the old ISSUES.md for clues about what went wrong.");
            }
            Ok(_) => {
                // Empty ISSUES.md - warn but continue
                logger.warn("Found empty ISSUES.md from previous run. Will be overwritten.");
            }
            Err(e) => {
                logger.warn(&format!("Cannot read ISSUES.md: {e}"));
            }
        }
    }

    // Check 1: Verify .agent directory is writable
    if !workspace.is_dir(agent_dir) {
        // Try to create it
        if let Err(e) = workspace.create_dir_all(agent_dir) {
            return PreflightResult::Error(format!(
                "Cannot create .agent directory: {e}. Check directory permissions."
            ));
        }
    }

    // Test write by touching a temp file
    let test_file = agent_dir.join(format!(".write_test_{cycle}"));
    match workspace.write(&test_file, "test") {
        Ok(()) => {
            let _ = workspace.remove(&test_file);
        }
        Err(e) => {
            return PreflightResult::Error(format!(
                ".agent directory is not writable: {e}. Check file permissions."
            ));
        }
    }

    // Check 2: Check number of files in .agent directory
    // (workspace read_dir gives us entry count without needing metadata)
    if let Ok(entries) = workspace.read_dir(agent_dir) {
        let entry_count = entries.len();
        if entry_count > MAX_AGENT_DIR_ENTRY_COUNT {
            logger.warn(&format!(
                ".agent directory has {entry_count} files. Consider cleaning up old logs."
            ));
            return PreflightResult::Warning(
                "Large .agent directory detected. Review may be slow.".to_string(),
            );
        }
    }

    PreflightResult::Ok
}

/// Run post-flight validation after a review pass completes.
///
/// These checks verify that the review agent produced expected output.
///
/// Uses workspace abstraction for file operations, enabling testing with
/// `MemoryWorkspace`.
pub fn post_flight_review_check(
    workspace: &dyn Workspace,
    logger: &crate::logger::Logger,
    cycle: u32,
) -> PostflightResult {
    let issues_path = Path::new(".agent/ISSUES.md");

    // Check 1: Verify ISSUES.md exists
    if !workspace.exists(issues_path) {
        logger.warn(&format!(
            "Review cycle {cycle} completed but ISSUES.md was not created. \
             The agent may have failed or used a different output format."
        ));
        logger.info("Possible causes:");
        logger.info("  - Agent failed to write the file (permission/execution error)");
        logger.info("  - Agent used a different output filename or format");
        logger.info("  - Agent was interrupted during execution");
        return PostflightResult::Missing(
            "ISSUES.md not found after review. Agent may have failed.".to_string(),
        );
    }

    // Check 2: Verify ISSUES.md is not empty and log its size
    let file_size = match workspace.read(issues_path) {
        Ok(content) if content.is_empty() => {
            logger.warn(&format!("Review cycle {cycle} created an empty ISSUES.md."));
            logger.info("Possible causes:");
            logger.info("  - Agent reviewed but found no issues (should write 'No issues found.')");
            logger.info("  - Agent failed during file write");
            logger.info("  - Agent doesn't understand the expected output format");
            return PostflightResult::Missing("ISSUES.md is empty".to_string());
        }
        Ok(content) => {
            // Log the file size for debugging
            let size = content.len() as u64;
            logger.info(&format!("ISSUES.md created ({size} bytes)"));
            size
        }
        Err(e) => {
            logger.warn(&format!("Cannot read ISSUES.md: {e}"));
            return PostflightResult::Missing(format!("Cannot read ISSUES.md: {e}"));
        }
    };

    // Check 3: Verify ISSUES.md has valid structure
    match ReviewMetrics::from_issues_file_with_workspace(workspace) {
        Ok(metrics) => {
            // Track whether metrics were successfully parsed from the file
            let _parsed = metrics.issues_file_found;
            // Check if metrics indicate reasonable content
            if metrics.total_issues == 0 && !metrics.no_issues_declared {
                // Partial recovery: file has content but no parseable issues
                logger.warn(&format!(
                    "Review cycle {cycle} produced ISSUES.md ({file_size} bytes) but no parseable issues detected."
                ));
                logger.info("Content may be in unexpected format. The fix pass may still work.");
                logger.info(
                    "Consider checking .agent/ISSUES.md manually to see what the agent wrote.",
                );
                return PostflightResult::Malformed(
                    "ISSUES.md exists but no issues detected. Check format.".to_string(),
                );
            }

            // Log a summary of what was found
            if metrics.total_issues > 0 {
                logger.info(&format!(
                    "Review found {} issues ({} critical, {} high, {} medium, {} low)",
                    metrics.total_issues,
                    metrics.critical_issues,
                    metrics.high_issues,
                    metrics.medium_issues,
                    metrics.low_issues
                ));
                if metrics.resolved_issues > 0 {
                    logger.info(&format!(
                        "  {} issues already resolved",
                        metrics.resolved_issues
                    ));
                }
            } else if metrics.no_issues_declared {
                logger.info("Review declared no issues found.");
            }

            PostflightResult::Valid
        }
        Err(e) => {
            // Partial recovery: attempt to show what content we can
            logger.warn(&format!("Failed to parse ISSUES.md: {e}"));
            logger.info(&format!(
                "ISSUES.md has {file_size} bytes but failed to parse."
            ));
            logger.info("The file may be malformed or in an unexpected format.");
            logger.info(
                "Attempting partial recovery: fix pass will proceed but may have limited success.",
            );

            // Try to read first few lines to give user a hint
            if let Ok(content) = workspace.read(issues_path) {
                let preview: String = content.lines().take(5).collect::<Vec<_>>().join("\n");
                if !preview.is_empty() {
                    logger.info("ISSUES.md preview (first 5 lines):");
                    for line in preview.lines() {
                        logger.info(&format!("  {line}"));
                    }
                }
            }

            PostflightResult::Malformed(format!("Failed to parse ISSUES.md: {e}"))
        }
    }
}

/// Check if the given agent/model combination is a problematic prompt target.
///
/// Certain AI agents have known compatibility issues with complex structured prompts.
/// This function detects those agents for which alternative handling may be needed.
fn is_problematic_prompt_target(agent: &str, model_flag: Option<&str>) -> bool {
    contains_glm_model(agent) || model_flag.is_some_and(contains_glm_model)
}
