use super::common::TestFixture;
use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::prompts::partials::get_shared_partials;
use crate::prompts::template_engine::Template;
use crate::prompts::template_variables::capability_template_variables;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{
    CommitEvent, DevelopmentEvent, PipelineEvent, PlanningEvent, ReviewEvent,
};
use crate::reducer::{reduce, PipelineState};
use crate::workspace::{ArtifactEnvelope, MemoryWorkspace, Workspace};
use serde_json::Value;
use std::collections::HashMap;

fn render_template(
    template_content: &str,
    drain: SessionDrain,
    base_vars: HashMap<&str, String>,
) -> String {
    let partials = get_shared_partials();
    let template = Template::new(template_content);
    let caps = CapabilitySet::defaults_for_drain(drain);
    let flags = PolicyFlagSet::defaults_for_drain(drain);
    let cap_vars = capability_template_variables(&caps, &flags);

    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(cap_vars)
        .collect();

    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    template
        .render_with_partials(&variables_ref, &partials)
        .expect("template rendering should succeed")
}

fn extract_json_after_marker(content: &str, marker: &str) -> Value {
    let marker_start = content
        .find(marker)
        .unwrap_or_else(|| panic!("marker not found in rendered template: {marker}"));
    let tail = &content[(marker_start + marker.len())..];
    let open_idx = tail
        .find('{')
        .unwrap_or_else(|| panic!("no JSON object found after marker: {marker}"));

    let json = extract_balanced_json(&tail[open_idx..])
        .unwrap_or_else(|| panic!("failed to extract balanced JSON object after marker: {marker}"));

    serde_json::from_str(&json).unwrap_or_else(|e| {
        panic!("invalid JSON example after marker '{marker}': {e}\nExtracted:\n{json}")
    })
}

fn extract_balanced_json(input: &str) -> Option<String> {
    let mut depth = 0usize;
    let mut in_string = false;
    let mut escaped = false;
    let mut end = None;

    for (idx, ch) in input.char_indices() {
        if in_string {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                in_string = false;
            }
            continue;
        }

        match ch {
            '"' => in_string = true,
            '{' => depth += 1,
            '}' => {
                if depth == 0 {
                    return None;
                }
                depth -= 1;
                if depth == 0 {
                    end = Some(idx + 1);
                    break;
                }
            }
            _ => {}
        }
    }

    end.map(|idx| input[..idx].to_string())
}

#[test]
fn plan_phase_contract_chain_sample_to_reducer_state() {
    let rendered = render_template(
        ralph_workflow_policy::PLANNING_TEMPLATE,
        SessionDrain::Planning,
        HashMap::from([("PROMPT", "test requirements".to_string())]),
    );
    let payload = extract_json_after_marker(&rendered, "string conforming to the plan schema:");

    let workspace = MemoryWorkspace::new_test();
    workspace
        .write_artifact_json(&ArtifactEnvelope::new(
            "plan",
            payload,
            "2026-03-26T00:00:00Z",
        ))
        .expect("plan artifact should persist");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let state = PipelineState {
        phase: crate::reducer::event::PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_xml_extracted: true,
        commit_diff_prepared: true,
        commit_diff_empty: false,
        commit_diff_content_id_sha256: Some("id".to_string()),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        agent_chain: crate::reducer::state::AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            crate::agents::AgentRole::Commit,
        ),
        ..PipelineState::initial(1, 0)
    };
    let handler = MainEffectHandler::new(state.clone());

    let result = handler
        .validate_planning_xml(&ctx, 0)
        .expect("planning validation should return event");
    assert!(matches!(
        result.event,
        PipelineEvent::Planning(PlanningEvent::PlanXmlValidated {
            iteration: 0,
            valid: true,
            ..
        })
    ));

    let reduced = reduce(state, result.event);
    let outcome = reduced
        .planning_validated_outcome
        .expect("reducer must persist planning validated outcome");
    assert!(outcome.valid);
    assert_eq!(outcome.iteration, 0);
}

#[test]
fn development_phase_contract_chain_sample_to_reducer_state() {
    let rendered = render_template(
        ralph_workflow_policy::DEVELOPER_ITERATION_TEMPLATE,
        SessionDrain::Development,
        HashMap::from([
            ("PROMPT", "test prompt".to_string()),
            ("PLAN", "test plan".to_string()),
        ]),
    );
    let payload = extract_json_after_marker(&rendered, "and content as a JSON string:");
    let expected_summary = payload
        .get("summary")
        .and_then(|s| s.as_str())
        .expect("development example must include summary")
        .to_string();

    let workspace = MemoryWorkspace::new_test();
    workspace
        .write_artifact_json(&ArtifactEnvelope::new(
            "development_result",
            payload,
            "2026-03-26T00:00:00Z",
        ))
        .expect("development_result artifact should persist");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let state = PipelineState {
        phase: crate::reducer::event::PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_xml_extracted: true,
        commit_diff_prepared: true,
        commit_diff_empty: false,
        commit_diff_content_id_sha256: Some("id".to_string()),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        agent_chain: crate::reducer::state::AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            crate::agents::AgentRole::Commit,
        ),
        ..PipelineState::initial(1, 0)
    };
    let handler = MainEffectHandler::new(state.clone());

    let result = handler.validate_development_xml(&ctx, 0);
    assert!(matches!(
        result.event,
        PipelineEvent::Development(DevelopmentEvent::XmlValidated { iteration: 0, .. })
    ));

    let reduced = reduce(state, result.event);
    let outcome = reduced
        .development_validated_outcome
        .expect("reducer must persist development validated outcome");
    assert_eq!(outcome.summary, expected_summary);
    assert_eq!(outcome.iteration, 0);
}

