//! Regression tests for RFC-009 brokered sessions - behavioral equivalence verification.
//!
//! These tests verify that RFC-009 changes maintain behavioral equivalence with the
//! pre-RFC-009 implementation for all drain types. The key constraint:
//!
//! **"All existing single-agent workflows must produce identical results.
//! Only parallel editing introduces user-visible behavior changes."**
//!
//! This file captures the complete behavioral contract for each drain type,
//! ensuring capability enforcement, audit trail, and session management
//! do not introduce regressions in single-agent workflows.
//!
//! # Test Categories
//!
//! 1. **Drain Capability Contracts** - Verify each drain has exactly the capabilities it should have
//! 2. **Effect-Capability Mapping** - Verify effects require the correct capabilities per drain
//! 3. **Policy Enforcement** - Verify denied capabilities produce correct PolicyOutcome
//! 4. **Audit Trail Completeness** - Verify audit records are created for all relevant events
//! 5. **Session Lifecycle** - Verify session creation, handshake, and cleanup work correctly
//! 6. **Edit Area Isolation** - Verify parallel worker restrictions don't affect single-agent
//! 7. **Command Policy** - Verify blacklist enforcement doesn't affect allowed commands

use ralph_workflow::agents::session::capability_gate::{
    check_effect_capability, effect_kind, effect_name, is_ralph_internal_effect, EffectKind,
};
use ralph_workflow::agents::session::parallel::{
    edit_areas_overlap, ParallelPlan, RestrictedEditArea,
};
use ralph_workflow::agents::session::{
    AgentSession, AgentSessionId, AuditTrail, Capability, PolicyFlag, PolicyOutcome, SessionDrain,
    SessionHandshake,
};
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::state::PromptMode;

use crate::test_timeout::with_default_timeout;

// =============================================================================
// Drain Capability Contracts
// =============================================================================

/// Verify Planning drain has exactly the capabilities defined in RFC-009.
///
/// Planning: read-only (workspace read, git read, ephemeral write for Ralph's own files)
#[test]
fn regression_planning_drain_capability_contract() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Planning, 0);

        // Must have
        assert!(session.capabilities.contains(Capability::WorkspaceRead));
        assert!(session
            .capabilities
            .contains(Capability::WorkspaceWriteEphemeral));
        assert!(session.capabilities.contains(Capability::GitStatusRead));
        assert!(session.capabilities.contains(Capability::GitDiffRead));
        assert!(session.capabilities.contains(Capability::ArtifactSubmit));

        // Must NOT have
        assert!(!session
            .capabilities
            .contains(Capability::WorkspaceWriteTracked));
        assert!(!session
            .capabilities
            .contains(Capability::ProcessExecBounded));
        assert!(!session.capabilities.contains(Capability::GitWrite));
    });
}

/// Verify Development drain has exactly the capabilities defined in RFC-009.
///
/// Development: write-capable (tracked + ephemeral writes, process exec)
#[test]
fn regression_development_drain_capability_contract() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Development, 0);

        // Must have
        assert!(session.capabilities.contains(Capability::WorkspaceRead));
        assert!(session
            .capabilities
            .contains(Capability::WorkspaceWriteEphemeral));
        assert!(session
            .capabilities
            .contains(Capability::WorkspaceWriteTracked));
        assert!(session.capabilities.contains(Capability::GitStatusRead));
        assert!(session.capabilities.contains(Capability::GitDiffRead));
        assert!(session
            .capabilities
            .contains(Capability::ProcessExecBounded));
        assert!(session.capabilities.contains(Capability::ArtifactSubmit));

        // Must NOT have (development doesn't get git write - that's commit's job)
        assert!(!session.capabilities.contains(Capability::GitWrite));
    });
}

