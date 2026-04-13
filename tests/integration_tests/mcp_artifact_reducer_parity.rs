//! Integration tests for MCP artifact ingestion end-to-end reducer outcomes.
//!
//! Proves end-to-end reducer outcomes for JSON artifact paths (MCP submission).
//! XML fallback has been removed; all artifact validation is JSON-only.

use crate::common::IntegrationFixture;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::reducer::effect::{Effect, EffectHandler};
use ralph_workflow::reducer::handler::MainEffectHandler;
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::reducer::state_reduction::reduce;
use ralph_workflow::workspace::{ArtifactEnvelope, MemoryWorkspace, Workspace};
use std::sync::Arc;

#[test]
fn integration_development_result_json_produces_validated_outcome() {
    // Development validation is JSON-only; verify the JSON path populates the
    // expected outcome fields end-to-end.
    with_default_timeout(|| {
        let json_ws = MemoryWorkspace::new_test();
        json_ws
            .write_artifact_json(&ArtifactEnvelope::new(
                "development_result",
                serde_json::json!({"status": "completed", "summary": "Fixed all bugs"}),
                "2026-03-26T00:00:00Z",
            ))
            .expect("development_result json should be written");

        let state = PipelineState::initial(1, 0);
        let json_event = {
            let mut fixture =
                IntegrationFixture::with_workspace(Arc::new(json_ws) as Arc<dyn Workspace>);
            let mut ctx = fixture.ctx(None);
            let mut handler = MainEffectHandler::new(state.clone());
            handler
                .execute(Effect::ValidateDevelopmentXml { iteration: 0 }, &mut ctx)
                .expect("json development validation should succeed")
                .event
        };

        let outcome = reduce(state, json_event)
            .development_validated_outcome
            .expect("json development outcome should be populated");

        assert_eq!(
            outcome.status,
            ralph_workflow::reducer::state::DevelopmentStatus::Completed
        );
        assert_eq!(outcome.summary, "Fixed all bugs");
    });
}

#[test]
fn integration_fix_result_json_produces_validated_outcome() {
    // Fix validation is JSON-only; verify the JSON path populates the
    // expected outcome fields end-to-end.
    with_default_timeout(|| {
        let json_ws = MemoryWorkspace::new_test();
        json_ws
            .write_artifact_json(&ArtifactEnvelope::new(
                "fix_result",
                serde_json::json!({"status": "all_issues_addressed"}),
                "2026-03-26T00:00:00Z",
            ))
            .expect("fix_result json should be written");

        let state = PipelineState::initial(0, 1);
        let json_event = {
            let mut fixture =
                IntegrationFixture::with_workspace(Arc::new(json_ws) as Arc<dyn Workspace>);
            let mut ctx = fixture.ctx(None);
            let mut handler = MainEffectHandler::new(state.clone());
            handler
                .execute(Effect::ValidateFixResultXml { pass: 0 }, &mut ctx)
                .expect("json fix validation should succeed")
                .event
        };

        let outcome = reduce(state, json_event)
            .fix_validated_outcome
            .expect("json fix outcome should be populated");

        assert_eq!(
            outcome.status,
            ralph_workflow::reducer::state::FixStatus::AllIssuesAddressed
        );
    });
}
