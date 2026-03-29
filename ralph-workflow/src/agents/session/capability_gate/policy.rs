//! Policy enforcement for capability-based access control.
//!
//! This module provides the core policy engine for Phase 2 enforcement:
//! - Session capability checking against required capabilities
//! - Ralph-internal effect bypass logic
//! - PolicyOutcome generation for audit trail

use crate::agents::session::{AgentSession, PolicyOutcome};
use crate::reducer::effect::Effect;

use super::effect_map::required_capabilities;

/// Returns true if this effect is a Ralph-internal effect that should bypass
/// the capability gate (e.g., checkpoint save, recovery, lifecycle operations).
///
/// These effects are never user-driven and are part of Ralph's internal
/// orchestration machinery.
#[must_use]
pub fn is_ralph_internal_effect(effect: &Effect) -> bool {
    matches!(
        effect,
        Effect::InitializeAgentChain { .. }
            | Effect::SaveCheckpoint { .. }
            | Effect::RestorePromptPermissions
            | Effect::LockPromptPermissions
            | Effect::WriteContinuationContext(..)
            | Effect::CleanupContinuationContext
            | Effect::WriteTimeoutContext { .. }
            | Effect::TriggerDevFixFlow { .. }
            | Effect::EmitCompletionMarkerAndTerminate { .. }
            | Effect::TriggerLoopRecovery { .. }
            | Effect::EmitRecoveryReset { .. }
            | Effect::AttemptRecovery { .. }
            | Effect::EmitRecoverySuccess { .. }
            | Effect::ConfigureGitAuth { .. }
            | Effect::PushToRemote { .. }
            | Effect::CreatePullRequest { .. }
            | Effect::BackoffWait { .. }
            | Effect::ValidateFinalState
            | Effect::CleanupContext
            | Effect::EnsureGitignoreEntries
            // Phase 4: Parallel worker effects are Ralph-internal
            | Effect::EvaluateParallelPlan { .. }
            | Effect::DispatchParallelWorkers { .. }
            // Archive effects write to .agent/ directory which is Ralph's internal ephemeral storage.
            // These are not user-driven writes - they're Ralph persisting its own artifacts.
            | Effect::ArchivePlanningXml { .. }
            | Effect::ArchiveDevelopmentXml { .. }
            | Effect::ArchiveReviewIssuesXml { .. }
            | Effect::ArchiveFixResultXml { .. }
            | Effect::ArchiveCommitXml
    )
}

