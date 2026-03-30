use crate::agents::JsonParserType;
use crate::config::Config;
use crate::logger::{Colors, Logger};
use crate::pipeline::Timer;

/// A single prompt-based agent invocation.
pub struct PromptCommand<'a> {
    pub label: &'a str,
    pub display_name: &'a str,
    pub cmd_str: &'a str,
    pub prompt: &'a str,
    /// Log prefix used for associating artifacts.
    ///
    /// Example: `.agent/logs/planning_1` (without extension).
    pub log_prefix: &'a str,
    /// Optional model fallback index for attribution.
    pub model_index: Option<usize>,
    /// Optional attempt counter for attribution.
    pub attempt: Option<u32>,
    pub logfile: &'a str,
    pub parser_type: JsonParserType,
    pub env_vars: &'a std::collections::HashMap<String, String>,
    /// Path to the file this phase is expected to produce.
    ///
    /// When set, the idle timeout monitor uses its existence as a
    /// "complete-but-waiting" signal: if the file exists and the process is
    /// idle, the process is killed and the phase advances as success.
    pub completion_output_path: Option<&'a std::path::Path>,
}

/// Runtime services required for running agent commands.
pub struct PipelineRuntime<'a> {
    pub timer: &'a mut Timer,
    pub logger: &'a Logger,
    pub colors: &'a Colors,
    pub config: &'a Config,
    /// Process executor for external process execution.
    pub executor: &'a dyn crate::executor::ProcessExecutor,
    /// Arc-wrapped executor for spawning into threads (e.g., idle timeout monitor).
    pub executor_arc: std::sync::Arc<dyn crate::executor::ProcessExecutor>,
    /// Workspace for file operations.
    pub workspace: &'a dyn crate::workspace::Workspace,
    /// Arc-wrapped workspace for spawning into threads (e.g., file activity monitor).
    pub workspace_arc: std::sync::Arc<dyn crate::workspace::Workspace>,
}

/// Options for saving a prompt to file and clipboard.
pub(super) struct PromptSaveOptions<'a> {
    /// Optional prompt archive info for observability.
    pub(super) archive_info: Option<PromptArchiveInfo<'a>>,
    /// Whether to copy to clipboard.
    pub(super) interactive: bool,
    /// Color configuration.
    pub(super) colors: Colors,
}

pub(super) struct PromptArchiveInfo<'a> {
    pub(super) phase_label: &'a str,
    pub(super) agent_name: &'a str,
    pub(super) log_prefix: &'a str,
    pub(super) model_index: Option<usize>,
    pub(super) attempt: Option<u32>,
}
