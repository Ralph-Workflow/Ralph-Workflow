//! Lifecycle and system effect-to-event mapping.
//!
//! This module handles effect execution for pipeline lifecycle and system effects.
//! These effects manage the overall pipeline state, not specific phase logic.
//!
//! ## Lifecycle Effects
//!
//! ### Agent Management
//! - **`InitializeAgentChain`** - Set up agent chain for a new phase
//! - **`BackoffWait`** - Wait before retrying after agent failure
//! - **`ReportAgentChainExhausted`** - Report when all agents in chain have failed
//!
//! ### Cleanup
//! - **`CleanupRequiredFiles`** - Unified cleanup of XML files before agent invocation
//!
//! ### Checkpointing
//! - **`SaveCheckpoint`** - Save pipeline state for resume capability
//!
//! ### Continuation (Development Phase)
//! - **`WriteContinuationContext`** - Save context for continuing partial work
//! - **`CleanupContinuationContext`** - Clean continuation context after completion
//!
//! ### Finalization
//! - **`ValidateFinalState`** - Validate pipeline completed successfully
//! - **`CleanupContext`** - Clean up temporary files
//! - **`LockPromptPermissions`** - Lock PROMPT.md with read-only permissions at startup
//! - **`RestorePromptPermissions`** - Restore file permissions changed during execution
//! - **`EnsureGitignoreEntries`** - Ensure required gitignore entries exist
//!
//! ### Error Recovery
//! - **`TriggerDevFixFlow`** - Trigger manual intervention workflow (panics in mock)
//! - **`EmitCompletionMarkerAndTerminate`** - Emit completion marker for external monitoring
//! - **`TriggerLoopRecovery`** - Recover from detected infinite loops
//!
//! ## Mock Behavior
//!
//! - **`SaveCheckpoint`** automatically emits phase completion events when appropriate
//! - **`InitializeAgentChain`** emits phase transition UI events
//! - **`CleanupRequiredFiles`** emits phase-appropriate cleanup events
//! - **`TriggerDevFixFlow`** panics (requires real workspace access)
//! - **`ReportAgentChainExhausted`** panics (should not occur in normal test flow)

use crate::common::domain_types::AgentName;
use crate::reducer::effect::Effect;
use crate::reducer::event::{
    AwaitingDevFixEvent, CheckpointTrigger, CommitEvent, DevelopmentEvent, PipelineEvent,
    PipelinePhase, PlanningEvent, ReviewEvent,
};
use crate::reducer::ui_event::UIEvent;

use super::super::MockEffectHandler;

