//! Orchestration logic for determining next effect.
//!
//! This module implements the **pure orchestration layer** that derives effects from state.
//! The orchestrator is a critical component of the reducer architecture that bridges
//! state transitions with effect execution.
//!
//! # Pure Function Contract
//!
//! All orchestration functions are **PURE**:
//! - Input: `&PipelineState` (immutable reference to current state)
//! - Output: `Effect` (intention to perform side effects)
//! - No I/O operations (no filesystem, network, environment access)
//! - No side effects (no logging, no mutations, no hidden state)
//! - Deterministic: same state always produces same effect
//!
//! # Architecture Flow
//!
//! ```text
//! State → determine_next_effect() → Effect → Handler → Event → Reducer → State
//!         ^^^^^^^^^^^^^^^^^^^^^^
//!         Pure orchestration (this module)
//! ```
//!
//! The orchestrator examines state and derives the next effect:
//! 1. Check for pending recovery operations (continuation cleanup, loop recovery)
//! 2. Check for retry/fallback conditions (XSD retry, agent retry)
//! 3. Determine normal phase progression effect
//!
//! # Decision Priority
//!
//! Orchestration checks conditions in priority order:
//! 1. **Recovery**: Continuation cleanup, loop recovery
//! 2. **Retry**: Same-agent retry pending
//! 3. **Continuation**: Agent requested continuation
//! 4. **Normal**: Phase-specific progression
//! 5. **Transition**: Advance to next phase
//!
//! See `tests` module for comprehensive orchestration tests.

use super::event::{CheckpointTrigger, PipelinePhase, RebasePhase};
use super::state::{CommitState, DevelopmentStatus, PipelineState, PromptMode, RebaseState};

use crate::reducer::effect::{ContinuationContextData, Effect};

mod phase_effects;
use phase_effects::determine_next_effect_for_phase;

pub mod rules;

/// Compute an effect fingerprint for loop detection.
///
/// The fingerprint uniquely identifies the "work context" that would produce
/// an effect. If the same fingerprint appears consecutively many times, we're
/// likely in a tight loop.
///
/// The fingerprint includes:
/// - Current phase
/// - Current runtime drain
/// - Current drain mode
/// - Current iteration
/// - Current reviewer pass
///
/// Intentionally excludes retry counters so that repeated retries still
/// register as the "same effect" for tight-loop detection.
#[must_use]
pub fn compute_effect_fingerprint(state: &PipelineState) -> String {
    format!(
        "{}:{}:{}:iter={}:pass={}",
        state.phase,
        state.runtime_drain(),
        match state.agent_chain.current_mode {
            crate::agents::DrainMode::Normal => "normal",
            crate::agents::DrainMode::Continuation => "continuation",
            crate::agents::DrainMode::SameAgentRetry => "same-agent-retry",
        },
        state.iteration,
        state.reviewer_pass,
    )
}

fn review_phase_uses_fix_drain(state: &PipelineState) -> bool {
    state.runtime_drain() == crate::agents::AgentDrain::Fix
}

fn fix_drain_is_loaded(state: &PipelineState) -> bool {
    state.agent_chain.current_drain == crate::agents::AgentDrain::Fix
        || (state.agent_chain.current_mode == crate::agents::DrainMode::Continuation
            && state.agent_chain.agents.is_empty()
            && state.runtime_drain() == crate::agents::AgentDrain::Fix)
}

fn development_retry_uses_analysis_drain(state: &PipelineState) -> bool {
    analysis_drain_is_loaded(state)
        || state.analysis_agent_invoked_iteration == Some(state.iteration)
        || (state.development_agent_invoked_iteration == Some(state.iteration)
            && state.development_xml_extracted_iteration != Some(state.iteration)
            && state
                .development_validated_outcome
                .as_ref()
                .is_none_or(|outcome| outcome.iteration != state.iteration))
}

fn analysis_drain_is_loaded(state: &PipelineState) -> bool {
    state.agent_chain.current_drain == crate::agents::AgentDrain::Analysis
}