/// Verify Fix drain has exactly the capabilities defined in RFC-009.
///
/// Fix: write-capable but scoped (workspace read/write tracked, process exec, NO ephemeral)
///
/// Note: Fix does NOT have WorkspaceWriteEphemeral. Ralph's internal writes (like
/// ArchiveFixResultXml) are handled by is_ralph_internal_effect bypass, not by granting
/// ephemeral write capability to Fix sessions.
#[test]
fn regression_fix_drain_capability_contract() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Fix, 0);

        // Must have
        assert!(session.capabilities.contains(Capability::WorkspaceRead));
        assert!(session
            .capabilities
            .contains(Capability::WorkspaceWriteTracked));
        assert!(session.capabilities.contains(Capability::GitStatusRead));
        assert!(session.capabilities.contains(Capability::GitDiffRead));
        assert!(session
            .capabilities
            .contains(Capability::ProcessExecBounded));
        assert!(session.capabilities.contains(Capability::ArtifactSubmit));

        // Fix does NOT have WorkspaceWriteEphemeral - Ralph's internal writes
        // (like archiving XML to .agent/) are bypassed via is_ralph_internal_effect
        assert!(!session
            .capabilities
            .contains(Capability::WorkspaceWriteEphemeral));

        // Must NOT have
        assert!(!session.capabilities.contains(Capability::GitWrite));
    });
}

/// Verify Review drain has exactly the capabilities defined in RFC-009.
///
/// Review: read-only (workspace read, git read, ephemeral write for Ralph's reports)
#[test]
fn regression_review_drain_capability_contract() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Review, 0);

        // Must have
        assert!(session.capabilities.contains(Capability::WorkspaceRead));
        assert!(session
            .capabilities
            .contains(Capability::WorkspaceWriteEphemeral));
        assert!(session.capabilities.contains(Capability::GitStatusRead));
        assert!(session.capabilities.contains(Capability::GitDiffRead));
        assert!(session.capabilities.contains(Capability::ArtifactSubmit));

        // Must NOT have
        assert!(!session
            .capabilities
            .contains(Capability::WorkspaceWriteTracked));
        assert!(!session
            .capabilities
            .contains(Capability::ProcessExecBounded));
        assert!(!session.capabilities.contains(Capability::GitWrite));
    });
}

/// Verify Analysis drain has exactly the capabilities defined in RFC-009.
///
/// Analysis: read-only (same as Review)
#[test]
fn regression_analysis_drain_capability_contract() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Analysis, 0);

        // Must have
        assert!(session.capabilities.contains(Capability::WorkspaceRead));
        assert!(session
            .capabilities
            .contains(Capability::WorkspaceWriteEphemeral));
        assert!(session.capabilities.contains(Capability::GitStatusRead));
        assert!(session.capabilities.contains(Capability::GitDiffRead));
        assert!(session.capabilities.contains(Capability::ArtifactSubmit));

        // Must NOT have
        assert!(!session
            .capabilities
            .contains(Capability::WorkspaceWriteTracked));
        assert!(!session
            .capabilities
            .contains(Capability::ProcessExecBounded));
        assert!(!session.capabilities.contains(Capability::GitWrite));
    });
}

/// Verify Commit drain has exactly the capabilities defined in RFC-009.
///
/// Commit: git-write only (git read, git write, ephemeral for Ralph's commit msg files)
#[test]
fn regression_commit_drain_capability_contract() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Commit, 0);

        // Must have
        assert!(session.capabilities.contains(Capability::WorkspaceRead));
        assert!(session
            .capabilities
            .contains(Capability::WorkspaceWriteEphemeral));
        assert!(session.capabilities.contains(Capability::GitStatusRead));
        assert!(session.capabilities.contains(Capability::GitDiffRead));
        assert!(session.capabilities.contains(Capability::GitWrite));
        assert!(session.capabilities.contains(Capability::ArtifactSubmit));

        // Must NOT have
        assert!(!session
            .capabilities
            .contains(Capability::WorkspaceWriteTracked));
        assert!(!session
            .capabilities
            .contains(Capability::ProcessExecBounded));
    });
}

// =============================================================================
// Policy Flag Contracts
// =============================================================================

/// Verify Planning/Analysis/Review drains have NoEdit policy flag.
#[test]
fn regression_read_only_drains_have_no_edit_flag() {
    with_default_timeout(|| {
        for drain in &[
            SessionDrain::Planning,
            SessionDrain::Analysis,
            SessionDrain::Review,
        ] {
            let session = AgentSession::for_drain("test".to_string(), *drain, 0);
            assert!(
                session.policy_flags.contains(PolicyFlag::NoEdit),
                "{:?} drain should have NoEdit flag",
                drain
            );
        }
    });
}