#[test]
fn review_phase_contract_chain_sample_to_reducer_state() {
    let rendered = render_template(
        ralph_workflow_policy::REVIEW_TEMPLATE,
        SessionDrain::Review,
        HashMap::from([
            ("PLAN", "test plan".to_string()),
            ("CHANGES", "test changes".to_string()),
        ]),
    );
    let payload = extract_json_after_marker(&rendered, "If issues found:");

    let workspace = MemoryWorkspace::new_test();
    workspace
        .write_artifact_json(&ArtifactEnvelope::new(
            "issues",
            payload,
            "2026-03-26T00:00:00Z",
        ))
        .expect("issues artifact should persist");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let state = PipelineState::initial(0, 1);
    let handler = MainEffectHandler::new(state.clone());

    let result = handler.validate_review_issues_xml(&ctx, 0);
    assert!(matches!(
        result.event,
        PipelineEvent::Review(ReviewEvent::IssuesXmlValidated {
            pass: 0,
            issues_found: true,
            ..
        })
    ));

    let reduced = reduce(state, result.event);
    let outcome = reduced
        .review_validated_outcome
        .expect("reducer must persist review validated outcome");
    assert!(outcome.issues_found);
    assert_eq!(outcome.pass, 0);
    assert!(!outcome.issues.is_empty());
}

#[test]
fn fix_phase_contract_chain_sample_to_reducer_state() {
    let rendered = render_template(
        ralph_workflow_policy::FIX_MODE_TEMPLATE,
        SessionDrain::Fix,
        HashMap::from([
            ("PROMPT", "test prompt".to_string()),
            ("PLAN", "test plan".to_string()),
            ("ISSUES", "issue 1".to_string()),
            ("FILES_TO_MODIFY", "src/lib.rs".to_string()),
        ]),
    );
    let payload = extract_json_after_marker(&rendered, "JSON string:");

    let workspace = MemoryWorkspace::new_test();
    workspace
        .write_artifact_json(&ArtifactEnvelope::new(
            "fix_result",
            payload,
            "2026-03-26T00:00:00Z",
        ))
        .expect("fix_result artifact should persist");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let state = PipelineState::initial(0, 1);
    let handler = MainEffectHandler::new(state.clone());

    let result = handler.validate_fix_result_xml(&ctx, 0);
    assert!(matches!(
        result.event,
        PipelineEvent::Review(ReviewEvent::FixResultXmlValidated { pass: 0, .. })
    ));

    let reduced = reduce(state, result.event);
    let outcome = reduced
        .fix_validated_outcome
        .expect("reducer must persist fix validated outcome");
    assert_eq!(outcome.pass, 0);
    assert_eq!(
        outcome.status,
        crate::reducer::state::FixStatus::AllIssuesAddressed
    );
}

#[test]
fn commit_phase_contract_chain_sample_to_reducer_state() {
    let rendered = render_template(
        ralph_workflow_policy::COMMIT_MESSAGE_TEMPLATE,
        SessionDrain::Commit,
        HashMap::from([("DIFF", "diff --git a/a b/b".to_string())]),
    );
    let payload = extract_json_after_marker(&rendered, "Detailed format:");
    let expected_subject = payload
        .get("subject")
        .and_then(|s| s.as_str())
        .expect("commit sample should have subject")
        .to_string();

    let workspace = MemoryWorkspace::new_test();
    workspace
        .write_artifact_json(&ArtifactEnvelope::new(
            "commit_message",
            payload,
            "2026-03-26T00:00:00Z",
        ))
        .expect("commit_message artifact should persist");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let state = PipelineState::initial(1, 0);
    let handler = MainEffectHandler::new(state.clone());

    let result = handler.validate_commit_xml(&ctx);
    assert!(matches!(
        result.event,
        PipelineEvent::Commit(CommitEvent::CommitXmlValidated { attempt: 1, .. })
    ));

    let reduced = reduce(state, result.event);
    let outcome = reduced
        .commit_validated_outcome
        .clone()
        .expect("reducer must persist commit validated outcome");
    assert_eq!(outcome.attempt, 1);
    let message = outcome
        .message
        .clone()
        .expect("validated commit outcome should include message");
    assert!(
        message.contains(&expected_subject),
        "commit message should include rendered sample subject"
    );
}