/// Derive the effect for writing timeout context to a temp file.
///
/// When a timeout with partial output occurs but the agent has no session ID,
/// we must extract the context from the logfile and write it to a temp file
/// before the same-agent retry prompt is prepared.
fn derive_timeout_context_write_effect(state: &PipelineState) -> Effect {
    let logfile_path = state
        .continuation
        .timeout_context_file_path
        .clone()
        .unwrap_or_else(|| ".agent/logs/unknown.log".to_string());

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
            // not the developer prompt chain, even if role metadata or drain metadata is stale.
            if development_retry_uses_analysis_drain(state) {
                if !analysis_drain_is_loaded(state) {
                    return Effect::InitializeAgentChain {
                        drain: crate::agents::AgentDrain::Analysis,
                    };
                }
                return Effect::InvokeAnalysisAgent {
                    iteration: state.iteration,
                };
            }

            Effect::PrepareDevelopmentPrompt {
                iteration: state.iteration,
                prompt_mode: PromptMode::SameAgentRetry,
            }
        }
        PipelinePhase::Review => {
            if review_phase_uses_fix_drain(state) {
                if !fix_drain_is_loaded(state) {
                    return Effect::InitializeAgentChain {
                        drain: crate::agents::AgentDrain::Fix,
                    };
                }
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
                    .unwrap_or(DevelopmentStatus::Failed);
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
        PipelinePhase::Review if review_phase_uses_fix_drain(state) => {
            if fix_drain_is_loaded(state) {
                Effect::PrepareFixPrompt {
                    pass: state.reviewer_pass,
                    prompt_mode: PromptMode::Continuation,
                }
            } else {
                Effect::InitializeAgentChain {
                    drain: crate::agents::AgentDrain::Fix,
                }
            }
        }
        // Other phases don't support continuation
        _ => Effect::SaveCheckpoint {
            trigger: CheckpointTrigger::PhaseTransition,
        },
    }
}

fn render_cloud_pr_title_and_body(state: &PipelineState) -> (String, String) {
    use std::collections::HashMap;

    let run_id = state.cloud.run_id.as_deref().unwrap_or("unknown");

    let prompt_summary = format!("Ralph workflow run {run_id}");

    let vars: HashMap<_, _> = [
        ("run_id", run_id.to_string()),
        ("prompt_summary", prompt_summary),
    ]
    .into_iter()
    .collect();

    let default_title = "Ralph workflow changes".to_string();

    let title = state
        .cloud
        .git_remote
        .pr_title_template
        .as_deref()
        .and_then(|t| try_render_cloud_pr_template(t, &vars))
        .unwrap_or(default_title);

    let body = state
        .cloud
        .git_remote
        .pr_body_template
        .as_deref()
        .and_then(|t| try_render_cloud_pr_template(t, &vars))
        .unwrap_or_default();

    (title, body)
}

fn try_render_cloud_pr_template(
    template: &str,
    vars: &std::collections::HashMap<&str, String>,
) -> Option<String> {
    let converted = convert_cloud_pr_template_placeholders(template)?;

    let partials: std::collections::HashMap<String, String> = std::iter::empty().collect();
    let t = crate::prompts::template_engine::Template::new(&converted);
    t.render_with_partials(vars, &partials).ok()
}

