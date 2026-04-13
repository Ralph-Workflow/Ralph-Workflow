use super::common::TestFixture;
use crate::files::artifact_paths;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{
    CommitEvent, DevelopmentEvent, PipelineEvent, PlanningEvent, ReviewEvent,
};
use crate::reducer::state::{ContinuationState, PipelineState};
use crate::workspace::{ArtifactEnvelope, MemoryWorkspace, Workspace};

const VALID_PLAN_XML: &str = r#"<ralph-plan>
<ralph-summary>
<context>Add a new feature to the application</context>
<scope-items>
<scope-item count="3" category="files">files to modify</scope-item>
<scope-item count="1" category="feature">new feature</scope-item>
<scope-item count="5" category="tests">test cases</scope-item>
</scope-items>
</ralph-summary>

<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Add configuration</title>
<target-files>
<file path="src/config.rs" action="modify"/>
</target-files>
<location>After the imports</location>
<content>
<paragraph>Add new configuration option.</paragraph>
</content>
</step>
</ralph-implementation-steps>

<ralph-critical-files>
<primary-files>
<file path="src/config.rs" action="modify" estimated-changes="~20 lines"/>
</primary-files>
</ralph-critical-files>

<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>Breaking existing configuration</risk>
<mitigation>Add backward compatibility</mitigation>
</risk-pair>
</ralph-risks-mitigations>

<ralph-verification-strategy>
<verification>
<method>Run unit tests</method>
<expected-outcome>All tests pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

const VALID_DEVELOPMENT_XML: &str = r#"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Fixed all bugs</ralph-summary>
</ralph-development-result>"#;

const VALID_ISSUES_XML: &str =
    "<ralph-issues><ralph-no-issues-found>ok</ralph-no-issues-found></ralph-issues>";

const VALID_FIX_XML: &str =
    "<ralph-fix-result><ralph-status>all_issues_addressed</ralph-status></ralph-fix-result>";

const VALID_COMMIT_XML_SKIP: &str =
    "<ralph-commit><ralph-skip>No changes to commit</ralph-skip></ralph-commit>";

fn write_development_result_json(
    workspace: &MemoryWorkspace,
    content: serde_json::Value,
) -> std::io::Result<()> {
    workspace.write_artifact_json(&ArtifactEnvelope::new(
        "development_result",
        content,
        "2026-03-26T00:00:00Z",
    ))
}

#[test]
fn planning_invalid_json_does_not_fall_back_to_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/plan.json", "{not valid json")
        .with_file(artifact_paths::PLAN_XML, VALID_PLAN_XML);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(1, 0));

    let result = handler
        .validate_planning_xml(&ctx, 0)
        .expect("handler should not error");

    assert!(matches!(
        result.event,
        PipelineEvent::Planning(PlanningEvent::OutputValidationFailed { iteration: 0, .. })
    ));
}

#[test]
fn development_invalid_json_does_not_fall_back_to_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/development_result.json", "{broken")
        .with_file(artifact_paths::DEVELOPMENT_RESULT_XML, VALID_DEVELOPMENT_XML);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(1, 0));

    let result = handler.validate_development_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Development(DevelopmentEvent::OutputValidationFailed { iteration: 0, .. })
    ));
}

#[test]
fn development_missing_json_fails_without_xml_fallback() {
    // Development validation is JSON-only: missing JSON always produces
    // OutputValidationFailed, even when an XML file is present.
    let workspace = MemoryWorkspace::new_test()
        .with_file(artifact_paths::DEVELOPMENT_RESULT_XML, VALID_DEVELOPMENT_XML);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(1, 0));

    let result = handler.validate_development_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Development(DevelopmentEvent::OutputValidationFailed { iteration: 0, .. })
    ));
}

#[test]
fn development_continuation_json_missing_next_steps_fails_without_xml_fallback() {
    let workspace = MemoryWorkspace::new_test();
    write_development_result_json(
        &workspace,
        serde_json::json!({
            "status": "partial",
            "summary": "Work remains"
        }),
    )
    .expect("should write development_result.json envelope");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let mut state = PipelineState::initial(1, 0);
    state.continuation = ContinuationState {
        continuation_attempt: 1,
        ..ContinuationState::default()
    };
    let handler = MainEffectHandler::new(state);

    let result = handler.validate_development_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Development(DevelopmentEvent::OutputValidationFailed { iteration: 0, .. })
    ));
}

#[test]
fn development_continuation_json_tolerates_files_changed_by_clearing_it() {
    let workspace = MemoryWorkspace::new_test();
    write_development_result_json(
        &workspace,
        serde_json::json!({
            "status": "partial",
            "summary": "Work remains",
            "files_changed": "src/lib.rs\nsrc/main.rs",
            "next_steps": "1. Continue implementation"
        }),
    )
    .expect("should write development_result.json envelope");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let mut state = PipelineState::initial(1, 0);
    state.continuation = ContinuationState {
        continuation_attempt: 1,
        ..ContinuationState::default()
    };
    let handler = MainEffectHandler::new(state);

    let result = handler.validate_development_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Development(DevelopmentEvent::XmlValidated {
            iteration: 0,
            files_changed: None,
            ..
        })
    ));
}

#[test]
fn fix_analysis_continuation_json_missing_next_steps_fails_without_xml_fallback() {
    let workspace = MemoryWorkspace::new_test();
    write_development_result_json(
        &workspace,
        serde_json::json!({
            "status": "partial",
            "summary": "Needs follow-up"
        }),
    )
    .expect("should write development_result.json envelope");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let mut state = PipelineState::initial(0, 1);
    state.fix_analysis_agent_invoked_pass = Some(0);
    state.continuation = ContinuationState {
        fix_continuation_attempt: 1,
        ..ContinuationState::default()
    };
    let handler = MainEffectHandler::new(state);

    let result = handler.validate_fix_result_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Review(ReviewEvent::FixOutputValidationFailed { pass: 0, .. })
    ));
}

#[test]
fn review_invalid_json_does_not_fall_back_to_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/issues.json", "{bad")
        .with_file(artifact_paths::ISSUES_XML, VALID_ISSUES_XML);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));

    let result = handler.validate_review_issues_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Review(ReviewEvent::OutputValidationFailed {
            pass: 0,
            error_detail: Some(_),
            ..
        })
    ));
}

#[test]
fn fix_invalid_json_does_not_fall_back_to_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/fix_result.json", "{bad")
        .with_file(artifact_paths::FIX_RESULT_XML, VALID_FIX_XML);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));

    let result = handler.validate_fix_result_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Review(ReviewEvent::FixOutputValidationFailed {
            pass: 0,
            error_detail: Some(_),
            ..
        })
    ));
}

#[test]
fn fix_missing_json_fails_without_xml_fallback() {
    // Fix validation is JSON-only: missing JSON always produces FixOutputValidationFailed,
    // even when an XML file is present.
    let workspace = MemoryWorkspace::new_test().with_file(artifact_paths::FIX_RESULT_XML, VALID_FIX_XML);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));

    let result = handler.validate_fix_result_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Review(ReviewEvent::FixOutputValidationFailed { pass: 0, .. })
    ));
}

#[test]
fn commit_invalid_json_does_not_fall_back_to_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/commit_message.json", "{bad")
        .with_file(artifact_paths::COMMIT_MESSAGE_XML, VALID_COMMIT_XML_SKIP);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(1, 0));

    let result = handler.validate_commit_xml(&ctx);

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(CommitEvent::CommitXmlValidationFailed { .. })
    ));
}

