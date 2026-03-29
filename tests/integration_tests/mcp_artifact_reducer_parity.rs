//! Integration parity tests for MCP artifact ingestion vs XML fallback.
//!
//! Proves end-to-end reducer outcome parity between:
//! - JSON artifact-present path (MCP submission)
//! - XML-only fallback path (legacy extraction)

use crate::common::IntegrationFixture;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::reducer::effect::{Effect, EffectHandler};
use ralph_workflow::reducer::handler::MainEffectHandler;
use ralph_workflow::reducer::state::{
    CommitValidatedOutcome, DevelopmentValidatedOutcome, FixValidatedOutcome, PipelineState,
    PlanningValidatedOutcome, ReviewValidatedOutcome,
};
use ralph_workflow::reducer::state_reduction::reduce;
use ralph_workflow::workspace::{ArtifactEnvelope, MemoryWorkspace, Workspace};
use std::sync::Arc;

const PLAN_XML: &str = r#"<ralph-plan>
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
<target-files><file path="src/config.rs" action="modify"/></target-files>
<location>After the imports</location>
<content><paragraph>Add new configuration option.</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="src/config.rs" action="modify" estimated-changes="~20 lines"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="low"><risk>Breaking existing configuration</risk><mitigation>Add backward compatibility</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>Run unit tests</method><expected-outcome>All tests pass</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

const DEVELOPMENT_RESULT_XML: &str = r#"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Fixed all bugs</ralph-summary>
</ralph-development-result>"#;

const ISSUES_XML: &str =
    "<ralph-issues><ralph-no-issues-found>ok</ralph-no-issues-found></ralph-issues>";

const FIX_RESULT_XML: &str =
    "<ralph-fix-result><ralph-status>all_issues_addressed</ralph-status></ralph-fix-result>";

const COMMIT_MESSAGE_XML: &str =
    "<ralph-commit><ralph-subject>feat: parity check</ralph-subject></ralph-commit>";

#[test]
fn integration_plan_json_and_xml_produce_equivalent_reducer_outcome() {
    with_default_timeout(|| {
        let json_ws = MemoryWorkspace::new_test();
        json_ws
            .write_artifact_json(&ArtifactEnvelope::new(
                "plan",
                serde_json::json!({
                    "summary": {
                        "context": "Add a new feature to the application",
                        "scope_items": [
                            {"text": "files to modify", "count": "3", "category": "files"},
                            {"text": "new feature", "count": "1", "category": "feature"},
                            {"text": "test cases", "count": "5", "category": "tests"}
                        ]
                    },
                    "steps": [{
                        "number": 1,
                        "step_type": "file_change",
                        "priority": "high",
                        "title": "Add configuration",
                        "targets": [{"path": "src/config.rs", "action": "modify"}],
                        "location": "After the imports",
                        "content": "Add new configuration option."
                    }],
                    "critical_files": {
                        "primary_files": [{"path": "src/config.rs", "action": "modify", "estimated_changes": "~20 lines"}]
                    },
                    "risks_mitigations": [{"severity": "low", "risk": "Breaking existing configuration", "mitigation": "Add backward compatibility"}],
                    "verification_strategy": [{"method": "Run unit tests", "expected_outcome": "All tests pass"}]
                }),
                "2026-03-26T00:00:00Z",
            ))
            .expect("plan json should be written");

        let xml_ws = MemoryWorkspace::new_test().with_file(
            ralph_workflow::files::llm_output_extraction::file_based_extraction::paths::PLAN_XML,
            PLAN_XML,
        );

        let (json_outcome, xml_outcome) = run_plan_parity_case(json_ws, xml_ws);
        assert_eq!(json_outcome.iteration, xml_outcome.iteration);
        assert_eq!(json_outcome.valid, xml_outcome.valid);
        assert_eq!(json_outcome.markdown, xml_outcome.markdown);
    });
}

#[test]
fn integration_development_result_json_and_xml_produce_equivalent_reducer_outcome() {
    with_default_timeout(|| {
        let json_ws = MemoryWorkspace::new_test();
        json_ws
            .write_artifact_json(&ArtifactEnvelope::new(
                "development_result",
                serde_json::json!({"status": "completed", "summary": "Fixed all bugs"}),
                "2026-03-26T00:00:00Z",
            ))
            .expect("development_result json should be written");

        let xml_ws = MemoryWorkspace::new_test().with_file(
            ralph_workflow::files::llm_output_extraction::file_based_extraction::paths::DEVELOPMENT_RESULT_XML,
            DEVELOPMENT_RESULT_XML,
        );

        let (json_outcome, xml_outcome) = run_development_parity_case(json_ws, xml_ws);
        assert_eq!(json_outcome.iteration, xml_outcome.iteration);
        assert_eq!(json_outcome.status, xml_outcome.status);
        assert_eq!(json_outcome.summary, xml_outcome.summary);
        assert_eq!(json_outcome.files_changed, xml_outcome.files_changed);
        assert_eq!(json_outcome.next_steps, xml_outcome.next_steps);
    });
}

