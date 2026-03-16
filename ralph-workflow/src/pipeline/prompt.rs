//! Prompt-based command execution.

mod agent_spawn;

#[cfg(test)]
mod agent_spawn_test;

mod cleanup;
mod environment;
mod process_wait;
mod run;
mod save;
mod stderr_collector;
mod streaming;
mod streaming_line_reader;
mod types;

pub use run::run_with_prompt;
pub use streaming::extract_error_identifier_from_logfile;
pub use streaming::extract_error_message_from_logfile;
pub use types::{PipelineRuntime, PromptCommand};

/// Exit code returned when a process is killed due to SIGTERM.
const SIGTERM_EXIT_CODE: i32 = 143;

#[cfg(test)]
pub use agent_spawn_test::run_with_agent_spawn_with_monitor_config;

#[cfg(test)]
use agent_spawn::run_with_agent_spawn;

#[cfg(test)]
use crate::agents::JsonParserType;

#[cfg(test)]
use stderr_collector::collect_stderr_with_cap_and_drain;

#[cfg(test)]
use save::build_prompt_archive_filename;

#[cfg(test)]
use streaming_line_reader::StreamingLineReader;

#[cfg(test)]
use streaming_line_reader::MAX_BUFFER_SIZE;

#[cfg(test)]
use crate::config::Config;

#[cfg(test)]
use crate::logger::{Colors, Logger};

#[cfg(test)]
use crate::pipeline::Timer;

#[cfg(test)]
use std::io::BufRead;

/// Maximum safe prompt size in bytes for command-line arguments.
///
/// The OS has a limit on total argument size (`ARG_MAX`), typically:
/// - Linux: 2MB (but often limited to 128KB per argument)
/// - macOS: ~1MB
/// - Windows: 32KB
///
/// We use a conservative limit of 200KB to:
/// - Leave room for other arguments and environment variables
/// - Work safely across all platforms
/// - Avoid E2BIG (Argument list too long) errors at spawn time
#[cfg(test)]
const MAX_PROMPT_SIZE: usize = 200 * 1024; // 200KB

/// Truncate a prompt that exceeds the safe size limit.
///
/// Returns the original prompt if within limits, or a truncated version with a marker.
#[cfg(test)]
fn truncate_prompt_if_needed(prompt: &str, logger: &Logger) -> String {
    if prompt.len() <= MAX_PROMPT_SIZE {
        return prompt.to_string();
    }

    let excess = prompt.len() - MAX_PROMPT_SIZE;
    logger.warn(&format!(
        "Prompt exceeds safe limit ({} bytes > {} bytes), truncating {} bytes",
        prompt.len(),
        MAX_PROMPT_SIZE,
        excess
    ));

    let truncation_markers = [
        "\n---\n",
        "\n```\n",
        "\n<last-output>",
        "\nPrevious output:",
    ];

    for marker in truncation_markers {
        if let Some(marker_pos) = prompt.find(marker) {
            let content_start = marker_pos + marker.len();
            if content_start < prompt.len() {
                let before_marker = &prompt[..content_start];
                let after_marker = &prompt[content_start..];

                if after_marker.len() > excess + 100 {
                    let keep_from = excess + 100;
                    let truncated_content = &after_marker[keep_from..];
                    let clean_start = truncated_content.find('\n').map_or(0, |i| i + 1);

                    return format!(
                        "{}\n[... {} bytes truncated to fit CLI argument limit ...]\n{}",
                        before_marker,
                        keep_from + clean_start,
                        &truncated_content[clean_start..]
                    );
                }
            }
        }
    }

    let keep_start = MAX_PROMPT_SIZE / 3;
    let keep_end = MAX_PROMPT_SIZE / 3;
    let start_part = &prompt[..keep_start];
    let end_part = &prompt[prompt.len() - keep_end..];

    let start_end = start_part.rfind('\n').map_or(keep_start, |i| i + 1);
    let end_start = end_part.find('\n').map_or(0, |i| i + 1);

    format!(
        "{}\n\n[... {} bytes truncated to fit CLI argument limit ...]\n\n{}",
        &prompt[..start_end],
        prompt.len() - start_end - (keep_end - end_start),
        &end_part[end_start..]
    )
}

#[cfg(test)]
mod tests;

#[cfg(test)]
mod sanitize_env_tests;