fn convert_cloud_pr_template_placeholders(input: &str) -> Option<String> {
    const ALLOWED: [&str; 2] = ["run_id", "prompt_summary"];

    fn parse_char_by_char(chars: &[char], pos: usize) -> Option<String> {
        if pos >= chars.len() {
            return Some(String::new());
        }

        let ch = chars[pos];

        if ch == '}' {
            return parse_char_by_char(chars, pos + 1);
        }

        if ch == '{' && pos + 1 < chars.len() && chars[pos + 1] == '{' {
            return Some(String::from("{") + &parse_char_by_char(chars, pos + 2)?);
        }

        if ch == '{' {
            let name_end = chars[pos..]
                .iter()
                .position(|&c| c == '}')
                .map(|offset| pos + offset);

            let (name, end): (String, usize) = match name_end {
                Some(end) => (chars[pos + 1..end].iter().collect(), end + 1),
                None => {
                    return Some(format!(
                        "{{{rest}",
                        rest = parse_char_by_char(chars, pos + 1)?
                    ));
                }
            };

            let trimmed = name.trim();
            let replacement = if is_simple_placeholder_name(trimmed) && ALLOWED.contains(&trimmed) {
                format!("{{{{{trimmed}}}}}")
            } else if is_simple_placeholder_name(trimmed) {
                return None;
            } else {
                format!("{{{name}}}")
            };
            return Some(format!(
                "{replacement}{rest}",
                rest = parse_char_by_char(chars, end)?
            ));
        }

        Some(format!(
            "{ch}{rest}",
            rest = parse_char_by_char(chars, pos + 1)?
        ))
    }

    let chars: Vec<char> = input.chars().collect();
    parse_char_by_char(&chars, 0)
}

