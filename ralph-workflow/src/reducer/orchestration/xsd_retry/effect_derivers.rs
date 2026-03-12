/// Compute an effect fingerprint for loop detection.
///
/// The fingerprint uniquely identifies the "work context" that would produce
/// an effect. If the same fingerprint appears consecutively many times, we're
/// likely in a tight loop.
///
/// The fingerprint includes:
/// - Current phase
/// - Current agent role
/// - Current iteration
/// - Current reviewer pass
/// - XSD retry pending flag
///
/// Intentionally excludes retry counters like `xsd_retry_count` so that repeated
/// retries still register as the "same effect" for tight-loop detection.
#[must_use]
pub fn compute_effect_fingerprint(state: &PipelineState) -> String {
    format!(
        "{}:{}:{}:iter={}:pass={}:xsd_retry={}",
        state.phase,
        state.agent_chain.current_drain,
        match state.agent_chain.current_mode {
            crate::agents::DrainMode::Normal => "normal",
            crate::agents::DrainMode::Continuation => "continuation",
            crate::agents::DrainMode::SameAgentRetry => "same-agent-retry",
            crate::agents::DrainMode::XsdRetry => "xsd-retry",
        },
        state.iteration,
        state.reviewer_pass,
        state.continuation.xsd_retry_pending
    )
}

#[cfg(test)]
mod xsd_retry_fingerprint_tests {
    use super::compute_effect_fingerprint;
    use crate::agents::AgentRole;
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::state::PipelineState;

    #[test]
    fn test_effect_fingerprint_ignores_xsd_retry_count() {
        let mut state = PipelineState::initial(1, 1);
        state.phase = PipelinePhase::Development;
        state.agent_chain.current_role = AgentRole::Developer;
        state.iteration = 1;
        state.reviewer_pass = 0;
        state.continuation.xsd_retry_pending = true;

        state.continuation.xsd_retry_count = 1;
        let fp1 = compute_effect_fingerprint(&state);
        state.continuation.xsd_retry_count = 2;
        let fp2 = compute_effect_fingerprint(&state);

        assert_eq!(fp1, fp2);
    }
}

fn review_phase_uses_fix_drain(state: &PipelineState) -> bool {
    state.agent_chain.current_drain == crate::agents::AgentDrain::Fix
}

/// Derive the effect for XSD retry based on current phase.
///
/// XSD retry reuses the same agent and session if available.
/// Returns the appropriate phase-specific effect with retry context.
fn derive_xsd_retry_effect(state: &PipelineState) -> Effect {
    match state.phase {
        PipelinePhase::Planning => Effect::PreparePlanningPrompt {
            iteration: state.iteration,
            prompt_mode: PromptMode::XsdRetry,
        },
        PipelinePhase::Development => {
            // development_result.xml is produced by the analysis agent.
            // When XSD validation fails, retry analysis output generation directly.
            // Ensure the analysis agent chain role is initialized (resume safety).
            if state.agent_chain.current_drain != crate::agents::AgentDrain::Analysis {
                return Effect::InitializeAgentChain {
                    drain: crate::agents::AgentDrain::Analysis,
                };
            }
            Effect::InvokeAnalysisAgent {
                iteration: state.iteration,
            }
        }
        PipelinePhase::Review => {
            if review_phase_uses_fix_drain(state) {
                Effect::PrepareFixPrompt {
                    pass: state.reviewer_pass,
                    prompt_mode: PromptMode::XsdRetry,
                }
            } else {
                Effect::PrepareReviewPrompt {
                    pass: state.reviewer_pass,
                    prompt_mode: PromptMode::XsdRetry,
                }
            }
        }
        PipelinePhase::CommitMessage => Effect::PrepareCommitPrompt {
            prompt_mode: PromptMode::XsdRetry,
        },
        // Other phases don't have XSD retry
        _ => Effect::SaveCheckpoint {
            trigger: CheckpointTrigger::PhaseTransition,
        },
    }
}