/// Verify Development/Fix drains have AllowShell policy flag.
#[test]
fn regression_write_capable_drains_have_allow_shell_flag() {
    with_default_timeout(|| {
        for drain in &[SessionDrain::Development, SessionDrain::Fix] {
            let session = AgentSession::for_drain("test".to_string(), *drain, 0);
            assert!(
                session.policy_flags.contains(PolicyFlag::AllowShell),
                "{:?} drain should have AllowShell flag",
                drain
            );
        }
    });
}

/// Verify Commit drain has AllowGitWrite policy flag.
#[test]
fn regression_commit_drain_has_allow_git_write_flag() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Commit, 0);
        assert!(
            session.policy_flags.contains(PolicyFlag::AllowGitWrite),
            "Commit drain should have AllowGitWrite flag"
        );
    });
}

// =============================================================================
// Effect-Capability Mapping
// =============================================================================

/// Verify planning effects require only capabilities available to Planning drain.
#[test]
fn regression_planning_effects_require_planning_capabilities() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Planning, 0);

        let planning_effects = [
            Effect::PreparePlanningPrompt {
                iteration: 1,
                prompt_mode: PromptMode::Normal,
            },
            Effect::MaterializePlanningInputs { iteration: 1 },
            Effect::InvokePlanningAgent { iteration: 1 },
            Effect::ExtractPlanningXml { iteration: 1 },
            Effect::ValidatePlanningXml { iteration: 1 },
            Effect::WritePlanningMarkdown { iteration: 1 },
            Effect::ArchivePlanningXml { iteration: 1 },
        ];

        for effect in planning_effects {
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Planning session should allow {:?}: {:?}",
                effect,
                outcome
            );
        }
    });
}

/// Verify Development effects are denied for Planning session.
#[test]
fn regression_planning_denies_development_effects() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Planning, 0);

        // These require WorkspaceWriteTracked or ProcessExecBounded
        let dev_effects = [
            Effect::InvokeDevelopmentAgent { iteration: 1 },
            Effect::InvokeFixAgent { pass: 1 },
        ];

        for effect in dev_effects {
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Denied { .. }),
                "Planning session should deny {:?}: {:?}",
                effect,
                outcome
            );
        }
    });
}

/// Verify Development session allows development effects.
#[test]
fn regression_development_allows_development_effects() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Development, 0);

        let effects = [
            Effect::InvokeDevelopmentAgent { iteration: 1 },
            Effect::InvokeAnalysisAgent { iteration: 1 },
        ];

        for effect in effects {
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Development session should allow {:?}: {:?}",
                effect,
                outcome
            );
        }
    });
}

/// Verify Commit session denies workspace write effects.
#[test]
fn regression_commit_denies_workspace_write() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Commit, 0);

        // Commit session should allow git operations but not workspace writes
        let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Commit session should deny InvokeDevelopmentAgent: {:?}",
            outcome
        );
    });
}

/// Verify Commit session allows commit effects.
#[test]
fn regression_commit_allows_commit_effects() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Commit, 0);

        let effects = [
            Effect::CheckCommitDiff,
            Effect::InvokeCommitAgent,
            Effect::CreateCommit {
                message: "test".to_string(),
                files: vec![],
                excluded_files: vec![],
            },
        ];

        for effect in effects {
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Commit session should allow {:?}: {:?}",
                effect,
                outcome
            );
        }
    });
}

// =============================================================================
// Ralph-Internal Effect Bypass
// =============================================================================

/// Verify parallel effects are marked as Ralph-internal.
#[test]
fn regression_parallel_effects_are_ralph_internal() {
    with_default_timeout(|| {
        let eval_effect = Effect::EvaluateParallelPlan {
            plan: ParallelPlan {
                parent_plan_id: "test".to_string(),
                work_units: vec![],
            },
        };
        assert!(
            is_ralph_internal_effect(&eval_effect),
            "EvaluateParallelPlan should be Ralph-internal"
        );

        let dispatch_effect = Effect::DispatchParallelWorkers {
            plan: ParallelPlan {
                parent_plan_id: "test".to_string(),
                work_units: vec![],
            },
        };
        assert!(
            is_ralph_internal_effect(&dispatch_effect),
            "DispatchParallelWorkers should be Ralph-internal"
        );
    });
}

