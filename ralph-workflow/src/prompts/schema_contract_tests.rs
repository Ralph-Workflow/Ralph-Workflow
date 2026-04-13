use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::prompts::partials::get_shared_partials;
use crate::prompts::template_engine::Template;
use crate::prompts::template_variables::capability_template_variables;
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

fn assert_schema_valid(schema_str: &str, payload: &Value, context: &str) {
    let schema: Value = serde_json::from_str(schema_str).expect("schema JSON should parse");
    let validator = jsonschema::validator_for(&schema).expect("schema should compile");
    let errors: Vec<String> = validator
        .iter_errors(payload)
        .map(|err| format!("{} @ {}", err, err.instance_path))
        .collect();

    assert!(
        errors.is_empty(),
        "{context} must match schema. Errors:\n{}\nPayload:\n{}",
        errors.join("\n"),
        serde_json::to_string_pretty(payload).expect("payload should serialize")
    );
}

#[test]
fn test_planning_template_example_matches_plan_schema() {
    let rendered = render_template(
        ralph_workflow_policy::PLANNING_TEMPLATE,
        SessionDrain::Planning,
        HashMap::from([("PROMPT", "test requirements".to_string())]),
    );

    let payload = extract_json_after_marker(&rendered, "string conforming to the plan schema:");
    assert_schema_valid(
        include_str!("../../schemas/plan.schema.json"),
        &payload,
        "planning example",
    );
}

#[test]
fn test_development_template_examples_match_development_result_schema() {
    let rendered = render_template(
        ralph_workflow_policy::DEVELOPER_ITERATION_TEMPLATE,
        SessionDrain::Development,
        HashMap::from([
            ("PROMPT", "test prompt".to_string()),
            ("PLAN", "test plan".to_string()),
        ]),
    );

    let completed_payload = extract_json_after_marker(&rendered, "and content as a JSON string:");
    let partial_payload =
        extract_json_after_marker(&rendered, "For partial completion requiring continuation:");

    let schema = include_str!("../../schemas/development_result.schema.json");
    assert_schema_valid(schema, &completed_payload, "development completed example");
    assert_schema_valid(schema, &partial_payload, "development partial example");
}

#[test]
fn test_review_template_examples_match_issues_schema() {
    let rendered = render_template(
        ralph_workflow_policy::REVIEW_TEMPLATE,
        SessionDrain::Review,
        HashMap::from([
            ("PLAN", "test plan".to_string()),
            ("CHANGES", "test changes".to_string()),
        ]),
    );

    let issues_payload = extract_json_after_marker(&rendered, "If issues found:");
    let no_issues_payload = extract_json_after_marker(&rendered, "If no issues found:");

    let schema = include_str!("../../schemas/issues.schema.json");
    assert_schema_valid(schema, &issues_payload, "review issues_found example");
    assert_schema_valid(schema, &no_issues_payload, "review no_issues_found example");
}

#[test]
fn test_commit_template_examples_match_commit_schema() {
    let rendered = render_template(
        ralph_workflow_policy::COMMIT_MESSAGE_TEMPLATE,
        SessionDrain::Commit,
        HashMap::from([("DIFF", "diff --git a/a b/b".to_string())]),
    );

    let schema = include_str!("../../schemas/commit_message.schema.json");
    let examples = [
        ("Simple format:", "commit simple example"),
        ("Detailed format:", "commit detailed example"),
        (
            "Skip format (when no commit needed):",
            "commit skip example",
        ),
        ("With file selection:", "commit with file selection example"),
        (
            "Example -- commit only specific files:",
            "commit specific-files example",
        ),
    ];

    for (marker, context) in examples {
        let payload = extract_json_after_marker(&rendered, marker);
        assert_schema_valid(schema, &payload, context);
    }
}