/// Derive the effect for writing timeout context to a temp file.
///
/// When a timeout with partial output occurs but the agent has no session ID,
/// we must extract the context from the logfile and write it to a temp file
/// before the same-agent retry prompt is prepared.
///
/// This function creates a `WriteTimeoutContext` effect that:
/// 1. Reads the logfile content
/// 2. Writes it to a temp file (e.g., `.agent/tmp/timeout_context_1.txt`)
fn derive_timeout_context_write_effect(state: &PipelineState) -> Effect {
    // Get the logfile path from continuation state (set by reducer during TimedOut processing)
    let logfile_path = state
        .continuation
        .timeout_context_file_path
        .clone()
        .unwrap_or_else(|| ".agent/logs/unknown.log".to_string());

    // Generate a context file path based on the retry attempt
    let context_path = format!(
        ".agent/tmp/timeout_context_{}.txt",
        state.continuation.same_agent_retry_count
    );

    Effect::WriteTimeoutContext {
        role: state.agent_chain.current_drain.role(),
        logfile_path,
        context_path,
    }
}

/// Derive the effect for same-agent retry based on current phase.
///
/// Same-agent retry starts a new invocation with the same agent (no session reuse),
/// but uses a different prompt mode to provide retry-specific guidance.
fn derive_same_agent_retry_effect(state: &PipelineState) -> Effect {
    match state.phase {
        PipelinePhase::Planning => Effect::PreparePlanningPrompt {
            iteration: state.iteration,
            prompt_mode: PromptMode::SameAgentRetry,
        },
        PipelinePhase::Development => {
            // Development phase runs BOTH developer and analysis agents.
            // Same-agent retries must be drain-aware so analysis failures retry analysis,
            // not the developer prompt chain, even if role metadata is stale.
            if state.agent_chain.current_drain == crate::agents::AgentDrain::Analysis {
                Effect::InvokeAnalysisAgent {
                    iteration: state.iteration,
                }
            } else {
                Effect::PrepareDevelopmentPrompt {
                    iteration: state.iteration,
                    prompt_mode: PromptMode::SameAgentRetry,
                }
            }
        }
        PipelinePhase::Review => {
            if review_phase_uses_fix_drain(state) {
                Effect::PrepareFixPrompt {
                    pass: state.reviewer_pass,
                    prompt_mode: PromptMode::SameAgentRetry,
                }
            } else {
                Effect::PrepareReviewPrompt {
                    pass: state.reviewer_pass,
                    prompt_mode: PromptMode::SameAgentRetry,
                }
            }
        }
        PipelinePhase::CommitMessage => Effect::PrepareCommitPrompt {
            prompt_mode: PromptMode::SameAgentRetry,
        },
        _ => Effect::SaveCheckpoint {
            trigger: CheckpointTrigger::PhaseTransition,
        },
    }
}

/// Derive the effect for continuation based on current phase.
///
/// Continuation starts a new session (agent starts fresh but with context).
/// Only applies to Development and Fix phases where incomplete work can continue.
fn derive_continuation_effect(state: &PipelineState) -> Effect {
    match state.phase {
        PipelinePhase::Development => {
            // Write continuation context first if needed
            if state.continuation.context_write_pending {
                let status = state
                    .continuation
                    .previous_status
                    .unwrap_or(super::state::DevelopmentStatus::Failed);
                let summary = state
                    .continuation
                    .previous_summary
                    .clone()
                    .unwrap_or_default();
                let files_changed = state.continuation.previous_files_changed.clone();
                let next_steps = state.continuation.previous_next_steps.clone();

                Effect::WriteContinuationContext(ContinuationContextData {
                    iteration: state.iteration,
                    attempt: state.continuation.continuation_attempt,
                    status,
                    summary,
                    files_changed,
                    next_steps,
                })
            } else {
                Effect::PrepareDevelopmentContext {
                    iteration: state.iteration,
                }
            }
        }
        // Fix continuation: start the fix chain with a fresh session
        PipelinePhase::Review if review_phase_uses_fix_drain(state) => Effect::PrepareFixPrompt {
            pass: state.reviewer_pass,
            prompt_mode: PromptMode::Normal,
        },
        // Other phases don't support continuation
        _ => Effect::SaveCheckpoint {
            trigger: CheckpointTrigger::PhaseTransition,
        },
    }
}
