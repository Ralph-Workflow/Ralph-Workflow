//! Integration tests for capability gate enforcement in the pipeline.
//!
//! These tests verify that the capability gate correctly denies effects
//! that require capabilities the session doesn't have, using the session
//! model types directly.
//!
//! Unlike the unit tests in `reducer/boundary/tests/capability_gate_enforcement.rs`
//! which test `MainEffectHandler::execute` directly, these tests verify the
//! session capability model used by the capability gate.

use ralph_workflow::agents::session::capability_gate::required_capabilities;
use ralph_workflow::agents::session::{AgentSession, Capability, SessionDrain};

use crate::test_timeout::with_default_timeout;

/// Test that Development session has required capabilities for development effects.
#[test]
fn development_session_has_process_exec_capability() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test-dev".to_string(), SessionDrain::Development, 0);

        assert!(
            session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Development should have ProcessExecBounded"
        );
        assert!(
            session
                .capabilities
                .contains(Capability::WorkspaceWriteTracked),
            "Development should have WorkspaceWriteTracked"
        );
    });
}

/// Test that Planning session does NOT have ProcessExecBounded.
#[test]
fn planning_session_lacks_process_exec() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("test-planning".to_string(), SessionDrain::Planning, 0);

        assert!(
            !session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Planning should NOT have ProcessExecBounded"
        );
    });
}

/// Test that Commit session has GitWrite but not ProcessExecBounded.
#[test]
fn commit_session_git_write_without_process_exec() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test-commit".to_string(), SessionDrain::Commit, 0);

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

/// Test that Fix session has WorkspaceWriteTracked and ProcessExecBounded.
#[test]
fn fix_session_has_write_and_exec() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test-fix".to_string(), SessionDrain::Fix, 0);

        assert!(
            session
                .capabilities
                .contains(Capability::WorkspaceWriteTracked),
            "Fix should have WorkspaceWriteTracked"
        );
        assert!(
            session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Fix should have ProcessExecBounded"
        );
    });
}

/// Test that Review session allows InvokeReviewAgent effect.
#[test]
fn review_session_allows_review_effect() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test-review".to_string(), SessionDrain::Review, 0);

        // Review should allow InvokeReviewAgent (only requires ArtifactSubmit)
        assert!(
            session.capabilities.contains(Capability::ArtifactSubmit),
            "Review should have ArtifactSubmit for InvokeReviewAgent"
        );

        // Review should NOT have GitWrite
        assert!(
            !session.capabilities.contains(Capability::GitWrite),
            "Review should NOT have GitWrite"
        );
    });
}

/// Test that Analysis session allows InvokeAnalysisAgent effect.
#[test]
fn analysis_session_allows_analysis_effect() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("test-analysis".to_string(), SessionDrain::Analysis, 0);

        // Analysis should have ArtifactSubmit for InvokeAnalysisAgent
        assert!(
            session.capabilities.contains(Capability::ArtifactSubmit),
            "Analysis should have ArtifactSubmit"
        );

        // Analysis should NOT have ProcessExecBounded (no dev agent)
        assert!(
            !session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Analysis should NOT have ProcessExecBounded"
        );
    });
}

/// Test capability requirements for different effects.
#[test]
fn effect_capability_requirements() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::effect::Effect;

        // InvokeDevelopmentAgent requires WorkspaceWriteTracked + ProcessExecBounded
        let dev_caps = required_capabilities(&Effect::InvokeDevelopmentAgent { iteration: 0 });
        assert!(
            dev_caps.contains(&Capability::WorkspaceWriteTracked),
            "InvokeDevelopmentAgent requires WorkspaceWriteTracked"
        );
        assert!(
            dev_caps.contains(&Capability::ProcessExecBounded),
            "InvokeDevelopmentAgent requires ProcessExecBounded"
        );

        // CreateCommit requires GitWrite
        let commit_caps = required_capabilities(&Effect::CreateCommit {
            message: "test".to_string(),
            files: vec![],
            excluded_files: vec![],
        });
        assert!(
            commit_caps.contains(&Capability::GitWrite),
            "CreateCommit requires GitWrite"
        );

        // InvokePlanningAgent only requires ArtifactSubmit
        let plan_caps = required_capabilities(&Effect::InvokePlanningAgent { iteration: 0 });
        assert!(
            plan_caps.contains(&Capability::ArtifactSubmit),
            "InvokePlanningAgent requires ArtifactSubmit"
        );
    });
}

/// Test that Fix session capabilities satisfy InvokeFixAgent requirements.
#[test]
fn fix_session_satisfies_fix_agent_requirements() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::effect::Effect;

        let session = AgentSession::for_drain("test-fix".to_string(), SessionDrain::Fix, 0);
        let fix_caps = required_capabilities(&Effect::InvokeFixAgent { pass: 1 });

        // Fix agent requires WorkspaceWriteTracked and ProcessExecBounded
        assert!(
            session
                .capabilities
                .contains(Capability::WorkspaceWriteTracked),
            "Fix session should have WorkspaceWriteTracked"
        );
        assert!(
            session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Fix session should have ProcessExecBounded"
        );

        // Verify all required capabilities are present
        for cap in &fix_caps {
            assert!(
                session.capabilities.contains(*cap),
                "Fix session should have {:?} capability",
                cap
            );
        }
    });
}

/// Test that Planning session capabilities do NOT satisfy InvokeFixAgent requirements.
#[test]
fn planning_session_lacks_fix_agent_requirements() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::effect::Effect;

        let session =
            AgentSession::for_drain("test-planning".to_string(), SessionDrain::Planning, 0);
        let fix_caps = required_capabilities(&Effect::InvokeFixAgent { pass: 1 });

        // Planning should lack at least one required capability for InvokeFixAgent
        let mut lacks_capability = false;
        for cap in &fix_caps {
            if !session.capabilities.contains(*cap) {
                lacks_capability = true;
                break;
            }
        }
        assert!(
            lacks_capability,
            "Planning session should NOT satisfy all InvokeFixAgent requirements"
        );
    });
}
