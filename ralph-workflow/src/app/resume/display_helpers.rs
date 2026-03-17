// Display helper functions for checkpoint resume.
// This module provides utility functions for formatting checkpoint output.

fn parse_checkpoint_timestamp_as_local(timestamp: &str) -> Option<chrono::DateTime<chrono::Local>> {
    use chrono::{Local, LocalResult, NaiveDateTime, TimeZone};

    let dt = NaiveDateTime::parse_from_str(timestamp, "%Y-%m-%d %H:%M:%S").ok()?;
    match Local.from_local_datetime(&dt) {
        LocalResult::Single(t) => Some(t),
        // For ambiguous times (DST fall-back), pick the latest interpretation so
        // "time ago" output doesn't go negative around transitions.
        LocalResult::Ambiguous(_, latest) => Some(latest),
        LocalResult::None => None,
    }
}

fn format_time_ago(duration: chrono::TimeDelta) -> String {
    if duration.num_days() > 0 {
        format!("{} day(s) ago", duration.num_days())
    } else if duration.num_hours() > 0 {
        format!("{} hour(s) ago", duration.num_hours())
    } else if duration.num_minutes() > 0 {
        format!("{} minute(s) ago", duration.num_minutes())
    } else {
        "just now".to_string()
    }
}

/// Reconstruct the original command from checkpoint data.
///
/// This function attempts to reconstruct the exact command that was used
/// to create the checkpoint, including all relevant flags and options.
fn reconstruct_command(checkpoint: &PipelineCheckpoint) -> Option<String> {
    let cli = &checkpoint.cli_args;
    let mut parts = vec!["ralph".to_string()];

    // Add -D flag
    if cli.developer_iters > 0 {
        parts.push(format!("-D {}", cli.developer_iters));
    }

    // Add -R flag
    if cli.reviewer_reviews > 0 {
        parts.push(format!("-R {}", cli.reviewer_reviews));
    }

    // Add --review-depth if specified
    if let Some(ref depth) = cli.review_depth {
        parts.push(format!("--review-depth {depth}"));
    }

    // Add --no-isolation if false (isolation_mode defaults to true)
    if !cli.isolation_mode {
        parts.push("--no-isolation".to_string());
    }

    // Add verbosity flags
    match cli.verbosity {
        0 => parts.push("--quiet".to_string()),
        2 => parts.push("--verbose".to_string()),
        3 => parts.push("--full".to_string()),
        4 => parts.push("--debug".to_string()),
        _ => {} // Default verbosity or unknown
    }

    // Add --show-streaming-metrics if true
    if cli.show_streaming_metrics {
        parts.push("--show-streaming-metrics".to_string());
    }

    // Add --reviewer-json-parser if specified
    if let Some(ref parser) = cli.reviewer_json_parser {
        parts.push(format!("--reviewer-json-parser {parser}"));
    }

    // Add --agent flags if agents differ from defaults
    // Note: We can't determine defaults here, so we always show them
    parts.push(format!("--agent {}", checkpoint.developer_agent));
    parts.push(format!("--reviewer-agent {}", checkpoint.reviewer_agent));

    // Add model overrides if present
    if let Some(ref model) = checkpoint.developer_agent_config.model_override {
        parts.push(format!("--model \"{model}\""));
    }
    if let Some(ref model) = checkpoint.reviewer_agent_config.model_override {
        parts.push(format!("--reviewer-model \"{model}\""));
    }

    // Add provider overrides if present
    if let Some(ref provider) = checkpoint.developer_agent_config.provider_override {
        parts.push(format!("--provider \"{provider}\""));
    }
    if let Some(ref provider) = checkpoint.reviewer_agent_config.provider_override {
        parts.push(format!("--reviewer-provider \"{provider}\""));
    }

    if parts.len() > 1 {
        Some(parts.join(" "))
    } else {
        None
    }
}