/// Verify lifecycle effects are marked as Ralph-internal.
#[test]
fn regression_lifecycle_effects_are_ralph_internal() {
    with_default_timeout(|| {
        let internal_effects = [
            Effect::InitializeAgentChain {
                drain: ralph_workflow::agents::AgentDrain::Planning,
            },
            Effect::SaveCheckpoint {
                trigger: ralph_workflow::reducer::event::CheckpointTrigger::Interrupt,
            },
            Effect::ValidateFinalState,
            Effect::CleanupContext,
        ];

        for effect in internal_effects {
            assert!(
                is_ralph_internal_effect(&effect),
                "{:?} should be Ralph-internal",
                effect
            );
        }
    });
}

// =============================================================================
// Session Handshake
// =============================================================================

/// Verify session handshake contains all required fields.
#[test]
fn regression_session_handshake_has_required_fields() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("run-123".to_string(), SessionDrain::Development, 5);
        let handshake = SessionHandshake::from_session(&session);

        assert_eq!(handshake.session_id.as_str(), "run-123-development-5");
        assert_eq!(handshake.drain, SessionDrain::Development);
        assert_eq!(handshake.protocol_version, "ralph-mcp/1.0");
        assert!(handshake.worker_identity.is_none()); // Single-agent
        assert!(handshake.edit_area.is_none()); // Single-agent
    });
}

/// Verify parallel worker handshake includes worker identity and edit area.
#[test]
fn regression_parallel_worker_handshake_has_worker_info() {
    with_default_timeout(|| {
        let edit_area = RestrictedEditArea::paths(vec!["src/feature.rs".to_string()]);
        let worker_identity = ralph_workflow::agents::session::WorkerIdentity {
            worker_id: "worker-1".to_string(),
            parent_session_id: AgentSessionId::new("run-123", &SessionDrain::Development, 0),
            work_unit_id: "unit-1".to_string(),
            branch_name: "parallel/run-123/unit-1".to_string(),
        };

        let session = AgentSession::for_parallel_worker(
            "run-123".to_string(),
            SessionDrain::Development,
            1,
            worker_identity.clone(),
            edit_area.clone(),
            std::time::SystemTime::now(),
        );

        let handshake = SessionHandshake::from_session(&session);

        assert!(handshake.worker_identity.is_some());
        assert!(handshake.edit_area.is_some());
        assert_eq!(
            handshake.worker_identity.as_ref().unwrap().worker_id,
            "worker-1"
        );
    });
}

// =============================================================================
// Audit Trail
// =============================================================================

/// Verify audit trail records capability checks.
#[test]
fn regression_audit_trail_records_capability_checks() {
    with_default_timeout(|| {
        use ralph_workflow::agents::session::audit::{record_effect_check, serialize_audit_trail};

        let trail = AuditTrail::new();
        let session_id = AgentSessionId::new("test", &SessionDrain::Planning, 0);
        let caps = vec![Capability::WorkspaceRead, Capability::ArtifactSubmit];
        let outcome = PolicyOutcome::Approved;

        let new_trail = record_effect_check(
            &trail,
            &session_id,
            1700000000,
            "TestEffect",
            &caps,
            &outcome,
        );

        assert_eq!(new_trail.len(), 2);

        // Verify serialization works
        let serialized = serialize_audit_trail(&new_trail);
        assert!(!serialized.is_empty());
    });
}

/// Verify audit trail records command checks.
#[test]
fn regression_audit_trail_records_command_checks() {
    with_default_timeout(|| {
        use ralph_workflow::agents::session::audit::{record_command_check, serialize_audit_trail};

        let trail = AuditTrail::new();
        let session_id = AgentSessionId::new("test", &SessionDrain::Development, 0);
        let outcome = PolicyOutcome::Approved;

        let new_trail =
            record_command_check(&trail, &session_id, 1700000000, "cargo test", &outcome);

        assert_eq!(new_trail.len(), 1);

        let serialized = serialize_audit_trail(&new_trail);
        assert!(serialized.contains("cargo test"));
    });
}

