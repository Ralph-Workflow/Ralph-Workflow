// Pure domain logic for development prompt mode behavior selection
//
// The orchestrator decides WHICH mode to use based on state (Normal, Continuation,
// SameAgentRetry). This module contains pure helpers that derive the
// specific prompt construction strategy for each mode WITHOUT performing I/O.

use crate::reducer::state::PromptMode;

/// Execution path for building a development prompt.
///
/// The orchestrator pre-decides the PromptMode based on state.
/// This helper converts that mode into a specific execution path,
/// removing policy branching from the boundary layer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum DevelopmentPromptExecutionPath {
    /// Execute normal development prompt flow
    Normal,
    /// Execute continuation prompt flow with attempt context
    Continuation,
    /// Execute same-agent retry flow with prepended guidance
    SameAgentRetry,
}

/// Derive the execution path from the orchestrator-decided mode.
///
/// **Pure function**: Translates PromptMode policy decision into execution path.
/// Boundary uses returned path to dispatch to appropriate helper (no branching on mode).
pub(crate) fn derive_development_prompt_execution_path(
    prompt_mode: PromptMode,
) -> DevelopmentPromptExecutionPath {
    // Orchestrator already decided the mode based on state/retry/continuation logic.
    // This helper just converts the mode enum into an execution path enum,
    // keeping the boundary free of mode-based policy branching.
    match prompt_mode {
        PromptMode::Normal => DevelopmentPromptExecutionPath::Normal,
        PromptMode::Continuation => DevelopmentPromptExecutionPath::Continuation,
        PromptMode::SameAgentRetry => DevelopmentPromptExecutionPath::SameAgentRetry,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normal_mode_maps_to_normal_path() {
        let path = derive_development_prompt_execution_path(PromptMode::Normal);
        assert_eq!(path, DevelopmentPromptExecutionPath::Normal);
    }

    #[test]
    fn test_continuation_mode_maps_to_continuation_path() {
        let path = derive_development_prompt_execution_path(PromptMode::Continuation);
        assert_eq!(path, DevelopmentPromptExecutionPath::Continuation);
    }

    #[test]
    fn test_same_agent_retry_mode_maps_to_same_agent_retry_path() {
        let path = derive_development_prompt_execution_path(PromptMode::SameAgentRetry);
        assert_eq!(path, DevelopmentPromptExecutionPath::SameAgentRetry);
    }
}
