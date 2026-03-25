// NOTE: split from reducer/state_reduction.rs.

use crate::agents::DrainMode;
use crate::reducer::event::{AgentErrorKind, AgentEvent, PipelinePhase, TimeoutOutputKind};
use crate::reducer::state::{ContinuationState, PipelineState, SameAgentRetryReason};

pub(super) fn reduce_agent_event(state: PipelineState, event: AgentEvent) -> PipelineState {
    match event {
        // Do NOT clear any saved continuation prompt on invocation start.
        //
        // Rationale: after a 429, we preserve prompt context so the next agent can continue the
        // same work. If the first post-rate-limit invocation fails (e.g., timeout/internal), we
        // must keep the continuation prompt available for retries until an invocation succeeds.
        AgentEvent::InvocationStarted { .. } => PipelineState {
            continuation: ContinuationState {
                xsd_retry_session_reuse_pending: false,
                ..state.continuation
            },
            ..state
        },
        // Clear continuation prompt and failure reason on success
        AgentEvent::InvocationSucceeded { .. } => PipelineState {
            agent_chain: state
                .agent_chain
                .clear_continuation_prompt()
                .with_mode(DrainMode::Normal),
            continuation: ContinuationState {
                same_agent_retry_count: 0,
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                xsd_retry_session_reuse_pending: false,
                ..state.continuation
            },
            ..state
        },
        // Rate limit (429): immediate agent switch, preserve prompt context.
        AgentEvent::RateLimited {
            role: _,
            prompt_context,
            ..
        } => {
            let state = reset_phase_xml_cleanup_for_retry(state);
            let active_role = state.agent_chain.current_drain.role();
            PipelineState {
                agent_chain: state
                    .agent_chain
                    .switch_to_next_agent_with_prompt_for_role(active_role, prompt_context)
                    .clear_session_id()
                    .with_mode(DrainMode::Normal)
                    .with_failure_reason(Some("rate-limited".to_string())),
                continuation: ContinuationState {
                    xsd_retry_count: 0,
                    xsd_retry_pending: false,
                    xsd_retry_session_reuse_pending: false,
                    same_agent_retry_count: 0,
                    same_agent_retry_pending: false,
                    same_agent_retry_reason: None,
                    ..state.continuation
                },
                ..state
            }
        }
        // Auth failure (401/403): immediate agent switch, clear session and prompt context.
        AgentEvent::AuthFailed { .. } => {
            let state = reset_phase_xml_cleanup_for_retry(state);
            PipelineState {
                agent_chain: state
                    .agent_chain
                    .switch_to_next_agent()
                    .clear_session_id()
                    .clear_continuation_prompt()
                    .with_mode(DrainMode::Normal)
                    .with_failure_reason(Some("auth failed".to_string())),
                continuation: ContinuationState {
                    xsd_retry_count: 0,
                    xsd_retry_pending: false,
                    xsd_retry_session_reuse_pending: false,
                    same_agent_retry_count: 0,
                    same_agent_retry_pending: false,
                    same_agent_retry_reason: None,
                    ..state.continuation
                },
                ..state
            }
        }
        // Capability denied: immediate agent switch, clear session and prompt context.
        // This indicates a misconfiguration — session capabilities don't match the effects
        // the orchestrator is trying to execute. Try the next agent.
        AgentEvent::CapabilityDenied {
            role: _,
            capability,
            reason: _,
        } => {
            let state = reset_phase_xml_cleanup_for_retry(state);
            PipelineState {
                agent_chain: state
                    .agent_chain
                    .switch_to_next_agent()
                    .clear_session_id()
                    .clear_continuation_prompt()
                    .with_mode(DrainMode::Normal)
                    .with_failure_reason(Some(format!("capability denied: {}", capability))),
                continuation: ContinuationState {
                    xsd_retry_count: 0,
                    xsd_retry_pending: false,
                    xsd_retry_session_reuse_pending: false,
                    same_agent_retry_count: 0,
                    same_agent_retry_pending: false,
                    same_agent_retry_reason: None,
                    ..state.continuation
                },
                ..state
            }
        }
        // Timeout with no output: immediate agent switch (no same-agent retry)
        // The agent produced no output at all — likely overloaded or unavailable.
        // Switching agents immediately is safer than retrying the same agent.
        AgentEvent::TimedOut {
            output_kind: TimeoutOutputKind::NoOutput,
            ..
        } => {
            let state = reset_phase_xml_cleanup_for_retry(state);
            PipelineState {
                agent_chain: state
                    .agent_chain
                    .switch_to_next_agent()
                    .clear_session_id()
                    .with_mode(DrainMode::Normal)
                    .with_failure_reason(Some("timed out (no output)".to_string())),
                continuation: ContinuationState {
                    xsd_retry_count: 0,
                    xsd_retry_pending: false,
                    xsd_retry_session_reuse_pending: false,
                    same_agent_retry_count: 0,
                    same_agent_retry_pending: false,
                    same_agent_retry_reason: None,
                    ..state.continuation
                },
                metrics: state
                    .metrics
                    .increment_timeout_no_output_agent_switches_total(),
                ..state
            }
        }
        // Timeout with partial output: retry same agent with context preservation
        // The agent produced partial output before timing out — likely a connectivity issue.
        // Retry the same agent first; fall back only after retry budget exhaustion.
        // Context should be preserved (session reuse or context file extraction).
        AgentEvent::TimedOut {
            output_kind: TimeoutOutputKind::PartialOutput,
            logfile_path,
            ..
        } => reduce_same_agent_retryable_failure(
            state,
            SameAgentRetryableFailure::TimeoutWithContext,
            logfile_path,
        ),
        // Other retriable errors (Network, ModelUnavailable): try next model
        AgentEvent::InvocationFailed {
            retriable: true, ..
        } => {
            let state = reset_phase_xml_cleanup_for_retry(state);
            PipelineState {
                agent_chain: state
                    .agent_chain
                    .advance_to_next_model()
                    .with_mode(DrainMode::Normal),
                continuation: ContinuationState {
                    xsd_retry_count: 0,
                    xsd_retry_pending: false,
                    xsd_retry_session_reuse_pending: false,
                    same_agent_retry_count: 0,
                    same_agent_retry_pending: false,
                    same_agent_retry_reason: None,
                    ..state.continuation
                },
                ..state
            }
        }
        AgentEvent::InvocationFailed {
            retriable: false,
            role: _,
            error_kind,
            ..
        } => {
            let state = reset_phase_xml_cleanup_for_retry(state);
            let active_role = state.agent_chain.current_drain.role();
            match error_kind {
                // Authentication and rate limit failures: immediate agent switch.
                // These may arrive as InvocationFailed for legacy callers; prefer AuthFailed/RateLimited.
                AgentErrorKind::Authentication => PipelineState {
                    agent_chain: state
                        .agent_chain
                        .switch_to_next_agent()
                        .clear_session_id()
                        .clear_continuation_prompt()
                        .with_mode(DrainMode::Normal)
                        .with_failure_reason(Some("auth failed".to_string())),
                    continuation: ContinuationState {
                        xsd_retry_count: 0,
                        xsd_retry_pending: false,
                        xsd_retry_session_reuse_pending: false,
                        same_agent_retry_count: 0,
                        same_agent_retry_pending: false,
                        same_agent_retry_reason: None,
                        ..state.continuation
                    },
                    ..state
                },
                AgentErrorKind::RateLimit => PipelineState {
                    // Legacy callers may report rate limit as InvocationFailed without prompt context.
                    // In that case, explicitly overwrite any saved continuation prompt to avoid
                    // reusing stale prompt context on the next invocation.
                    agent_chain: state
                        .agent_chain
                        .switch_to_next_agent_with_prompt_for_role(active_role, None)
                        .clear_session_id()
                        .with_mode(DrainMode::Normal)
                        .with_failure_reason(Some("rate-limited".to_string())),
                    continuation: ContinuationState {
                        xsd_retry_count: 0,
                        xsd_retry_pending: false,
                        xsd_retry_session_reuse_pending: false,
                        same_agent_retry_count: 0,
                        same_agent_retry_pending: false,
                        same_agent_retry_reason: None,
                        ..state.continuation
                    },
                    ..state
                },
                // Internal/unknown: retry same agent first; fall back after budget exhaustion.
                AgentErrorKind::InternalError => reduce_same_agent_retryable_failure(
                    state,
                    SameAgentRetryableFailure::InternalError,
                    None,
                ),
                // Defensive: treat explicit Timeout similarly if it arrives here.
                AgentErrorKind::Timeout => reduce_same_agent_retryable_failure(
                    state,
                    SameAgentRetryableFailure::Timeout,
                    None,
                ),
                // Other non-retriable errors: retry same agent first; only fall back after budget.
                _ => reduce_same_agent_retryable_failure(
                    state,
                    SameAgentRetryableFailure::OtherNonRetriable,
                    None,
                ),
            }
        }
        AgentEvent::FallbackTriggered {
            from_agent: _,
            to_agent,
            ..
        } => {
            let state = reset_phase_xml_cleanup_for_retry(state);

            PipelineState {
                agent_chain: state
                    .agent_chain
                    .switch_to_agent_named(to_agent.as_str())
                    .clear_session_id()
                    .clear_continuation_prompt()
                    .with_mode(DrainMode::Normal),
                continuation: ContinuationState {
                    xsd_retry_count: 0,
                    xsd_retry_pending: false,
                    xsd_retry_session_reuse_pending: false,
                    same_agent_retry_count: 0,
                    same_agent_retry_pending: false,
                    same_agent_retry_reason: None,
                    ..state.continuation
                },
                metrics: state.metrics.increment_agent_fallbacks_total(),
                ..state
            }
        }
        AgentEvent::ChainExhausted { .. } => PipelineState {
            agent_chain: state.agent_chain.start_retry_cycle(),
            ..state
        },
        AgentEvent::ModelFallbackTriggered { .. } => PipelineState {
            agent_chain: state.agent_chain.advance_to_next_model(),
            metrics: state.metrics.increment_model_fallbacks_total(),
            ..state
        },
        AgentEvent::RetryCycleStarted { .. } => PipelineState {
            agent_chain: state.agent_chain.clear_backoff_pending(),
            metrics: state.metrics.increment_retry_cycles_started_total(),
            ..state
        },
        AgentEvent::ChainInitialized {
            drain,
            agents,
            max_cycles,
            retry_delay_ms,
            backoff_multiplier,
            max_backoff_ms,
        } => {
            let agents_strings: Vec<String> = agents.iter().map(|a| a.to_string()).collect();
            let models_per_agent: Vec<Vec<String>> =
                agents_strings.iter().map(|_| vec![]).collect();
            PipelineState {
                agent_chain: state
                    .agent_chain
                    .with_agents(agents_strings, models_per_agent, drain.role())
                    .with_drain(drain)
                    .with_max_cycles(max_cycles)
                    .with_backoff_policy(retry_delay_ms, backoff_multiplier, max_backoff_ms)
                    .reset_for_drain(drain),
                ..state
            }
        }
        // Session established: store session ID for potential XSD retry
        AgentEvent::SessionEstablished { session_id, .. } => PipelineState {
            agent_chain: state.agent_chain.with_session_id(Some(session_id)),
            ..state
        },
        // XSD validation failed: trigger XSD retry via continuation state
        AgentEvent::XsdValidationFailed { .. } => {
            let active_review_drain = state.runtime_drain();
            PipelineState {
                agent_chain: state.agent_chain.with_mode(DrainMode::XsdRetry),
                continuation: state.continuation.trigger_xsd_retry(),
                // Increment per-phase counter based on current phase.
                metrics: match state.phase {
                    PipelinePhase::Planning => state.metrics.increment_xsd_retry_planning(),
                    PipelinePhase::Development => state.metrics.increment_xsd_retry_development(),
                    PipelinePhase::Review => {
                        if active_review_drain == crate::agents::AgentDrain::Fix {
                            state.metrics.increment_xsd_retry_fix()
                        } else {
                            state.metrics.increment_xsd_retry_review()
                        }
                    }
                    PipelinePhase::CommitMessage => state.metrics.increment_xsd_retry_commit(),
                    _ => state.metrics.increment_xsd_retry_attempts_total(),
                },
                ..state
            }
        }

        // Template variables invalid: retry same agent first; only fall back after budget.
        AgentEvent::TemplateVariablesInvalid { .. } => reduce_same_agent_retryable_failure(
            state,
            SameAgentRetryableFailure::OtherNonRetriable,
            None,
        ),

        // Timeout context written: store the context file path and clear the pending flag.
        // The context file is written by the handler for session-less agent retry.
        // The retry prompt will reference this file via the retry guidance preamble.
        AgentEvent::TimeoutContextWritten { context_path, .. } => PipelineState {
            continuation: ContinuationState {
                timeout_context_write_pending: false,
                timeout_context_file_path: Some(context_path),
                ..state.continuation
            },
            ..state
        },

        // =====================================================================
        // Phase 4: Parallel Worker Events
        // =====================================================================
        // Note: These events are handled here for state tracking during parallel execution.
        // The actual worker dispatch and coordination happens via effects.
        AgentEvent::ParallelPlanProduced { plan } => {
            // Store the parallel plan in state for tracking.
            // The orchestration layer will derive the EvaluateParallelPlan effect.
            PipelineState {
                parallel_plan: Some(plan),
                parallel_plan_validated: false,
                ..state
            }
        }
        AgentEvent::ParallelPlanValidated { plan } => {
            // Plan validated - store it (orchestration will derive DispatchParallelWorkers).
            // Keep any existing parallel state since the plan is now validated and ready for dispatch.
            PipelineState {
                parallel_plan: Some(plan),
                parallel_plan_validated: true,
                ..state
            }
        }
        AgentEvent::ParallelPlanRejected { plan: _, reason } => {
            // Plan rejected - fall back to single-agent mode.
            // Clear the parallel plan and record the rejection reason.
            // The orchestration layer will continue with single-agent planning.
            PipelineState {
                parallel_plan: None,
                parallel_plan_validated: false,
                parallel_workers: Vec::new(),
                parallel_workers_completed: Vec::new(),
                parallel_plan_rejected_reason: Some(reason),
                ..state
            }
        }
        AgentEvent::ParallelWorkersDispatched {
            worker_count: _,
            workers,
        } => {
            // Workers dispatched - track the worker identities.
            // The orchestration layer manages worker lifecycle via events.
            PipelineState {
                parallel_workers: workers,
                parallel_workers_completed: Vec::new(),
                ..state
            }
        }
        AgentEvent::ParallelWorkerCompleted {
            worker_id,
            metadata: _,
        } => {
            // Worker completed - track completion status.
            // When all workers complete, orchestration will trigger verification.
            let mut completed = state.parallel_workers_completed.clone();
            if !completed.contains(&worker_id) {
                completed.push(worker_id);
            }
            PipelineState {
                parallel_workers_completed: completed,
                ..state
            }
        }
    }
}

