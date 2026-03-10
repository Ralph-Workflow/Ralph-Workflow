use crate::pipeline::types::CommandResult;
use std::io;

use super::types::{PipelineRuntime, PromptArchiveInfo, PromptCommand, PromptSaveOptions};

/// Run a command with a prompt argument.
///
/// This is an internal helper for `run_with_fallback`.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn run_with_prompt(
    cmd: &PromptCommand<'_>,
    runtime: &mut PipelineRuntime<'_>,
) -> io::Result<CommandResult> {
    const ANTHROPIC_ENV_VARS_TO_SANITIZE: &[&str] = &[
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
    ];

    // Verify commit protections are intact before each agent run.
    // This self-heals against a prior agent that deleted .no_agent_commit or git hooks.
    let protection_result = crate::git_helpers::ensure_agent_phase_protections(runtime.logger);
    if protection_result.tampering_detected {
        runtime.logger.warn(&format!(
            "WARNING: Agent tampered with git protections (self-healed): {}",
            protection_result.details.join("; ")
        ));
    }

    runtime.timer.start_phase();
    runtime.logger.step(&format!(
        "{}{}{}",
        runtime.colors.bold(),
        cmd.label,
        runtime.colors.reset()
    ));

    let options = PromptSaveOptions {
        archive_info: Some(PromptArchiveInfo {
            phase_label: cmd.label,
            agent_name: cmd.display_name,
            log_prefix: cmd.log_prefix,
            model_index: cmd.model_index,
            attempt: cmd.attempt,
        }),
        interactive: runtime.config.behavior.interactive,
        colors: *runtime.colors,
    };

    super::save::save_prompt_to_file_and_clipboard(
        cmd.prompt,
        &runtime.config.prompt_path,
        options,
        runtime.logger,
        runtime.executor,
        runtime.workspace,
    )?;

    super::agent_spawn::run_with_agent_spawn(cmd, runtime, ANTHROPIC_ENV_VARS_TO_SANITIZE)
}