impl MockEffectHandler {
    /// Handle lifecycle and system effects.
    ///
    /// Returns appropriate mock events for lifecycle effects without
    /// performing real I/O or system operations.
    pub(super) fn handle_lifecycle_effect(
        &self,
        effect: Effect,
    ) -> Option<(PipelineEvent, Vec<UIEvent>, Vec<PipelineEvent>)> {
        match effect {
            Effect::CleanupRequiredFiles { files: _ } => {
                // Emit phase-appropriate cleanup event based on current phase
                let event = match self.state.phase {
                    PipelinePhase::Planning => {
                        PipelineEvent::Planning(PlanningEvent::PlanXmlCleaned {
                            iteration: self.state.iteration,
                        })
                    }
                    PipelinePhase::Development => {
                        PipelineEvent::Development(DevelopmentEvent::XmlCleaned {
                            iteration: self.state.iteration,
                        })
                    }
                    PipelinePhase::Review => {
                        // Review-phase cleanup follows the active drain so mock execution stays
                        // aligned with reducer-owned fix continuation and retry flows.
                        if self.state.runtime_drain() == crate::agents::AgentDrain::Fix {
                            PipelineEvent::Review(ReviewEvent::FixResultXmlCleaned {
                                pass: self.state.reviewer_pass,
                            })
                        } else {
                            PipelineEvent::Review(ReviewEvent::IssuesXmlCleaned {
                                pass: self.state.reviewer_pass,
                            })
                        }
                    }
                    PipelinePhase::CommitMessage => {
                        let attempt = match &self.state.commit {
                            crate::reducer::state::CommitState::Generating { attempt, .. } => {
                                *attempt
                            }
                            _ => 1,
                        };
                        PipelineEvent::Commit(CommitEvent::CommitXmlCleaned { attempt })
                    }
                    _ => {
                        // Fallback for unexpected phases - use context_cleaned as a safe default
                        PipelineEvent::context_cleaned()
                    }
                };
                Some((event, vec![], vec![]))
            }

            Effect::AgentInvocation {
                role,
                agent,
                model: _,
                prompt: _,
            } => {
                let ui = vec![UIEvent::AgentActivity {
                    agent: agent.clone(),
                    message: format!("Completed {role} task"),
                }];
                Some((
                    PipelineEvent::agent_invocation_succeeded(role, AgentName::from(agent.clone())),
                    ui,
                    vec![],
                ))
            }

            Effect::InitializeAgentChain { drain, .. } => {
                let role = drain.role();
                // Emit phase transition when initializing agent chain for a new phase
                let ui = match role {
                    crate::agents::AgentRole::Developer
                        if self.state.phase == PipelinePhase::Planning =>
                    {
                        vec![UIEvent::PhaseTransition {
                            from: None,
                            to: PipelinePhase::Planning,
                        }]
                    }
                    crate::agents::AgentRole::Reviewer
                        if self.state.phase == PipelinePhase::Review =>
                    {
                        vec![UIEvent::PhaseTransition {
                            from: Some(self.state.phase),
                            to: PipelinePhase::Review,
                        }]
                    }
                    _ => vec![],
                };
                Some((
                    PipelineEvent::agent_chain_initialized(
                        drain,
                        vec![AgentName::from("mock_agent")],
                        3,
                        1000,
                        2.0,
                        60000,
                    ),
                    ui,
                    vec![],
                ))
            }

            Effect::BackoffWait {
                role,
                cycle,
                duration_ms: _,
            } => Some((
                PipelineEvent::agent_retry_cycle_started(role, cycle),
                vec![],
                vec![],
            )),

            Effect::ReportAgentChainExhausted { role, phase, cycle } => {
                panic!(
                    "MockEffectHandler received ReportAgentChainExhausted effect: role={role:?}, phase={phase:?}, cycle={cycle}"
                )
            }

            Effect::ValidateFinalState => {
                let ui = vec![UIEvent::PhaseTransition {
                    from: Some(self.state.phase),
                    to: PipelinePhase::Finalizing,
                }];
                Some((PipelineEvent::finalizing_started(), ui, vec![]))
            }

            Effect::SaveCheckpoint { trigger } => {
                let checkpoint_saved = PipelineEvent::checkpoint_saved(trigger);

                let additional_events = if trigger == CheckpointTrigger::PhaseTransition {
                    match self.state.phase {
                        PipelinePhase::Planning => vec![PipelineEvent::planning_phase_completed()],
                        PipelinePhase::Development
                            if self.state.iteration >= self.state.total_iterations =>
                        {
                            vec![PipelineEvent::development_phase_completed()]
                        }
                        PipelinePhase::Review
                            if self.state.reviewer_pass >= self.state.total_reviewer_passes =>
                        {
                            vec![PipelineEvent::review_phase_completed(
                                /* early_exit */ false,
                            )]
                        }
                        PipelinePhase::CommitMessage => {
                            vec![PipelineEvent::commit_skipped(
                                "Mock: commit phase transition".to_string(),
                            )]
                        }
                        _ => vec![],
                    }
                } else {
                    vec![]
                };

                Some((checkpoint_saved, vec![], additional_events))
            }

            Effect::CleanupContext => Some((PipelineEvent::context_cleaned(), vec![], vec![])),

            Effect::RestorePromptPermissions => {
                let ui = if self.state.phase == PipelinePhase::Finalizing {
                    vec![UIEvent::PhaseTransition {
                        from: Some(self.state.phase),
                        to: PipelinePhase::Complete,
                    }]
                } else {
                    vec![]
                };
                Some((PipelineEvent::prompt_permissions_restored(), ui, vec![]))
            }

            Effect::LockPromptPermissions => {
                // Mock always succeeds with no warning
                Some((
                    PipelineEvent::prompt_permissions_locked(None),
                    vec![],
                    vec![],
                ))
            }

            Effect::WriteContinuationContext(ref data) => Some((
                PipelineEvent::development_continuation_context_written(
                    data.iteration,
                    data.attempt,
                ),
                vec![],
                vec![],
            )),

            Effect::WriteTimeoutContext {
                role,
                logfile_path,
                context_path,
            } => Some((
                PipelineEvent::agent_timeout_context_written(role, logfile_path, context_path),
                vec![],
                vec![],
            )),

            Effect::CleanupContinuationContext => Some((
                PipelineEvent::development_continuation_context_cleaned(),
                vec![],
                vec![],
            )),

            Effect::TriggerDevFixFlow { .. } => {
                // Handled in execute() method to access PhaseContext workspace
                panic!(
                    "TriggerDevFixFlow should be handled in execute() method, not execute_mock()"
                )
            }

            Effect::EmitCompletionMarkerAndTerminate {
                is_failure,
                reason: _,
            } => Some((
                PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::CompletionMarkerEmitted {
                    is_failure,
                }),
                vec![],
                vec![],
            )),

            Effect::TriggerLoopRecovery {
                detected_loop,
                loop_count,
            } => Some((
                PipelineEvent::LoopRecoveryTriggered {
                    detected_loop,
                    loop_count,
                },
                vec![],
                vec![],
            )),

            Effect::EmitRecoveryReset {
                reset_type,
                target_phase,
            } => {
                let level = match reset_type {
                    crate::reducer::effect::RecoveryResetType::PhaseStart => 2,
                    crate::reducer::effect::RecoveryResetType::IterationReset => 3,
                    crate::reducer::effect::RecoveryResetType::CompleteReset => 4,
                };
                Some((
                    PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                        level,
                        attempt_count: self.state.dev_fix_attempt_count,
                        target_phase,
                    }),
                    vec![],
                    vec![],
                ))
            }

            Effect::AttemptRecovery {
                level,
                attempt_count,
            } => Some((
                PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                    level,
                    attempt_count,
                    target_phase: {
                        let phase = self
                            .state
                            .failed_phase_for_recovery
                            .or(self.state.previous_phase)
                            .unwrap_or(PipelinePhase::Development);
                        if phase == PipelinePhase::AwaitingDevFix {
                            PipelinePhase::Development
                        } else {
                            phase
                        }
                    },
                }),
                vec![],
                vec![],
            )),

            Effect::EmitRecoverySuccess {
                level,
                total_attempts,
            } => Some((
                PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoverySucceeded {
                    level,
                    total_attempts,
                }),
                vec![],
                vec![],
            )),

            Effect::EnsureGitignoreEntries => Some((
                PipelineEvent::gitignore_entries_ensured(
                    vec!["/PROMPT*".to_string(), ".agent/".to_string()],
                    vec![],
                    false,
                ),
                vec![],
                vec![],
            )),

            Effect::CheckUncommittedChangesBeforeTermination => {
                match self.pre_termination_snapshot.clone() {
                    super::super::core::PreTerminationSnapshotMock::Clean => Some((
                        PipelineEvent::pre_termination_safety_check_passed(),
                        vec![],
                        vec![],
                    )),
                    super::super::core::PreTerminationSnapshotMock::Dirty { file_count } => Some((
                        PipelineEvent::pre_termination_uncommitted_changes_detected(file_count),
                        vec![],
                        vec![],
                    )),
                    super::super::core::PreTerminationSnapshotMock::Error { kind } => {
                        // execute_mock() cannot return handler errors (it doesn't take PhaseContext and
                        // doesn't return Result). Call the EffectHandler::execute() path to simulate
                        // failures for this effect.
                        panic!(
                            "MockEffectHandler cannot simulate pre-termination snapshot error via execute_mock (kind={kind:?}); use execute() instead"
                        )
                    }
                }
            }

            _ => None,
        }
    }
}
