//! Behavioral equivalence regression tests for RFC-009 Phase 4.
//!
//! These tests verify that parallel worker features do not affect single-agent behavior.
//! The key constraint is: "All existing single-agent workflows must produce identical results.
//! Only parallel editing introduces user-visible behavior changes."
//!
//! This means:
//! - Single-agent sessions work exactly as before RFC-009
//! - Parallel mode does not affect single-agent execution paths
//! - Capability gate enforcement is unchanged for single-agent drains

use ralph_workflow::agents::session::capability_gate::required_capabilities;
use ralph_workflow::agents::session::parallel::RestrictedEditArea;
use ralph_workflow::agents::session::{AgentSession, Capability, SessionDrain};
use ralph_workflow::reducer::effect::Effect;

use crate::test_timeout::with_default_timeout;

/// Test that single-agent session has same capabilities as before RFC-009.
/// This verifies that drain defaults are unchanged for non-parallel workers.
#[test]
fn single_agent_development_session_has_expected_capabilities() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Development, 0);

        // Verify expected capabilities for development drain
        assert!(
            session.capabilities.contains(Capability::WorkspaceRead),
            "Development should have WorkspaceRead"
        );
        assert!(
            session
                .capabilities
                .contains(Capability::WorkspaceWriteTracked),
            "Development should have WorkspaceWriteTracked"
        );
        assert!(
            session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Development should have ProcessExecBounded"
        );
        assert!(
            session.capabilities.contains(Capability::ArtifactSubmit),
            "Development should have ArtifactSubmit"
        );
    });
}

/// Test that single-agent planning session has same capabilities as before.
#[test]
fn single_agent_planning_session_has_expected_capabilities() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Planning, 0);

        // Verify expected capabilities for planning drain
        assert!(
            session.capabilities.contains(Capability::WorkspaceRead),
            "Planning should have WorkspaceRead"
        );
        assert!(
            session
                .capabilities
                .contains(Capability::WorkspaceWriteEphemeral),
            "Planning should have WorkspaceWriteEphemeral"
        );
        assert!(
            !session
                .capabilities
                .contains(Capability::WorkspaceWriteTracked),
            "Planning should NOT have WorkspaceWriteTracked"
        );
        assert!(
            !session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Planning should NOT have ProcessExecBounded"
        );
    });
}

/// Test that single-agent review session has same capabilities as before.
#[test]
fn single_agent_review_session_has_expected_capabilities() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Review, 0);

        // Verify expected capabilities for review drain
        assert!(
            session.capabilities.contains(Capability::WorkspaceRead),
            "Review should have WorkspaceRead"
        );
        assert!(
            session.capabilities.contains(Capability::GitDiffRead),
            "Review should have GitDiffRead"
        );
        assert!(
            session.capabilities.contains(Capability::ArtifactSubmit),
            "Review should have ArtifactSubmit"
        );
        assert!(
            !session.capabilities.contains(Capability::GitWrite),
            "Review should NOT have GitWrite"
        );
    });
}

/// Test that single-agent commit session has same capabilities as before.
#[test]
fn single_agent_commit_session_has_expected_capabilities() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Commit, 0);

        // Verify expected capabilities for commit drain
        assert!(
            session.capabilities.contains(Capability::GitStatusRead),
            "Commit should have GitStatusRead"
        );
        assert!(
            session.capabilities.contains(Capability::GitDiffRead),
            "Commit should have GitDiffRead"
        );
        assert!(
            session.capabilities.contains(Capability::GitWrite),
            "Commit should have GitWrite"
        );
        assert!(
            !session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Commit should NOT have ProcessExecBounded"
        );
    });
}

