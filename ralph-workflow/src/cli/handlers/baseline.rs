//! Baseline display handler.
//!
//! Handles the --show-baseline CLI flag to display the current
//! start commit and review baseline state.

use crate::git_helpers::{get_current_head_oid, get_review_baseline_info, load_review_baseline};
use crate::git_helpers::{load_start_point, ReviewBaseline};

trait StdIoWriteCompat {
    fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> std::io::Result<()>;
}

impl<T: std::io::Write> StdIoWriteCompat for T {
    fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> std::io::Result<()> {
        std::io::Write::write_fmt(self, args)
    }
}

/// Handle the --show-baseline flag.
///
/// Displays information about the current start commit and review baseline.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_show_baseline() -> std::io::Result<()> {
    let _ = writeln!(
        std::io::stdout(),
        "╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    );
    let _ = writeln!(std::io::stdout(), "RALPH BASELINE STATE\n");

    // Show start commit state
    let _ = writeln!(std::io::stdout(), "Start Commit (.agent/start_commit):");
    match load_start_point() {
        Ok(crate::git_helpers::StartPoint::Commit(oid)) => {
            let _ = writeln!(std::io::stdout(), "  Commit: {oid}");
            print_commit_info(&oid.to_string());
        }
        Ok(crate::git_helpers::StartPoint::EmptyRepo) => {
            let _ = writeln!(
                std::io::stdout(),
                "  State: Empty repository (no commits yet)"
            );
        }
        Err(e) => {
            let _ = writeln!(std::io::stdout(), "  Error: {e}");
        }
    }

    let _ = writeln!(std::io::stdout());

    // Show review baseline state
    let _ = writeln!(
        std::io::stdout(),
        "Review Baseline (.agent/review_baseline.txt):"
    );
    match load_review_baseline() {
        Ok(ReviewBaseline::Commit(oid)) => {
            let _ = writeln!(std::io::stdout(), "  Commit: {oid}");
            print_commit_info(&oid.to_string());
        }
        Ok(ReviewBaseline::NotSet) => {
            let _ = writeln!(
                std::io::stdout(),
                "  State: Not set (using start commit for diff)"
            );
        }
        Err(e) => {
            let _ = writeln!(std::io::stdout(), "  Error: {e}");
        }
    }

    let _ = writeln!(std::io::stdout());

    // Show baseline info (commits since baseline, stale status)
    match get_review_baseline_info() {
        Ok((baseline_oid, commits_since, is_stale)) => {
            let _ = writeln!(std::io::stdout(), "Baseline Analysis:");
            if let Some(oid) = baseline_oid {
                let _ = writeln!(
                    std::io::stdout(),
                    "  Commits since baseline: {commits_since}"
                );
                if is_stale {
                    let _ = writeln!(std::io::stdout(), "  Status: STALE (>10 commits behind)\n           Consider running: ralph --reset-start-commit");
                } else {
                    let _ = writeln!(std::io::stdout(), "  Status: Current (within 10 commits)");
                }

                // Show current HEAD for comparison
                if let Ok(head) = get_current_head_oid() {
                    let _ = writeln!(std::io::stdout());
                    let _ = writeln!(std::io::stdout(), "Current HEAD: {head}");
                    if head != oid {
                        let _ = writeln!(
                            std::io::stdout(),
                            "  Difference: HEAD is {commits_since} commits ahead of baseline"
                        );
                    }
                }
            } else {
                let _ = writeln!(std::io::stdout(), "Baseline Analysis:");
                let _ = writeln!(
                    std::io::stdout(),
                    "  No review baseline set - using start commit"
                );
            }
        }
        Err(e) => {
            let _ = writeln!(std::io::stdout(), "Could not analyze baseline: {e}");
        }
    }

    let _ = writeln!(
        std::io::stdout(),
        "\n╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    );

    Ok(())
}

/// Print information about a commit.
fn print_commit_info(oid: &str) {
    if let Ok(repo) = git2::Repository::discover(".") {
        if let Ok(parsed_oid) = git2::Oid::from_str(oid) {
            if let Ok(commit) = repo.find_commit(parsed_oid) {
                // Get short ID
                let short_id = commit
                    .as_object()
                    .short_id()
                    .ok()
                    .and_then(|buf| buf.as_str().map(std::string::ToString::to_string))
                    .unwrap_or_else(|| {
                        let len = 8.min(oid.len());
                        oid[..len].to_string()
                    });

                let _ = writeln!(std::io::stdout(), "  Short ID: {short_id}");

                // Get author info
                let author = commit.author();
                let name = author.name().unwrap_or("<unknown>");
                let when = author.when();
                let _ = writeln!(std::io::stdout(), "  Author: {name}");
                let _ = writeln!(
                    std::io::stdout(),
                    "  Time: {} seconds since epoch",
                    when.seconds()
                );

                // Get commit summary
                let summary = commit.summary().unwrap_or("<no message>");
                // Truncate long summaries
                let summary = if summary.len() > 60 {
                    format!("{}...", &summary[..57.min(summary.len())])
                } else {
                    summary.to_string()
                };
                let _ = writeln!(std::io::stdout(), "  Summary: {summary}");
            } else {
                let _ = writeln!(
                    std::io::stdout(),
                    "  Warning: Commit not found in repository"
                );
                let _ = writeln!(
                    std::io::stdout(),
                    "  The OID may reference a deleted commit or be from a different repository"
                );
            }
        } else {
            let _ = writeln!(std::io::stdout(), "  Warning: Invalid OID format");
        }
    }
}