#[test]
fn integration_issues_json_and_xml_produce_equivalent_reducer_outcome() {
    with_default_timeout(|| {
        let json_ws = MemoryWorkspace::new_test();
        json_ws
            .write_artifact_json(&ArtifactEnvelope::new(
                "issues",
                serde_json::json!({"type": "no_issues_found", "explanation": "ok"}),
                "2026-03-26T00:00:00Z",
            ))
            .expect("issues json should be written");

        let xml_ws = MemoryWorkspace::new_test().with_file(
            ralph_workflow::files::llm_output_extraction::file_based_extraction::paths::ISSUES_XML,
            ISSUES_XML,
        );

        let (json_outcome, xml_outcome) = run_issues_parity_case(json_ws, xml_ws);
        assert_eq!(json_outcome.pass, xml_outcome.pass);
        assert_eq!(json_outcome.issues_found, xml_outcome.issues_found);
        assert_eq!(json_outcome.clean_no_issues, xml_outcome.clean_no_issues);
        assert_eq!(json_outcome.issues, xml_outcome.issues);
        assert_eq!(json_outcome.no_issues_found, xml_outcome.no_issues_found);
    });
}

#[test]
fn integration_fix_result_json_and_xml_produce_equivalent_reducer_outcome() {
    with_default_timeout(|| {
        let json_ws = MemoryWorkspace::new_test();
        json_ws
            .write_artifact_json(&ArtifactEnvelope::new(
                "fix_result",
                serde_json::json!({"status": "all_issues_addressed"}),
                "2026-03-26T00:00:00Z",
            ))
            .expect("fix_result json should be written");

        let xml_ws = MemoryWorkspace::new_test().with_file(
            ralph_workflow::files::llm_output_extraction::file_based_extraction::paths::FIX_RESULT_XML,
            FIX_RESULT_XML,
        );

        let (json_outcome, xml_outcome) = run_fix_parity_case(json_ws, xml_ws);
        assert_eq!(json_outcome.pass, xml_outcome.pass);
        assert_eq!(json_outcome.status, xml_outcome.status);
        assert_eq!(json_outcome.summary, xml_outcome.summary);
    });
}

#[test]
fn integration_commit_message_json_and_xml_produce_equivalent_reducer_outcome() {
    with_default_timeout(|| {
        let json_ws = MemoryWorkspace::new_test();
        json_ws
            .write_artifact_json(&ArtifactEnvelope::new(
                "commit_message",
                serde_json::json!({"type": "commit", "subject": "feat: parity check"}),
                "2026-03-26T00:00:00Z",
            ))
            .expect("commit_message json should be written");

        let xml_ws = MemoryWorkspace::new_test().with_file(
            ralph_workflow::files::llm_output_extraction::file_based_extraction::paths::COMMIT_MESSAGE_XML,
            COMMIT_MESSAGE_XML,
        );

        let (json_outcome, xml_outcome) = run_commit_parity_case(json_ws, xml_ws);
        assert_eq!(json_outcome.attempt, xml_outcome.attempt);
        assert_eq!(json_outcome.message, xml_outcome.message);
        assert_eq!(json_outcome.reason, xml_outcome.reason);
    });
}

fn run_plan_parity_case(
    json_workspace: MemoryWorkspace,
    xml_workspace: MemoryWorkspace,
) -> (PlanningValidatedOutcome, PlanningValidatedOutcome) {
    let state = PipelineState::initial(1, 0);

    let json_event = {
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::new(json_workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);
        let mut handler = MainEffectHandler::new(state.clone());
        handler
            .execute(Effect::ValidatePlanningXml { iteration: 0 }, &mut ctx)
            .expect("json plan validation should succeed")
            .event
    };

    let xml_event = {
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::new(xml_workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);
        let mut handler = MainEffectHandler::new(state.clone());
        handler
            .execute(Effect::ValidatePlanningXml { iteration: 0 }, &mut ctx)
            .expect("xml plan validation should succeed")
            .event
    };

    let json_outcome = reduce(state.clone(), json_event)
        .planning_validated_outcome
        .expect("json plan outcome should be populated");
    let xml_outcome = reduce(state, xml_event)
        .planning_validated_outcome
        .expect("xml plan outcome should be populated");

    (json_outcome, xml_outcome)
}