// =============================================================================
// Edit Area Isolation
// =============================================================================

/// Verify single-agent session has no edit area restriction.
#[test]
fn regression_single_agent_no_edit_area_restriction() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Development, 0);
        assert!(session.edit_area.is_none());
        assert!(!session.is_parallel_worker());
    });
}

/// Verify parallel worker session has edit area restriction.
#[test]
fn regression_parallel_worker_has_edit_area_restriction() {
    with_default_timeout(|| {
        let edit_area = RestrictedEditArea::paths(vec!["src/feature.rs".to_string()]);
        let session = AgentSession::for_parallel_worker(
            "test".to_string(),
            SessionDrain::Development,
            0,
            ralph_workflow::agents::session::WorkerIdentity {
                worker_id: "worker-1".to_string(),
                parent_session_id: AgentSessionId::new("test", &SessionDrain::Development, 0),
                work_unit_id: "unit-1".to_string(),
                branch_name: "parallel/test/unit-1".to_string(),
            },
            edit_area,
            std::time::SystemTime::now(),
        );

        assert!(session.edit_area.is_some());
        assert!(session.is_parallel_worker());

        // Within edit area - should be allowed
        let within_outcome = session.check_edit_area("src/feature.rs");
        assert!(matches!(within_outcome, PolicyOutcome::Approved));

        // Outside edit area - should be denied
        let outside_outcome = session.check_edit_area("src/other.rs");
        assert!(matches!(outside_outcome, PolicyOutcome::Denied { .. }));
    });
}

/// Verify edit area checking doesn't affect single-agent sessions.
#[test]
fn regression_single_agent_edit_area_check_passes() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Development, 0);

        // Single-agent should allow all paths (no edit area restriction)
        let outcome = session.check_edit_area("any/path/at/all");
        assert!(matches!(outcome, PolicyOutcome::Approved));
    });
}

// =============================================================================
// Effect Kind Classification
// =============================================================================

/// Verify effect kinds are classified correctly.
#[test]
fn regression_effect_kind_classification() {
    with_default_timeout(|| {
        // Read effects
        assert_eq!(
            effect_kind(&Effect::MaterializePlanningInputs { iteration: 1 }),
            EffectKind::WorkspaceRead
        );

        // Write effects
        assert_eq!(
            effect_kind(&Effect::WritePlanningMarkdown { iteration: 1 }),
            EffectKind::WorkspaceWrite
        );

        // Git effects
        assert_eq!(effect_kind(&Effect::CheckCommitDiff), EffectKind::GitRead);
        assert_eq!(
            effect_kind(&Effect::CreateCommit {
                message: "test".to_string(),
                files: vec![],
                excluded_files: vec![],
            }),
            EffectKind::GitWrite
        );

        // Agent invocation
        assert_eq!(
            effect_kind(&Effect::InvokePlanningAgent { iteration: 1 }),
            EffectKind::AgentInvocation
        );
    });
}

/// Verify effect names are formatted correctly.
#[test]
fn regression_effect_name_format() {
    with_default_timeout(|| {
        let effect = Effect::InvokeDevelopmentAgent { iteration: 42 };
        let name = effect_name(&effect);
        assert!(name.contains("InvokeDevelopmentAgent"));
        assert!(name.contains("42"));
    });
}

// =============================================================================
// Edit Area Overlap Detection
// =============================================================================

/// Verify overlapping edit areas are detected.
#[test]
fn regression_edit_area_overlap_detection() {
    with_default_timeout(|| {
        let area1 = RestrictedEditArea::paths(vec!["src/lib.rs".to_string()]);
        let area2 = RestrictedEditArea::paths(vec!["src/lib.rs".to_string()]);

        assert!(edit_areas_overlap(&area1, &area2));
    });
}

