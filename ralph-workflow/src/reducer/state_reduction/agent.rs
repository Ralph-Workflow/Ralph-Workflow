// NOTE: split from reducer/state_reduction.rs.

use crate::agents::DrainMode;
use crate::reducer::event::{AgentErrorKind, AgentEvent, PipelinePhase, TimeoutOutputKind};
use crate::reducer::state::{ContinuationState, PipelineState, RunMetrics, SameAgentRetryReason};

pub(super) fn reduce_agent_event(state: PipelineState, event: AgentEvent) -> PipelineState {
    match event {
        // Do NOT clear any saved continuation prompt on invocation start.
        //
        // Rationale: after a 429, we preserve prompt context so the next agent can continue the
        // same work. If the first post-rate-limit invocation fails (e.g., timeout/internal), we
        // must keep the continuation prompt available for retries until an invocation succeeds.
        AgentEvent::InvocationStarted { .. } => state,
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
            output_kind: TimeoutOutputKind::NoResult,
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
            output_kind: TimeoutOutputKind::PartialResult,
            logfile_path,
            ..
        } => reduce_same_agent_retryable_failure(
            state,
            SameAgentRetryableFailure::TimeoutWithContext,
            logfile_path,
        ),
        // Network errors: trigger connectivity check before consuming any retry budget.
        //
        // IMPORTANT: We do NOT advance models, reset retry counters, or change agent chain
        // state. All retry/continuation state is preserved unchanged. The orchestrator
        // will return CheckNetworkConnectivity (Priority 2) before any budget-consuming
        // effect. If connectivity is confirmed offline, the pipeline freezes without
        // consuming budget. If connectivity is restored, the orchestrator re-derives
        // the same effect that was about to run, and normal retry proceeds.
        AgentEvent::InvocationFailed {
            retriable: true,
            error_kind: AgentErrorKind::Network,
            ..
        } => PipelineState {
            connectivity: state.connectivity.trigger_check(),
            ..state
        },
        // Other retriable errors (ModelUnavailable): try next model
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
            models_per_agent,
            max_cycles,
            retry_delay_ms,
            backoff_multiplier,
            max_backoff_ms,
        } => {
            let agents_strings: Vec<String> = agents.iter().map(|a| a.to_string()).collect();
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
        // Session established: store session ID for potential retry context reuse
        AgentEvent::SessionEstablished { session_id, .. } => PipelineState {
            agent_chain: state.agent_chain.with_session_id(Some(session_id)),
            ..state
        },
        // XSD validation failed: no-op, MCP artifacts are the only path now.
        // XSD retry infrastructure has been removed; treat as a no-op.
        AgentEvent::XsdValidationFailed { .. } => state,

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
            PipelineState {
                parallel_workers_completed: if !state
                    .parallel_workers_completed
                    .contains(&worker_id)
                {
                    state
                        .parallel_workers_completed
                        .iter()
                        .chain(std::iter::once(&worker_id))
                        .cloned()
                        .collect()
                } else {
                    state.parallel_workers_completed.clone()
                },
                ..state
            }
        }
        AgentEvent::VerifierCompleted { decision: _ } => {
            // Verifier completed - the orchestration layer handles the decision
            // and derives the next effect (rework, spawn new, collapse, or accept).
            // Just track that verification happened.
            PipelineState {
                parallel_verification_completed: true,
                ..state
            }
        }
        AgentEvent::ParallelWorkReworked {
            unit_ids: _,
            feedback: _,
        } => {
            // Work sent back for rework - increment verification iteration counter.
            // The orchestration layer will re-dispatch workers with feedback.
            PipelineState {
                parallel_verification_iteration: state.parallel_verification_iteration + 1,
                ..state
            }
        }
        AgentEvent::ParallelWorkCollapsed {
            remaining_units: _,
            reason: _,
        } => {
            // Work collapsed to single-agent - clear parallel state.
            // The orchestration layer will continue with single-agent execution.
            PipelineState {
                parallel_plan: None,
                parallel_plan_validated: false,
                parallel_workers: Vec::new(),
                parallel_workers_completed: Vec::new(),
                parallel_verification_completed: false,
                parallel_verification_iteration: 0,
                ..state
            }
        }
        // Connectivity probe succeeded: update ConnectivityState to reflect the probe result.
        // This clears check_pending and, if we were offline, transitions back to online.
        AgentEvent::ConnectivityCheckSucceeded => PipelineState {
            connectivity: state.connectivity.on_probe_succeeded(),
            ..state
        },

        // Connectivity probe failed: update ConnectivityState by incrementing failure count.
        // The reducer maintains the failure counter via on_probe_failed().
        // If the failure threshold is reached, enters offline mode.
        // Increment connectivity_interruptions_total only on the false→true transition.
        // This is the exact moment the pipeline enters offline mode (debounce threshold met).
        AgentEvent::ConnectivityCheckFailed => {
            let new_connectivity = state.connectivity.clone().on_probe_failed();
            let connectivity_interruptions_total =
                if new_connectivity.is_offline && !state.connectivity.is_offline {
                    state
                        .metrics
                        .connectivity_interruptions_total
                        .saturating_add(1)
                } else {
                    state.metrics.connectivity_interruptions_total
                };
            PipelineState {
                connectivity: new_connectivity,
                metrics: RunMetrics {
                    connectivity_interruptions_total,
                    ..state.metrics.clone()
                },
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
        // - If no session ID: set timeout_context_write_pending to extract context to file
        let (timeout_context_write_pending, timeout_context_file_path) = match failure {
            SameAgentRetryableFailure::TimeoutWithContext => {
                if agent_chain.last_session_id.is_some() {
                    (false, None)
                } else {
                    // Store the logfile path so orchestration can use it for WriteTimeoutContext
                    (true, logfile_path)
                }
            }
            _ => (false, None),
        };

        PipelineState {
            agent_chain: agent_chain.with_mode(DrainMode::SameAgentRetry),
            continuation: ContinuationState {
                same_agent_retry_count: new_retry_count,
                same_agent_retry_pending: true,
                same_agent_retry_reason: Some(reason),
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