fn run_development_parity_case(
    json_workspace: MemoryWorkspace,
    xml_workspace: MemoryWorkspace,
) -> (DevelopmentValidatedOutcome, DevelopmentValidatedOutcome) {
    let state = PipelineState::initial(1, 0);

    let json_event = {
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::new(json_workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);
        let mut handler = MainEffectHandler::new(state.clone());
        handler
            .execute(Effect::ValidateDevelopmentXml { iteration: 0 }, &mut ctx)
            .expect("json development validation should succeed")
            .event
    };

    let xml_event = {
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::new(xml_workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);
        let mut handler = MainEffectHandler::new(state.clone());
        handler
            .execute(Effect::ValidateDevelopmentXml { iteration: 0 }, &mut ctx)
            .expect("xml development validation should succeed")
            .event
    };

    let json_outcome = reduce(state.clone(), json_event)
        .development_validated_outcome
        .expect("json development outcome should be populated");
    let xml_outcome = reduce(state, xml_event)
        .development_validated_outcome
        .expect("xml development outcome should be populated");

    (json_outcome, xml_outcome)
}

fn run_issues_parity_case(
    json_workspace: MemoryWorkspace,
    xml_workspace: MemoryWorkspace,
) -> (ReviewValidatedOutcome, ReviewValidatedOutcome) {
    let state = PipelineState::initial(0, 1);

    let json_event = {
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::new(json_workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);
        let mut handler = MainEffectHandler::new(state.clone());
        handler
            .execute(Effect::ValidateReviewIssuesXml { pass: 0 }, &mut ctx)
            .expect("json issues validation should succeed")
            .event
    };

    let xml_event = {
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::new(xml_workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);
        let mut handler = MainEffectHandler::new(state.clone());
        handler
            .execute(Effect::ValidateReviewIssuesXml { pass: 0 }, &mut ctx)
            .expect("xml issues validation should succeed")
            .event
    };

    let json_outcome = reduce(state.clone(), json_event)
        .review_validated_outcome
        .expect("json review outcome should be populated");
    let xml_outcome = reduce(state, xml_event)
        .review_validated_outcome
        .expect("xml review outcome should be populated");

    (json_outcome, xml_outcome)
}

fn run_fix_parity_case(
    json_workspace: MemoryWorkspace,
    xml_workspace: MemoryWorkspace,
) -> (FixValidatedOutcome, FixValidatedOutcome) {
    let state = PipelineState::initial(0, 1);

    let json_event = {
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::new(json_workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);
        let mut handler = MainEffectHandler::new(state.clone());
        handler
            .execute(Effect::ValidateFixResultXml { pass: 0 }, &mut ctx)
            .expect("json fix validation should succeed")
            .event
    };

    let xml_event = {
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::new(xml_workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);
        let mut handler = MainEffectHandler::new(state.clone());
        handler
            .execute(Effect::ValidateFixResultXml { pass: 0 }, &mut ctx)
            .expect("xml fix validation should succeed")
            .event
    };

    let json_outcome = reduce(state.clone(), json_event)
        .fix_validated_outcome
        .expect("json fix outcome should be populated");
    let xml_outcome = reduce(state, xml_event)
        .fix_validated_outcome
        .expect("xml fix outcome should be populated");

    (json_outcome, xml_outcome)
}

fn run_commit_parity_case(
    json_workspace: MemoryWorkspace,
    xml_workspace: MemoryWorkspace,
) -> (CommitValidatedOutcome, CommitValidatedOutcome) {
    let state = PipelineState::initial(1, 0);

    let json_event = {
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::new(json_workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);
        let mut handler = MainEffectHandler::new(state.clone());
        handler
            .execute(Effect::ValidateCommitXml, &mut ctx)
            .expect("json commit validation should succeed")
            .event
    };

    let xml_event = {
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::new(xml_workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);
        let mut handler = MainEffectHandler::new(state.clone());
        handler
            .execute(Effect::ValidateCommitXml, &mut ctx)
            .expect("xml commit validation should succeed")
            .event
    };

    let json_outcome = reduce(state.clone(), json_event)
        .commit_validated_outcome
        .expect("json commit outcome should be populated");
    let xml_outcome = reduce(state, xml_event)
        .commit_validated_outcome
        .expect("xml commit outcome should be populated");

    (json_outcome, xml_outcome)
}