#[derive(Clone, Copy)]
enum SameAgentRetryableFailure {
    Timeout,
    TimeoutWithContext,
    InternalError,
    OtherNonRetriable,
}

fn reduce_same_agent_retryable_failure(
    state: PipelineState,
    failure: SameAgentRetryableFailure,
    logfile_path: Option<String>,
) -> PipelineState {
    let state = reset_phase_xml_cleanup_for_retry(state);
    // Keep agent selection reducer-driven and deterministic:
    // - Retry same agent first for timeouts/internal errors.
    // - Fall back to next agent only after exhausting the configured budget.
    let new_retry_count = state.continuation.same_agent_retry_count + 1;

    // Only increment metrics if we're actually retrying (not exhausted)
    let will_retry = new_retry_count < state.continuation.max_same_agent_retry_count;

    if new_retry_count >= state.continuation.max_same_agent_retry_count {
        let max_count = state.continuation.max_same_agent_retry_count;
        PipelineState {
            agent_chain: state
                .agent_chain
                .switch_to_next_agent()
                .clear_session_id()
                .with_mode(DrainMode::Normal)
                .with_failure_reason(Some(format!("failed after {} retries", max_count))),
            continuation: ContinuationState {
                xsd_retry_count: 0,
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                same_agent_retry_count: 0,
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                timeout_context_write_pending: false,
                timeout_context_file_path: None,
                ..state.continuation
            },
            metrics: if will_retry {
                state.metrics.increment_same_agent_retry_attempts_total()
            } else {
                state.metrics
            },
            ..state
        }
    } else {
        let reason = match failure {
            SameAgentRetryableFailure::Timeout => SameAgentRetryReason::Timeout,
            SameAgentRetryableFailure::TimeoutWithContext => {
                SameAgentRetryReason::TimeoutWithContext
            }
            SameAgentRetryableFailure::InternalError => SameAgentRetryReason::InternalError,
            SameAgentRetryableFailure::OtherNonRetriable => SameAgentRetryReason::Other,
        };

        // For TimeoutWithContext, preserve session ID to maintain context.
        // For all other retry reasons, clear the session ID.
        let agent_chain = match failure {
            SameAgentRetryableFailure::TimeoutWithContext => state.agent_chain,
            _ => state.agent_chain.clear_session_id(),
        };

        // For TimeoutWithContext:
        // - If session ID exists: set xsd_retry_session_reuse_pending to reuse session
        // - If no session ID: set timeout_context_write_pending to extract context to file
        let (session_reuse_pending, timeout_context_write_pending, timeout_context_file_path) =
            match failure {
                SameAgentRetryableFailure::TimeoutWithContext => {
                    if agent_chain.last_session_id.is_some() {
                        (true, false, None)
                    } else {
                        // Store the logfile path so orchestration can use it for WriteTimeoutContext
                        (false, true, logfile_path)
                    }
                }
                _ => (false, false, None),
            };

        PipelineState {
            agent_chain: agent_chain.with_mode(DrainMode::SameAgentRetry),
            continuation: ContinuationState {
                same_agent_retry_count: new_retry_count,
                same_agent_retry_pending: true,
                same_agent_retry_reason: Some(reason),
                xsd_retry_session_reuse_pending: session_reuse_pending,
                timeout_context_write_pending,
                timeout_context_file_path,
                ..state.continuation
            },
            metrics: if will_retry {
                state.metrics.increment_same_agent_retry_attempts_total()
            } else {
                state.metrics
            },
            ..state
        }
    }
}

fn reset_phase_xml_cleanup_for_retry(state: PipelineState) -> PipelineState {
    match state.phase {
        PipelinePhase::Planning => PipelineState {
            planning_required_files_cleaned_iteration: None,
            ..state
        },
        PipelinePhase::Development => PipelineState {
            development_required_files_cleaned_iteration: None,
            ..state
        },
        PipelinePhase::Review => {
            if state.runtime_drain() == crate::agents::AgentDrain::Fix {
                PipelineState {
                    fix_required_files_cleaned_pass: None,
                    ..state
                }
            } else {
                PipelineState {
                    review_required_files_cleaned_pass: None,
                    ..state
                }
            }
        }
        PipelinePhase::CommitMessage => PipelineState {
            commit_required_files_cleaned: false,
            ..state
        },
        _ => state,
    }
}
