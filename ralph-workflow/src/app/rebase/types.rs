use crate::logger::{Colors, Logger};
use crate::prompts::template_context::TemplateContext;

/// Context for conflict resolution operations.
///
/// Groups together the configuration and runtime state needed for
/// AI-assisted conflict resolution during rebase operations.
pub struct ConflictResolutionContext<'a> {
    pub config: &'a crate::config::Config,
    pub registry: &'a crate::agents::AgentRegistry,
    pub template_context: &'a TemplateContext,
    pub logger: &'a Logger,
    pub colors: Colors,
    pub executor_arc: std::sync::Arc<dyn crate::executor::ProcessExecutor>,
    pub workspace: &'a dyn crate::workspace::Workspace,
    pub workspace_arc: std::sync::Arc<dyn crate::workspace::Workspace>,
}

/// Result type for conflict resolution attempts.
///
/// Represents the different ways conflict resolution can succeed or fail.
pub enum ConflictResolutionResult {
    /// Agent resolved conflicts by editing files directly (no JSON output)
    FileEditsOnly,
    /// Resolution failed completely
    Failed,
}

pub(crate) enum InitialRebaseOutcome {
    Succeeded { new_head: String },
    Skipped { reason: String },
}
