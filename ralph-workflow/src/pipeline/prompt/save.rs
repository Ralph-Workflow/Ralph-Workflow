use super::types::{PromptArchiveInfo, PromptSaveOptions};
use crate::logger::Logger;
use crate::Workspace;

use std::sync::atomic::{AtomicU64, Ordering};

static PROMPT_ARCHIVE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// Saves the prompt to a file, archives it, and optionally copies it to the clipboard.
///
/// # Arguments
///
/// * `prompt` - The prompt content to save
/// * `prompt_path` - Primary path for the prompt (e.g., `.agent/last_prompt.txt`)
/// * `options` - Options for archiving and clipboard behavior
/// * `logger` - Logger for status messages
/// * `executor` - Process executor for clipboard operations
/// * `workspace` - Workspace for file operations
///
/// # Archive Behavior
///
/// When `options.archive_info` is provided, the prompt is also saved to a unique timestamped
/// archive file in `.agent/prompts/`. This enables debugging by preserving each
/// prompt sent to each agent invocation, rather than overwriting a single file.
pub(super) fn save_prompt_to_file_and_clipboard(
    prompt: &str,
    prompt_path: &std::path::Path,
    options: PromptSaveOptions<'_>,
    logger: &Logger,
    executor: &dyn crate::executor::ProcessExecutor,
    workspace: &dyn Workspace,
) -> std::io::Result<()> {
    // Save prompt to primary location (existing behavior)
    workspace.write(prompt_path, prompt)?;
    logger.info(&format!(
        "Prompt saved to {}{}{}",
        options.colors.cyan(),
        prompt_path.display(),
        options.colors.reset()
    ));

    // Archive prompt with unique path for debugging
    if let Some(info) = options.archive_info {
        if let Err(e) = archive_prompt(prompt, &info, logger, workspace) {
            // Log but don't fail - archiving is for observability, not critical path
            logger.warn(&format!("Failed to archive prompt: {e}"));
        }
    }

    // Copy to clipboard if interactive
    if options.interactive {
        if let Some(clipboard_cmd) = super::super::clipboard::get_platform_clipboard_command() {
            match super::io_clipboard::copy_to_clipboard(executor, prompt, clipboard_cmd.clone()) {
                Ok(()) => {
                    logger.info(&format!(
                        "Prompt copied to clipboard {}({}){}",
                        options.colors.dim(),
                        clipboard_cmd.paste_hint,
                        options.colors.reset()
                    ));
                }
                Err(e) => {
                    logger.warn(&format!("Failed to copy to clipboard: {e}"));
                }
            }
        }
    }
    Ok(())
}

/// Archive a prompt to a unique timestamped file for debugging.
///
/// Prompts are archived to `.agent/prompts/{phase_iteration}_{agent}_{model_index}_a{attempt}_{timestamp}.txt`.
///
/// The archive filename is derived from structured components:
/// - `phase_iteration`: derived from `log_prefix` when possible (e.g., `planning_1`)
/// - `agent`: sanitized agent name (slashes replaced with hyphens)
/// - `model_index`: provided explicitly when known
/// - `attempt`: provided explicitly when known
/// - `timestamp`: milliseconds since UNIX epoch
///
/// This enables post-mortem debugging by preserving every prompt sent to every
/// agent invocation, even when the same agent is invoked multiple times.
fn archive_prompt(
    prompt: &str,
    info: &PromptArchiveInfo<'_>,
    logger: &Logger,
    workspace: &dyn Workspace,
) -> std::io::Result<()> {
    use std::path::PathBuf;

    let prompts_dir = PathBuf::from(".agent/prompts");
    workspace.create_dir_all(&prompts_dir)?;

    let timestamp = super::time::current_timestamp_ms();

    let archive_filename = build_prompt_archive_filename(
        info.phase_label,
        info.agent_name,
        info.log_prefix,
        info.model_index,
        info.attempt,
        timestamp,
    );
    let archive_path = prompts_dir.join(archive_filename);

    workspace.write(&archive_path, prompt)?;
    logger.info(&format!("Prompt archived to {}", archive_path.display()));

    Ok(())
}

pub(super) fn build_prompt_archive_filename(
    phase_label: &str,
    agent_name: &str,
    log_prefix: &str,
    model_index: Option<usize>,
    attempt: Option<u32>,
    timestamp_ms: u128,
) -> String {
    use crate::pipeline::logfile::sanitize_agent_name;
    use std::path::Path;

    let seq = PROMPT_ARCHIVE_SEQUENCE.fetch_add(1, Ordering::Relaxed);

    let safe_agent = sanitize_agent_name(&agent_name.to_lowercase());

    let prefix_part = Path::new(log_prefix)
        .file_name()
        .and_then(|s| s.to_str())
        .filter(|s| !s.is_empty())
        .map_or_else(
            || "unknown".to_string(),
            |s| sanitize_agent_name(&s.to_lowercase()),
        );

    let prefix_part =
        if prefix_part.is_empty() || prefix_part == "unknown" || prefix_part == safe_agent {
            sanitize_agent_name(&phase_label.to_lowercase())
        } else {
            prefix_part
        };

    let parts: Vec<String> = [
        Some(prefix_part),
        Some(safe_agent),
        model_index.map(|m| m.to_string()),
        attempt.map(|a| format!("a{a}")),
    ]
    .into_iter()
    .flatten()
    .collect();

    format!("{}_s{}_{}.txt", parts.join("_"), seq, timestamp_ms)
}
