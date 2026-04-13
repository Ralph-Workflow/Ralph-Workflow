use super::common::TestFixture;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::reduce;
use crate::reducer::state::PipelineState;
use crate::workspace::{ArtifactEnvelope, MemoryWorkspace, Workspace};

#[test]
fn development_result_json_produces_validated_outcome() {
    // Development validation is JSON-only. Verify that a JSON artifact produces a
    // validated outcome with the expected fields.
    let json_ws = MemoryWorkspace::new_test();
    json_ws
        .write_artifact_json(&ArtifactEnvelope::new(
            "development_result",
            serde_json::json!({
                "status": "completed",
                "summary": "Fixed all bugs"
            }),
            "2026-03-26T00:00:00Z",
        ))
        .expect("development_result json should be written");

    let state = PipelineState::initial(1, 0);
    let json_event = {
        let mut fixture = TestFixture::with_workspace(json_ws);
        let ctx = fixture.ctx();
        let handler = MainEffectHandler::new(state.clone());
        handler.validate_development_xml(&ctx, 0).event
    };

    let outcome = reduce(state, json_event)
        .development_validated_outcome
        .expect("json development outcome should be populated");

    assert_eq!(outcome.status, crate::reducer::state::DevelopmentStatus::Completed);
    assert_eq!(outcome.summary, "Fixed all bugs");
    assert_eq!(outcome.files_changed, None);
    assert_eq!(outcome.next_steps, None);
}

#[test]
fn fix_result_json_produces_validated_outcome() {
    // Fix validation is JSON-only. Verify that a JSON artifact produces a validated
    // outcome with the expected fields.
    let json_ws = MemoryWorkspace::new_test();
    json_ws
        .write_artifact_json(&ArtifactEnvelope::new(
            "fix_result",
            serde_json::json!({
                "status": "all_issues_addressed"
            }),
            "2026-03-26T00:00:00Z",
        ))
        .expect("fix_result json should be written");

    let state = PipelineState::initial(0, 1);
    let json_event = {
        let mut fixture = TestFixture::with_workspace(json_ws);
        let ctx = fixture.ctx();
        let handler = MainEffectHandler::new(state.clone());
        handler.validate_fix_result_xml(&ctx, 0).event
    };

    let outcome = reduce(state, json_event)
        .fix_validated_outcome
        .expect("json fix outcome should be populated");

    assert_eq!(outcome.pass, 0);
    assert_eq!(outcome.status, crate::reducer::state::FixStatus::AllIssuesAddressed);
    assert_eq!(outcome.summary, None);
}