/// Suggest the next step based on the current checkpoint phase.
///
/// Returns a detailed, actionable description of what will happen next
/// when the user resumes from this checkpoint.
fn suggest_next_step(checkpoint: &PipelineCheckpoint) -> String {
    match checkpoint.phase {
        PipelinePhase::Planning => {
            "continue creating implementation plan from PROMPT.md".to_string()
        }
        PipelinePhase::PreRebase => "complete rebase before starting development".to_string(),
        PipelinePhase::PreRebaseConflict => {
            "resolve rebase conflicts then continue to development".to_string()
        }
        PipelinePhase::Development => {
            if checkpoint.iteration < checkpoint.total_iterations {
                format!(
                    "continue development iteration {} of {} (will use same prompts as before)",
                    checkpoint.iteration.saturating_add(1),
                    checkpoint.total_iterations
                )
            } else {
                "move to review phase".to_string()
            }
        }
        PipelinePhase::Review => {
            if checkpoint.reviewer_pass < checkpoint.total_reviewer_passes {
                format!(
                    "continue review pass {} of {} (will review recent changes)",
                    checkpoint.reviewer_pass.saturating_add(1),
                    checkpoint.total_reviewer_passes
                )
            } else {
                "complete review cycle".to_string()
            }
        }
        PipelinePhase::PostRebase => "complete post-development rebase".to_string(),
        PipelinePhase::PostRebaseConflict => "resolve post-rebase conflicts".to_string(),
        PipelinePhase::CommitMessage => "finalize commit message".to_string(),
        PipelinePhase::FinalValidation => "complete final validation".to_string(),
        PipelinePhase::Complete => "pipeline complete!".to_string(),
        PipelinePhase::Rebase => "complete rebase operation".to_string(),
        PipelinePhase::AwaitingDevFix => {
            "attempt to fix pipeline failure and emit completion marker".to_string()
        }
        PipelinePhase::Interrupted => {
            // Provide more detailed information for interrupted state
            // The interrupted phase can occur at any point, so we need to describe
            // what the user was doing when interrupted
            let mut context = vec!["resume from interrupted state".to_string()];

            // Add context about what was being worked on
            if checkpoint.iteration > 0 {
                context.push(format!(
                    "(development iteration {}/{})",
                    checkpoint.iteration, checkpoint.total_iterations
                ));
            }
            if checkpoint.reviewer_pass > 0 {
                context.push(format!(
                    "(review pass {}/{})",
                    checkpoint.reviewer_pass, checkpoint.total_reviewer_passes
                ));
            }

            // Explain what will happen on resume
            context.push("full pipeline will run from interrupted point".to_string());

            context.join(" - ")
        }
    }
}

/// Create a visual progress bar for checkpoint summary display.
fn create_progress_bar(current: u32, total: u32) -> String {
    if total == 0 {
        return "[----]".to_string();
    }

    let width: u32 = 20;
    let current_clamped = current.min(total);
    let filled = current_clamped
        .saturating_mul(width)
        .saturating_add(total / 2)
        .checked_div(total)
        .unwrap_or(0);

    let mut bar = String::from("[");
    for i in 0..width {
        if i < filled {
            bar.push('=');
        } else {
            bar.push('-');
        }
    }
    bar.push(']');

    let percentage = current_clamped
        .saturating_mul(100)
        .saturating_add(total / 2)
        .checked_div(total)
        .unwrap_or(0);
    format!("{bar} {percentage}%")
}

/// Get a stable, ASCII-only indicator for a pipeline phase.
///
/// This intentionally avoids emoji glyphs to keep output stable and compatible
/// with terminals and consumers that parse output.
const fn get_phase_indicator(phase: PipelinePhase) -> &'static str {
    match phase {
        PipelinePhase::Rebase => "[rebase]",
        PipelinePhase::Planning => "[plan]",
        PipelinePhase::Development => "[dev]",
        PipelinePhase::Review => "[review]",
        PipelinePhase::CommitMessage => "[commit]",
        PipelinePhase::FinalValidation => "[validate]",
        PipelinePhase::Complete => "[complete]",
        PipelinePhase::PreRebase => "[pre-rebase]",
        PipelinePhase::PreRebaseConflict | PipelinePhase::PostRebaseConflict => "[rebase-conflict]",
        PipelinePhase::PostRebase => "[post-rebase]",
        PipelinePhase::AwaitingDevFix => "[dev-fix]",
        PipelinePhase::Interrupted => "[interrupted]",
    }
}

/// Get a stable, ASCII-only marker for an execution step outcome.
///
/// This intentionally avoids Unicode glyphs so `--inspect-checkpoint` output
/// stays stable on non-UTF8 terminals.
const fn outcome_marker_ascii(
    outcome: &crate::checkpoint::execution_history::StepOutcome,
) -> &'static str {
    match outcome {
        crate::checkpoint::execution_history::StepOutcome::Success { .. } => "OK",
        crate::checkpoint::execution_history::StepOutcome::Failure { .. } => "FAIL",
        crate::checkpoint::execution_history::StepOutcome::Partial { .. } => "PART",
        crate::checkpoint::execution_history::StepOutcome::Skipped { .. } => "SKIP",
    }
}
