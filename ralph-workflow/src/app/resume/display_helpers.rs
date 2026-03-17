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

    let parts: Vec<String> = std::iter::once("ralph".to_string())
        .chain((cli.developer_iters > 0).then(|| format!("-D {}", cli.developer_iters)))
        .chain((cli.reviewer_reviews > 0).then(|| format!("-R {}", cli.reviewer_reviews)))
        .chain(
            cli.review_depth
                .as_ref()
                .map(|depth| format!("--review-depth {depth}")),
        )
        .chain((!cli.isolation_mode).then(|| "--no-isolation".to_string()))
        .chain(match cli.verbosity {
            0 => Some("--quiet".to_string()),
            2 => Some("--verbose".to_string()),
            3 => Some("--full".to_string()),
            4 => Some("--debug".to_string()),
            _ => None,
        })
        .chain(
            cli.show_streaming_metrics
                .then(|| "--show-streaming-metrics".to_string()),
        )
        .chain(
            cli.reviewer_json_parser
                .as_ref()
                .map(|parser| format!("--reviewer-json-parser {parser}")),
        )
        .chain(std::iter::once(format!(
            "--agent {}",
            checkpoint.developer_agent
        )))
        .chain(std::iter::once(format!(
            "--reviewer-agent {}",
            checkpoint.reviewer_agent
        )))
        .chain(
            checkpoint
                .developer_agent_config
                .model_override
                .as_ref()
                .map(|model| format!("--model \"{model}\"")),
        )
        .chain(
            checkpoint
                .reviewer_agent_config
                .model_override
                .as_ref()
                .map(|model| format!("--reviewer-model \"{model}\"")),
        )
        .chain(
            checkpoint
                .developer_agent_config
                .provider_override
                .as_ref()
                .map(|provider| format!("--provider \"{provider}\"")),
        )
        .chain(
            checkpoint
                .reviewer_agent_config
                .provider_override
                .as_ref()
                .map(|provider| format!("--reviewer-provider \"{provider}\"")),
        )
        .collect();

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
            let context: Vec<String> = std::iter::once("resume from interrupted state".to_string())
                .chain((checkpoint.iteration > 0).then(|| {
                    format!(
                        "(development iteration {}/{})",
                        checkpoint.iteration, checkpoint.total_iterations
                    )
                }))
                .chain((checkpoint.reviewer_pass > 0).then(|| {
                    format!(
                        "(review pass {}/{})",
                        checkpoint.reviewer_pass, checkpoint.total_reviewer_passes
                    )
                }))
                .chain(std::iter::once(
                    "full pipeline will run from interrupted point".to_string(),
                ))
                .collect();
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

    let bar: String = (0..width)
        .map(|i| if i < filled { '=' } else { '-' })
        .collect();

    let percentage = current_clamped
        .saturating_mul(100)
        .saturating_add(total / 2)
        .checked_div(total)
        .unwrap_or(0);
    format!("[{bar}] {percentage}%")
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