/// Verify non-overlapping edit areas pass.
#[test]
fn regression_edit_area_no_overlap() {
    with_default_timeout(|| {
        let area1 = RestrictedEditArea::paths(vec!["src/a.rs".to_string()]);
        let area2 = RestrictedEditArea::paths(vec!["src/b.rs".to_string()]);

        assert!(!edit_areas_overlap(&area1, &area2));
    });
}

/// Verify directory prefix overlap is detected.
#[test]
fn regression_edit_area_directory_overlap() {
    with_default_timeout(|| {
        let area1 = RestrictedEditArea::paths(vec!["src/lib.rs".to_string()]);
        let area2 = RestrictedEditArea::directory("src");

        assert!(edit_areas_overlap(&area1, &area2));
    });
}

// =============================================================================
// Session ID Generation
// =============================================================================

/// Verify session IDs are unique per drain/counter combination.
#[test]
fn regression_session_id_uniqueness() {
    with_default_timeout(|| {
        let id1 = AgentSessionId::new("run-1", &SessionDrain::Development, 0);
        let id2 = AgentSessionId::new("run-1", &SessionDrain::Development, 1);
        let id3 = AgentSessionId::new("run-1", &SessionDrain::Planning, 0);

        assert_ne!(id1.as_str(), id2.as_str());
        assert_ne!(id1.as_str(), id3.as_str());
        assert_ne!(id2.as_str(), id3.as_str());
    });
}

/// Verify session ID format.
#[test]
fn regression_session_id_format() {
    with_default_timeout(|| {
        let id = AgentSessionId::new("my-run", &SessionDrain::Fix, 3);
        assert_eq!(id.as_str(), "my-run-fix-3");
    });
}

// =============================================================================
// No Regression: Existing Behavior Preserved
// =============================================================================

/// Verify Capability enum has all expected variants.
#[test]
fn regression_capability_enum_completeness() {
    with_default_timeout(|| {
        // All capabilities from RFC-009 should be present
        let _ = Capability::WorkspaceRead;
        let _ = Capability::WorkspaceWriteEphemeral;
        let _ = Capability::WorkspaceWriteTracked;
        let _ = Capability::ProcessExecBounded;
        let _ = Capability::ArtifactSubmit;
        let _ = Capability::RunReportProgress;
        let _ = Capability::GitStatusRead;
        let _ = Capability::GitDiffRead;
        let _ = Capability::GitWrite;
        let _ = Capability::EnvRead;
    });
}

/// Verify all SessionDrain variants exist.
#[test]
fn regression_session_drain_completeness() {
    with_default_timeout(|| {
        let _ = SessionDrain::Planning;
        let _ = SessionDrain::Development;
        let _ = SessionDrain::Analysis;
        let _ = SessionDrain::Review;
        let _ = SessionDrain::Fix;
        let _ = SessionDrain::Commit;
    });
}

/// Verify PolicyOutcome variants work correctly.
#[test]
fn regression_policy_outcome_variants() {
    with_default_timeout(|| {
        let approved = PolicyOutcome::Approved;
        assert!(matches!(approved, PolicyOutcome::Approved));

        let denied = PolicyOutcome::Denied {
            reason: "test".to_string(),
        };
        assert!(matches!(denied, PolicyOutcome::Denied { .. }));

        let restricted = PolicyOutcome::ApprovedWithRestriction {
            restriction: "test".to_string(),
        };
        assert!(matches!(
            restricted,
            PolicyOutcome::ApprovedWithRestriction { .. }
        ));
    });
}

/// Verify capability identifiers are stable.
#[test]
fn regression_capability_identifiers() {
    with_default_timeout(|| {
        assert_eq!(Capability::WorkspaceRead.identifier(), "workspace.read");
        assert_eq!(
            Capability::WorkspaceWriteEphemeral.identifier(),
            "workspace.write_ephemeral"
        );
        assert_eq!(
            Capability::WorkspaceWriteTracked.identifier(),
            "workspace.write_tracked"
        );
        assert_eq!(
            Capability::ProcessExecBounded.identifier(),
            "process.exec_bounded"
        );
        assert_eq!(Capability::GitWrite.identifier(), "git.write");
    });
}
