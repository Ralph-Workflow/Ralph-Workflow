//! Tests for capability gate enforcement in MainEffectHandler.
//!
//! These tests verify that the capability gate correctly denies effects
//! that require capabilities the session doesn't have.

use crate::agents::session::{AgentSession, SessionDrain};
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::effect::{Effect, EffectHandler, EffectResult};
use crate::reducer::event::{AgentEvent, PipelineEvent};
use crate::reducer::state::PipelineState;
use crate::workspace::MemoryWorkspace;

use super::common::TestFixture;

/// Create a session for the given drain.
fn session_for_drain(drain: SessionDrain) -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), drain, 1)
}

/// Execute an effect with the given session active and return the result.
fn execute_with_session(effect: Effect, session: AgentSession) -> anyhow::Result<EffectResult> {
    execute_with_session_and_state(effect, session, PipelineState::initial(1, 0))
}

fn execute_with_session_and_state(
    effect: Effect,
    session: AgentSession,
    state: PipelineState,
) -> anyhow::Result<EffectResult> {
    let workspace = MemoryWorkspace::new_test();
    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.active_session = Some(session);
    ctx.audit_trail = crate::agents::session::AuditTrail::new();

    let mut handler = MainEffectHandler::new(state);
    handler.execute(effect, &mut ctx)
}

// =============================================================================
// Read-only drain denial tests
// =============================================================================

/// Planning session should deny InvokeDevelopmentAgent (requires WorkspaceWriteTracked + ProcessExecBounded).
#[test]
fn planning_denies_development_effect() {
    let session = session_for_drain(SessionDrain::Planning);
    let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };

    let result = execute_with_session(effect, session).unwrap();

    // Should return CapabilityDenied event
    match &result.event {
        PipelineEvent::Agent(AgentEvent::CapabilityDenied {
            capability,
            reason: _,
            ..
        }) => {
            assert!(
                capability.contains("workspace.write_tracked") || capability.contains("process.exec_bounded"),
                "Should deny due to missing WorkspaceWriteTracked or ProcessExecBounded, got: {capability}"
            );
        }
        other => {
            panic!(
                "Expected CapabilityDenied event for Planning session executing InvokeDevelopmentAgent, got: {:?}",
                other
            );
        }
    }
}

/// Planning session should deny CreateCommit (requires GitWrite).
#[test]
fn planning_denies_git_write() {
    let session = session_for_drain(SessionDrain::Planning);
    let effect = Effect::CreateCommit {
        message: "test".to_string(),
        files: vec![],
        excluded_files: vec![],
    };

    let result = execute_with_session(effect, session).unwrap();

    match &result.event {
        PipelineEvent::Agent(AgentEvent::CapabilityDenied { capability, .. }) => {
            assert!(
                capability.contains("git.write"),
                "Should deny due to missing GitWrite, got: {capability}"
            );
        }
        other => {
            panic!(
                "Expected CapabilityDenied event for Planning session executing CreateCommit, got: {:?}",
                other
            );
        }
    }
}

/// Analysis session should deny InvokeDevelopmentAgent.
#[test]
fn analysis_denies_development_effect() {
    let session = session_for_drain(SessionDrain::Analysis);
    let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };

    let result = execute_with_session(effect, session).unwrap();

    match &result.event {
        PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. }) => {}
        other => {
            panic!(
                "Expected CapabilityDenied event for Analysis session executing InvokeDevelopmentAgent, got: {:?}",
                other
            );
        }
    }
}

/// Review session should deny CreateCommit.
#[test]
fn review_denies_git_write() {
    let session = session_for_drain(SessionDrain::Review);
    let effect = Effect::CreateCommit {
        message: "test".to_string(),
        files: vec![],
        excluded_files: vec![],
    };

    let result = execute_with_session(effect, session).unwrap();

    match &result.event {
        PipelineEvent::Agent(AgentEvent::CapabilityDenied { capability, .. }) => {
            assert!(
                capability.contains("git.write"),
                "Should deny due to missing GitWrite, got: {capability}"
            );
        }
        other => {
            panic!(
                "Expected CapabilityDenied event for Review session executing CreateCommit, got: {:?}",
                other
            );
        }
    }
}

/// Commit session should deny InvokeDevelopmentAgent (requires WorkspaceWriteTracked).
#[test]
fn commit_denies_workspace_write_tracked() {
    let session = session_for_drain(SessionDrain::Commit);
    let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };

    let result = execute_with_session(effect, session).unwrap();

    match &result.event {
        PipelineEvent::Agent(AgentEvent::CapabilityDenied { capability, .. }) => {
            assert!(
                capability.contains("workspace.write_tracked"),
                "Should deny due to missing WorkspaceWriteTracked, got: {capability}"
            );
        }
        other => {
            panic!(
                "Expected CapabilityDenied event for Commit session executing InvokeDevelopmentAgent, got: {:?}",
                other
            );
        }
    }
}