/// Check whether the given session has sufficient capabilities to execute the effect.
///
/// Returns `PolicyOutcome::Approved` if all required capabilities are present,
/// or `PolicyOutcome::Denied { reason }` describing which capability is missing.
///
/// # Arguments
///
/// * `session` - The agent session to check capabilities against
/// * `effect` - The effect to be executed
///
/// # Example
///
/// ```ignore
/// let session = AgentSession::for_drain("run-123".to_string(), SessionDrain::Planning, 1);
/// let effect = Effect::InvokePlanningAgent { iteration: 1 };
/// let outcome = check_effect_capability(&session, &effect);
/// assert!(matches!(outcome, PolicyOutcome::Approved));
/// ```
#[must_use]
pub fn check_effect_capability(session: &AgentSession, effect: &Effect) -> PolicyOutcome {
    // Ralph-internal effects bypass capability checks entirely.
    // These are Ralph's own operations, not user-driven actions.
    if is_ralph_internal_effect(effect) {
        return PolicyOutcome::Approved;
    }

    let required = required_capabilities(effect);
    required
        .iter()
        .map(|cap| session.check_capability(*cap))
        .find(|outcome| !matches!(outcome, PolicyOutcome::Approved))
        .unwrap_or(PolicyOutcome::Approved)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::{AgentSession, PolicyOutcome, SessionDrain};

    fn planning_session() -> AgentSession {
        AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1)
    }

    fn review_session() -> AgentSession {
        AgentSession::for_drain("test-run".to_string(), SessionDrain::Review, 1)
    }

    fn commit_session() -> AgentSession {
        AgentSession::for_drain("test-run".to_string(), SessionDrain::Commit, 1)
    }

    // ===================================================================
    // Session capability checking tests
    // ===================================================================

    #[test]
    fn planning_session_denies_development_effects() {
        let session = planning_session();

        // Development effects require WorkspaceWriteTracked and ProcessExecBounded
        // which planning session doesn't have
        let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Planning session should deny development effects"
        );
    }

    #[test]
    fn planning_session_denies_fix_effects() {
        let session = planning_session();

        let effect = Effect::InvokeFixAgent { pass: 1 };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Planning session should deny fix effects"
        );
    }

    #[test]
    fn planning_session_denies_git_write() {
        let session = planning_session();

        let effect = Effect::CreateCommit {
            message: "test".to_string(),
            files: vec![],
            excluded_files: vec![],
        };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Planning session should deny git write"
        );
    }

    #[test]
    fn review_session_denies_git_write() {
        let session = review_session();

        let effect = Effect::CreateCommit {
            message: "test".to_string(),
            files: vec![],
            excluded_files: vec![],
        };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Review session should deny git write"
        );
    }

    #[test]
    fn commit_session_denies_workspace_write_tracked() {
        let session = commit_session();

        // Commit session doesn't have WorkspaceWriteTracked
        let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Commit session should deny workspace write tracked"
        );
    }

    // ===================================================================
    // Denial reason tests
    // ===================================================================

    #[test]
    fn denied_outcome_includes_reason() {
        let session = planning_session();
        let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };
        let outcome = check_effect_capability(&session, &effect);

        if let PolicyOutcome::Denied { reason } = outcome {
            assert!(
                reason.contains("workspace.write_tracked")
                    || reason.contains("process.exec_bounded"),
                "Denial reason should mention missing capability"
            );
        } else {
            panic!("Expected denial, got {:?}", outcome);
        }
    }

    #[test]
    fn denied_outcome_includes_capability_name() {
        let session = planning_session();
        let effect = Effect::CreateCommit {
            message: "test".to_string(),
            files: vec![],
            excluded_files: vec![],
        };
        let outcome = check_effect_capability(&session, &effect);

        if let PolicyOutcome::Denied { reason } = outcome {
            assert!(
                reason.contains("git.write"),
                "Denial reason should mention git.write"
            );
        } else {
            panic!("Expected denial, got {:?}", outcome);
        }
    }

    // ===================================================================
    // Internal effect bypass tests
    // ===================================================================

    #[test]
    fn ralph_internal_effects_bypass_capability_checks() {
        let session = planning_session();

        // These are Ralph-internal effects that should always be approved
        let internal_effects = vec![
            Effect::SaveCheckpoint {
                trigger: crate::reducer::event::CheckpointTrigger::Interrupt,
            },
            Effect::WriteContinuationContext(crate::reducer::effect::ContinuationContextData {
                iteration: 1,
                attempt: 1,
                status: crate::reducer::state::DevelopmentStatus::Completed,
                summary: String::new(),
                files_changed: None,
                next_steps: None,
            }),
            Effect::CleanupContinuationContext,
            Effect::TriggerLoopRecovery {
                detected_loop: "test".to_string(),
                loop_count: 1,
            },
            Effect::EmitRecoveryReset {
                reset_type: crate::reducer::effect::RecoveryResetType::CompleteReset,
                target_phase: crate::reducer::event::PipelinePhase::Planning,
            },
            Effect::AttemptRecovery {
                level: 1,
                attempt_count: 1,
            },
            Effect::EmitRecoverySuccess {
                level: 1,
                total_attempts: 1,
            },
        ];

        for effect in internal_effects {
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Ralph internal effect {:?} should be approved, got {:?}",
                effect,
                outcome
            );
        }
    }

    #[test]
    fn is_ralph_internal_effect_recognizes_internal_effects() {
        let internal_effects = vec![
            Effect::InitializeAgentChain {
                drain: crate::agents::AgentDrain::Planning,
            },
            Effect::SaveCheckpoint {
                trigger: crate::reducer::event::CheckpointTrigger::Interrupt,
            },
            Effect::WriteContinuationContext(crate::reducer::effect::ContinuationContextData {
                iteration: 1,
                attempt: 1,
                status: crate::reducer::state::DevelopmentStatus::Completed,
                summary: String::new(),
                files_changed: None,
                next_steps: None,
            }),
            Effect::ArchivePlanningXml { iteration: 1 },
        ];

        for effect in internal_effects {
            assert!(
                is_ralph_internal_effect(&effect),
                "{:?} should be recognized as Ralph-internal",
                effect
            );
        }
    }

    #[test]
    fn is_ralph_internal_effect_rejects_user_effects() {
        let user_effects = vec![
            Effect::InvokePlanningAgent { iteration: 1 },
            Effect::InvokeDevelopmentAgent { iteration: 1 },
            Effect::CreateCommit {
                message: "test".to_string(),
                files: vec![],
                excluded_files: vec![],
            },
            Effect::CheckCommitDiff,
        ];

        for effect in user_effects {
            assert!(
                !is_ralph_internal_effect(&effect),
                "{:?} should NOT be recognized as Ralph-internal",
                effect
            );
        }
    }
}