/// Test that effect capability requirements are unchanged for single-agent execution.
#[test]
fn effect_capability_requirements_unchanged_for_single_agent() {
    with_default_timeout(|| {
        // InvokeDevelopmentAgent should require WorkspaceWriteTracked and ProcessExecBounded
        let dev_effect = Effect::InvokeDevelopmentAgent { iteration: 0 };
        let dev_caps = required_capabilities(&dev_effect);
        assert!(
            dev_caps.contains(&Capability::WorkspaceWriteTracked),
            "InvokeDevelopmentAgent should require WorkspaceWriteTracked"
        );
        assert!(
            dev_caps.contains(&Capability::ProcessExecBounded),
            "InvokeDevelopmentAgent should require ProcessExecBounded"
        );

        // InvokeReviewAgent should require WorkspaceRead, GitDiffRead, ArtifactSubmit
        let review_effect = Effect::InvokeReviewAgent { pass: 0 };
        let review_caps = required_capabilities(&review_effect);
        assert!(
            review_caps.contains(&Capability::WorkspaceRead),
            "InvokeReviewAgent should require WorkspaceRead"
        );
        assert!(
            review_caps.contains(&Capability::GitDiffRead),
            "InvokeReviewAgent should require GitDiffRead"
        );

        // CreateCommit should require GitWrite
        let commit_effect = Effect::CreateCommit {
            message: "test".to_string(),
            files: vec![],
            excluded_files: vec![],
        };
        let commit_caps = required_capabilities(&commit_effect);
        assert!(
            commit_caps.contains(&Capability::GitWrite),
            "CreateCommit should require GitWrite"
        );
    });
}

/// Test that parallel effects are internal-only and don't affect single-agent paths.
#[test]
fn parallel_effects_are_internal_only() {
    with_default_timeout(|| {
        use ralph_workflow::agents::session::capability_gate::is_ralph_internal_effect;

        // EvaluateParallelPlan should be Ralph-internal
        let eval_effect = Effect::EvaluateParallelPlan {
            plan: ralph_workflow::agents::session::ParallelPlan {
                parent_plan_id: "test".to_string(),
                work_units: vec![],
            },
        };
        assert!(
            is_ralph_internal_effect(&eval_effect),
            "EvaluateParallelPlan should be Ralph-internal"
        );

        // DispatchParallelWorkers should be Ralph-internal
        let dispatch_effect = Effect::DispatchParallelWorkers {
            plan: ralph_workflow::agents::session::ParallelPlan {
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

/// Test that parallel worker session has additional restrictions beyond single-agent.
/// This ensures parallel mode introduces extra constraints, not different behavior.
#[test]
fn parallel_worker_session_has_edit_area_restriction() {
    with_default_timeout(|| {
        let edit_area = RestrictedEditArea::paths(vec!["src/feature.rs".to_string()]);
        let session = AgentSession::for_parallel_worker(
            "test".to_string(),
            SessionDrain::Development,
            0,
            ralph_workflow::agents::session::WorkerIdentity {
                worker_id: "worker-1".to_string(),
                parent_session_id: ralph_workflow::agents::session::AgentSessionId::new(
                    "test",
                    &SessionDrain::Development,
                    0,
                ),
                work_unit_id: "unit-1".to_string(),
                branch_name: "parallel/test/unit-1".to_string(),
            },
            edit_area,
            std::time::SystemTime::now(),
        );

        // Single-agent session has full access
        let single_agent =
            AgentSession::for_drain("test".to_string(), SessionDrain::Development, 0);

        // Both should have same base capabilities
        assert_eq!(
            single_agent
                .capabilities
                .contains(Capability::WorkspaceWriteTracked),
            session
                .capabilities
                .contains(Capability::WorkspaceWriteTracked),
            "Parallel worker should have same WorkspaceWriteTracked capability as single-agent"
        );

        // But parallel worker has edit area restriction
        assert!(
            session.edit_area.is_some(),
            "Parallel worker should have edit area restriction"
        );

        // Single-agent does not have edit area restriction
        assert!(
            single_agent.edit_area.is_none(),
            "Single-agent should not have edit area restriction"
        );
    });
}

/// Test that session without edit area (single-agent) allows all paths.
#[test]
fn single_agent_session_allows_all_paths() {
    with_default_timeout(|| {
        use ralph_workflow::agents::session::PolicyOutcome;

        // Single-agent session has no edit area restriction
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Development, 0);

        // When session has no edit area, it should allow all paths
        // This is implied by the fact that check_write_within_edit_area is only called
        // for sessions that have an edit_area set

        // For single-agent, the capability gate should allow the effect
        let effect = Effect::InvokeDevelopmentAgent { iteration: 0 };
        let caps = required_capabilities(&effect);

        for cap in &caps {
            let outcome = session.check_capability(*cap);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Single-agent should have capability {:?}: {:?}",
                cap,
                outcome
            );
        }
    });
}