/// Planning session should deny InvokeFixAgent (requires WorkspaceWriteTracked + ProcessExecBounded).
#[test]
fn planning_denies_fix_effect() {
    let session = session_for_drain(SessionDrain::Planning);
    let effect = Effect::InvokeFixAgent { pass: 1 };

    let result = execute_with_session(effect, session).unwrap();

    match &result.event {
        PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. }) => {}
        other => {
            panic!(
                "Expected CapabilityDenied event for Planning session executing InvokeFixAgent, got: {:?}",
                other
            );
        }
    }
}

// =============================================================================
// Permitted operation tests (behavioral equivalence)
// =============================================================================

/// Planning session should allow InvokePlanningAgent (only requires ArtifactSubmit).
#[test]
fn planning_allows_planning_effects() {
    let session = session_for_drain(SessionDrain::Planning);
    let effect = Effect::InvokePlanningAgent { iteration: 1 };

    let result = execute_with_session(effect, session);

    // Should NOT be CapabilityDenied - planning can invoke planning agent
    // It may fail for other reasons (missing prompt file), but NOT capability denial
    if let Ok(result) = result {
        if let PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. }) = &result.event {
            panic!("Planning session should allow InvokePlanningAgent");
        }
    }
    // If Err, that's also acceptable - it failed for non-capability reasons
}

/// Development session should allow InvokeDevelopmentAgent.
#[test]
fn development_allows_development_effects() {
    let session = session_for_drain(SessionDrain::Development);
    let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };

    let result = execute_with_session(effect, session);

    // Should NOT be CapabilityDenied - development can invoke development agent
    if let Ok(result) = result {
        if let PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. }) = &result.event {
            panic!("Development session should allow InvokeDevelopmentAgent");
        }
    }
    // If Err, that's also acceptable - it failed for non-capability reasons
}

/// Commit session should allow CreateCommit.
#[test]
fn commit_drain_allows_create() {
    let session = session_for_drain(SessionDrain::Commit);
    let effect = Effect::CreateCommit {
        message: "test".to_string(),
        files: vec![],
        excluded_files: vec![],
    };

    let state = PipelineState {
        phase: crate::reducer::event::PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::Generated {
            message: "test".to_string(),
        },
        commit_xml_archived: true,
        ..PipelineState::initial(1, 0)
    };

    let result = execute_with_session_and_state(effect, session, state);

    // Should NOT be CapabilityDenied - commit can create commits
    if let Ok(result) = result {
        if let PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. }) = &result.event {
            panic!("Commit session should allow CreateCommit");
        }
    }
    // If Err, that's also acceptable - it failed for non-capability reasons
}

/// Fix session should allow InvokeFixAgent.
#[test]
fn fix_allows_fix_effects() {
    let session = session_for_drain(SessionDrain::Fix);
    let effect = Effect::InvokeFixAgent { pass: 1 };

    let result = execute_with_session(effect, session);

    // Should NOT be CapabilityDenied - fix can invoke fix agent
    if let Ok(result) = result {
        if let PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. }) = &result.event {
            panic!("Fix session should allow InvokeFixAgent");
        }
    }
    // If Err, that's also acceptable - it failed for non-capability reasons
}

/// Review session should allow InvokeReviewAgent.
#[test]
fn review_allows_review_effects() {
    let session = session_for_drain(SessionDrain::Review);
    let effect = Effect::InvokeReviewAgent { pass: 1 };

    let result = execute_with_session(effect, session);

    // Should NOT be CapabilityDenied - review can invoke review agent
    if let Ok(result) = result {
        if let PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. }) = &result.event {
            panic!("Review session should allow InvokeReviewAgent");
        }
    }
    // If Err, that's also acceptable - it failed for non-capability reasons
}

/// Analysis session should allow InvokeAnalysisAgent.
#[test]
fn analysis_allows_analysis_effects() {
    let session = session_for_drain(SessionDrain::Analysis);
    let effect = Effect::InvokeAnalysisAgent { iteration: 1 };

    let result = execute_with_session(effect, session);

    // Should NOT be CapabilityDenied - analysis can invoke analysis agent
    if let Ok(result) = result {
        if let PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. }) = &result.event {
            panic!("Analysis session should allow InvokeAnalysisAgent");
        }
    }
    // If Err, that's also acceptable - it failed for non-capability reasons
}

// =============================================================================
// No session tests (backward compatibility)
// =============================================================================

/// Effects should execute normally when no session is active.
#[test]
fn no_session_allows_all_effects() {
    let workspace = MemoryWorkspace::new_test();
    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    // No active session set
    ctx.audit_trail = crate::agents::session::AuditTrail::new();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));

    // This should NOT fail just because there's no session - it should proceed
    // (note: InvokeDevelopmentAgent without a session may still fail for other reasons)
    let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };
    let result = handler.execute(effect, &mut ctx);

    // The result could be Ok or Err depending on whether the effect can execute,
    // but it should NOT be a CapabilityDenied event
    if let Ok(result) = result {
        if let PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. }) = &result.event {
            panic!("No session should not cause CapabilityDenied");
        }
    }
    // Err is also acceptable - the effect might fail for other reasons
}