fn is_simple_placeholder_name(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Determine the next effect to execute based on current state.
///
/// This function is pure - it only reads state and returns an effect.
/// The actual execution happens in the effect handler.
///
/// # Priority Order for Effects
///
/// 1. Continuation context cleanup (highest priority)
/// 2. Same-agent retry pending (timeout/internal error, retry same agent)
/// 3. Continue pending (output valid but incomplete, new session)
/// 4. Rebase in progress
/// 5. Agent chain exhausted
/// 6. Backoff wait
/// 7. Phase-specific effects
#[must_use]
pub fn determine_next_effect(state: &PipelineState) -> Effect {
    // Terminal: once aborted, drive a single checkpoint save so the event loop can
    // deterministically complete (Interrupted + checkpoint_saved_count > 0).
    if state.phase == PipelinePhase::Interrupted && state.checkpoint_saved_count == 0 {
        // BUT: if restoration is pending, do that FIRST before termination effects.
        if state.prompt_permissions.restore_needed && !state.prompt_permissions.restored {
            return Effect::RestorePromptPermissions;
        }

        return determine_next_effect_for_phase(state);
    }

    // Startup: Lock PROMPT.md permissions before any work (best-effort protection)
    if !state.prompt_permissions.locked {
        return Effect::LockPromptPermissions;
    }

    // Loop detection: check if the same effect has been derived too many times consecutively.
    let effect_fingerprint = compute_effect_fingerprint(state);
    let loop_detected = state
        .continuation
        .last_effect_kind
        .as_deref()
        .is_some_and(|last| last == effect_fingerprint)
        && state.continuation.consecutive_same_effect_count
            >= state.continuation.max_consecutive_same_effect;

    if loop_detected
        && !matches!(
            state.phase,
            PipelinePhase::Complete | PipelinePhase::Interrupted
        )
    {
        // MANDATORY RECOVERY: we're in a tight loop and not in a terminal phase
        return Effect::TriggerLoopRecovery {
            detected_loop: effect_fingerprint,
            loop_count: state.continuation.consecutive_same_effect_count,
        };
    }

    // Priority 2: Connectivity verification check.
    if state.connectivity.check_pending {
        return Effect::CheckNetworkConnectivity;
    }

    // Priority 3: Offline polling — pipeline is frozen while offline.
    if state.connectivity.is_offline && state.connectivity.poll_pending {
        return Effect::PollForConnectivity {
            interval_ms: state.connectivity.offline_poll_interval_ms,
        };
    }

    if state.continuation.context_cleanup_pending {
        return Effect::CleanupContinuationContext;
    }

    // Timeout context write: when a timeout with partial output occurs but the agent has no
    // session ID, we must extract the context from the logfile and write it to a temp file
    // BEFORE the same-agent retry prompt is prepared.
    if state.continuation.timeout_context_write_pending {
        return derive_timeout_context_write_effect(state);
    }

    // Same-agent retry: invocation failed (timeout/internal error), retry same agent with
    // retry-specific prompt guidance.
    if state.continuation.same_agent_retry_pending {
        if state.continuation.same_agent_retries_exhausted() {
            debug_assert!(
                false,
                "Unexpected state: same_agent_retry_pending=true but same_agent_retries_exhausted()=true. \
                 The reducer should have cleared same_agent_retry_pending when retries exhausted. \
                 same_agent_retry_count={}, max_same_agent_retry_count={}",
                state.continuation.same_agent_retry_count,
                state.continuation.max_same_agent_retry_count
            );
        } else {
            return derive_same_agent_retry_effect(state);
        }
    }

    // Continuation is drain-local runtime state. Only the active drain may consume its
    // pending continuation flag; stale compatibility flags for other drains must not hijack
    // orchestration before phase-specific effects re-establish the right drain.
    let active_drain = state.runtime_drain();
    if state
        .continuation
        .pending_continuation_for_drain(active_drain)
    {
        if state
            .continuation
            .continuation_exhausted_for_drain(active_drain)
        {
            // Exhausted continuation budget - proceed to normal phase-specific effects.
            // Budget exhaustion is handled in state reduction.
        } else {
            return derive_continuation_effect(state);
        }
    }

    if matches!(
        state.rebase,
        RebaseState::InProgress { .. } | RebaseState::Conflicted { .. }
    ) {
        let phase = match state.phase {
            PipelinePhase::Planning => RebasePhase::Initial,
            _ => RebasePhase::PostReview,
        };

        return match &state.rebase {
            RebaseState::InProgress { target_branch, .. } => Effect::RunRebase {
                phase,
                target_branch: target_branch.clone(),
            },
            RebaseState::Conflicted { .. } => Effect::ResolveRebaseConflicts {
                strategy: super::event::ConflictStrategy::Continue,
            },
            _ => unreachable!("checked rebase state before matching"),
        };
    }

    if !state.agent_chain.agents.is_empty() && state.agent_chain.is_exhausted() {
        let progressed = match state.phase {
            PipelinePhase::Planning | PipelinePhase::Development => state.iteration > 0,
            PipelinePhase::Review => state.reviewer_pass > 0,
            PipelinePhase::CommitMessage => matches!(
                state.commit,
                CommitState::Generated { .. }
                    | CommitState::Committed { .. }
                    | CommitState::Skipped
            ),
            PipelinePhase::FinalValidation
            | PipelinePhase::Finalizing
            | PipelinePhase::Complete
            | PipelinePhase::AwaitingDevFix
            | PipelinePhase::Interrupted => false,
        };

        if progressed
            && state.checkpoint_saved_count == 0
            && !matches!(
                state.phase,
                PipelinePhase::Complete
                    | PipelinePhase::Interrupted
                    | PipelinePhase::AwaitingDevFix
            )
        {
            return Effect::SaveCheckpoint {
                trigger: CheckpointTrigger::Interrupt,
            };
        }

        if matches!(state.phase, PipelinePhase::AwaitingDevFix) {
            // Fall through to determine_next_effect_for_phase
        } else {
            return Effect::ReportAgentChainExhausted {
                role: state.agent_chain.current_drain.role(),
                phase: state.phase,
                cycle: state.agent_chain.retry_cycle,
            };
        }
    }

    if let Some(duration_ms) = state.agent_chain.backoff_pending_ms {
        return Effect::BackoffWait {
            role: state.agent_chain.current_drain.role(),
            cycle: state.agent_chain.retry_cycle,
            duration_ms,
        };
    }

    // Cloud mode orchestration
    if state.cloud.enabled {
        if let Some(commit_sha) = &state.pending_push_commit {
            if !state.git_auth_configured {
                let auth_method = match &state.cloud.git_remote.auth_method {
                    crate::config::GitAuthStateMethod::SshKey { key_path } => key_path
                        .as_ref()
                        .map_or_else(|| "ssh-key:default".to_string(), |p| format!("ssh-key:{p}")),
                    crate::config::GitAuthStateMethod::Token { username } => {
                        format!("token:{username}")
                    }
                    crate::config::GitAuthStateMethod::CredentialHelper { helper } => {
                        format!("credential-helper:{helper}")
                    }
                };
                return Effect::ConfigureGitAuth { auth_method };
            }

            if state.cloud.git_remote.push_branch.is_empty() {
                return Effect::EmitCompletionMarkerAndTerminate {
                    is_failure: true,
                    reason: Some(
                        "Cloud mode is enabled but no push branch was resolved".to_string(),
                    ),
                };
            }
            return Effect::PushToRemote {
                remote: state.cloud.git_remote.remote_name.clone(),
                branch: state.cloud.git_remote.push_branch.clone(),
                force: state.cloud.git_remote.force_push,
                commit_sha: commit_sha.clone(),
            };
        }

        if state.phase == PipelinePhase::Finalizing
            && state.cloud.git_remote.create_pr
            && !state.pr_created
        {
            if state.metrics.commits_created_total == 0 {
                // Fall through to normal phase effects (finalization/cleanup).
            } else {
                if !state.unpushed_commits.is_empty()
                    || state.push_count == 0
                    || state.last_pushed_commit.is_none()
                {
                    return Effect::EmitCompletionMarkerAndTerminate {
                        is_failure: true,
                        reason: Some(
                            "Cannot create PR because required pushes did not succeed (unpushed commits remain)"
                                .to_string(),
                        ),
                    };
                }

                if state.cloud.git_remote.push_branch.is_empty() {
                    return Effect::EmitCompletionMarkerAndTerminate {
                        is_failure: true,
                        reason: Some(
                            "Cloud mode is enabled but no PR head branch was resolved".to_string(),
                        ),
                    };
                }
                let (title, body) = render_cloud_pr_title_and_body(state);
                return Effect::CreatePullRequest {
                    base_branch: state
                        .cloud
                        .git_remote
                        .pr_base_branch
                        .clone()
                        .unwrap_or_else(|| "main".to_string()),
                    head_branch: state.cloud.git_remote.push_branch.clone(),
                    title,
                    body,
                };
            }
        }
    }

    // Recovery completion: if the pipeline entered recovery due to a commit failure,
    // only clear recovery state AFTER CreateCommit has succeeded.
    if state.dev_fix_attempt_count > 0
        && state.recovery_escalation_level > 0
        && state.failed_phase_for_recovery == Some(PipelinePhase::CommitMessage)
        && matches!(
            state.commit,
            CommitState::Committed { .. } | CommitState::Skipped
        )
    {
        return Effect::EmitRecoverySuccess {
            level: state.recovery_escalation_level,
            total_attempts: state.dev_fix_attempt_count,
        };
    }

    determine_next_effect_for_phase(state)
}

/// Returns true if recovery state is active (dev-fix occurred and we transitioned back).
///
/// Recovery state is considered active when:
/// - `dev_fix_attempt_count` > 0 (at least one recovery attempt)
/// - `recovery_escalation_level` > 0 (escalation level set)
/// - `previous_phase` is `AwaitingDevFix` (just transitioned back from recovery)
///
/// When recovery state is active and a phase completes successfully (e.g., Planning
/// validates, Development completes), the orchestration should emit `RecoverySucceeded`
/// to clear the recovery tracking fields and resume normal operation.
pub(in crate::reducer::orchestration) const fn is_recovery_state_active(
    state: &PipelineState,
) -> bool {
    state.dev_fix_attempt_count > 0
        && state.recovery_escalation_level > 0
        && matches!(state.previous_phase, Some(PipelinePhase::AwaitingDevFix))
}

#[cfg(test)]
#[path = "orchestration/io_tests/mod.rs"]
mod tests;
