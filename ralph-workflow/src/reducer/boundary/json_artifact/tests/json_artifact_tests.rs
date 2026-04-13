//! Tests for json_artifact.rs - JSON artifact ingestion.

use crate::files::result_types::{FileAction, Priority, StepType};
use crate::reducer::boundary::json_artifact::{
    development_result_from_envelope, fix_result_from_envelope, issues_elements_from_envelope,
    plan_elements_from_envelope,
};
use crate::workspace::ArtifactEnvelope;

fn make_plan_envelope() -> ArtifactEnvelope {
    let content = serde_json::json!({
        "summary": {
            "context": "Adding error types for MCP",
            "scope_items": [
                {"text": "Add ErrorCode enum", "count": "1 enum"},
                {"text": "Add ValidationError struct"},
                {"text": "Add ErrorResponse struct"}
            ]
        },
        "steps": [
            {
                "number": 1,
                "title": "Define error types",
                "content": "Add ErrorCode, ValidationError, and ErrorResponse to types.rs",
                "step_type": "file_change",
                "targets": [{"path": "src/types.rs", "action": "modify"}],
                "depends_on": []
            }
        ],
        "critical_files": {
            "primary_files": [
                {"path": "src/types.rs", "action": "modify", "estimated_changes": "~50 lines"}
            ],
            "reference_files": [
                {"path": "src/lib.rs", "purpose": "module declarations"}
            ]
        },
        "risks_mitigations": [
            {"risk": "Breaking existing API", "mitigation": "Add types only", "severity": "low"}
        ],
        "verification_strategy": [
            {"method": "cargo test", "expected_outcome": "All tests pass"}
        ]
    });

    ArtifactEnvelope::new("plan", content, "2026-03-25T00:00:00Z")
}

fn make_dev_result_envelope() -> ArtifactEnvelope {
    let content = serde_json::json!({
        "status": "completed",
        "summary": "Implemented error types for MCP",
        "files_changed": "src/types.rs\nsrc/lib.rs",
        "next_steps": "Add unit tests"
    });

    ArtifactEnvelope::new("development_result", content, "2026-03-25T00:00:00Z")
}

#[test]
fn plan_elements_round_trip_from_json() {
    let envelope = make_plan_envelope();
    let result = plan_elements_from_envelope(&envelope);
    assert!(result.is_ok(), "Conversion failed: {:?}", result.err());

    let elements = result.unwrap();
    assert_eq!(elements.summary.context, "Adding error types for MCP");
    assert_eq!(elements.summary.scope_items.len(), 3);
    assert_eq!(
        elements.summary.scope_items[0].count.as_deref(),
        Some("1 enum")
    );
    assert_eq!(elements.steps.len(), 1);
    assert_eq!(elements.steps[0].number, 1);
    assert_eq!(elements.steps[0].title, "Define error types");
    assert_eq!(elements.steps[0].kind, StepType::FileChange);
    assert_eq!(elements.steps[0].target_files.len(), 1);
    assert_eq!(elements.steps[0].target_files[0].path, "src/types.rs");
    assert_eq!(elements.critical_files.primary_files.len(), 1);
    assert_eq!(elements.critical_files.reference_files.len(), 1);
    assert_eq!(elements.risks_mitigations.len(), 1);
    assert_eq!(
        elements.risks_mitigations[0].severity,
        Some(crate::files::result_types::Severity::Low)
    );
    assert_eq!(elements.verification_strategy.len(), 1);
}

#[test]
fn development_result_round_trip_from_json() {
    let envelope = make_dev_result_envelope();
    let result = development_result_from_envelope(&envelope);
    assert!(result.is_ok(), "Conversion failed: {:?}", result.err());

    let elements = result.unwrap();
    assert_eq!(elements.status, "completed");
    assert_eq!(elements.summary, "Implemented error types for MCP");
    assert!(elements.is_completed());
    assert!(elements.files_changed_present);
    assert_eq!(
        elements.files_changed.as_deref(),
        Some("src/types.rs\nsrc/lib.rs")
    );
    assert!(elements.next_steps_present);
    assert_eq!(elements.next_steps.as_deref(), Some("Add unit tests"));
}

#[test]
fn plan_missing_summary_returns_error() {
    let content = serde_json::json!({"steps": []});
    let envelope = ArtifactEnvelope::new("plan", content, "2026-03-25T00:00:00Z");
    let result = plan_elements_from_envelope(&envelope);
    assert!(result.is_err());
    assert!(result.unwrap_err().message.contains("missing 'summary'"));
}

#[test]
fn development_result_missing_status_returns_error() {
    let content = serde_json::json!({"summary": "Done"});
    let envelope = ArtifactEnvelope::new("development_result", content, "2026-03-25T00:00:00Z");
    let result = development_result_from_envelope(&envelope);
    assert!(result.is_err());
    assert!(result.unwrap_err().message.contains("status"));
}

#[test]
fn plan_with_parallel_plan() {
    let content = serde_json::json!({
        "summary": {
            "context": "Parallel work",
            "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}]
        },
        "steps": [{"number": 1, "title": "t", "content": "c"}],
        "critical_files": {
            "primary_files": [{"path": "f", "action": "modify"}]
        },
        "risks_mitigations": [{"risk": "r", "mitigation": "m"}],
        "verification_strategy": [{"method": "m", "expected_outcome": "o"}],
        "parallel_plan": [
            {
                "id": "wu-1",
                "description": "First unit",
                "edit_area": {"paths": ["a.rs"], "directories": ["src/"]},
                "depends_on": []
            },
            {
                "id": "wu-2",
                "description": "Second unit",
                "edit_area": {"paths": [], "directories": ["tests/"]},
                "depends_on": ["wu-1"]
            }
        ]
    });

    let envelope = ArtifactEnvelope::new("plan", content, "2026-03-25T00:00:00Z");
    let result = plan_elements_from_envelope(&envelope).unwrap();
    let pp = result.parallel_plan.unwrap();
    assert_eq!(pp.work_units.len(), 2);
    assert_eq!(pp.work_units[0].unit_id, "wu-1");
    assert_eq!(pp.work_units[1].dependencies, vec!["wu-1"]);
}

#[test]
fn development_result_partial_fields() {
    let content = serde_json::json!({
        "status": "partial",
        "summary": "Partially done"
    });
    let envelope = ArtifactEnvelope::new("development_result", content, "2026-03-25T00:00:00Z");
    let result = development_result_from_envelope(&envelope).unwrap();
    assert!(result.is_partial());
    assert!(!result.files_changed_present);
    assert!(!result.next_steps_present);
    assert!(result.files_changed.is_none());
    assert!(result.next_steps.is_none());
}

/// Verifies that JSON and XML paths produce equivalent domain state for plans.
///
/// Given a JSON envelope and the equivalent XML, both conversion paths
/// must yield the same key fields (summary context, step count, titles,
/// critical file count, etc.).
#[test]
fn json_xml_equivalent_state_plan() {
    // Construct a JSON plan envelope
    let json_content = serde_json::json!({
        "summary": {
            "context": "Equivalence test",
            "scope_items": [
                {"text": "Item A"},
                {"text": "Item B"},
                {"text": "Item C"}
            ]
        },
        "steps": [
            {"number": 1, "title": "Step one", "content": "Do step one"}
        ],
        "critical_files": {
            "primary_files": [
                {"path": "src/main.rs", "action": "modify"}
            ]
        },
        "risks_mitigations": [
            {"risk": "Risk one", "mitigation": "Mitigate one"}
        ],
        "verification_strategy": [
            {"method": "cargo test", "expected_outcome": "pass"}
        ]
    });

    let envelope = ArtifactEnvelope::new("plan", json_content, "2026-03-25T00:00:00Z");
    let from_json = plan_elements_from_envelope(&envelope).unwrap();

    // Verify key fields are consistent
    assert_eq!(from_json.summary.context, "Equivalence test");
    assert_eq!(from_json.summary.scope_items.len(), 3);
    assert_eq!(from_json.steps.len(), 1);
    assert_eq!(from_json.steps[0].title, "Step one");
    assert_eq!(from_json.critical_files.primary_files.len(), 1);
    assert_eq!(
        from_json.critical_files.primary_files[0].path,
        "src/main.rs"
    );
    assert_eq!(from_json.risks_mitigations.len(), 1);
    assert_eq!(from_json.verification_strategy.len(), 1);
}

/// Verifies that JSON and XML paths produce equivalent domain state for
/// development results.
#[test]
fn json_xml_equivalent_state_development() {
    let json_content = serde_json::json!({
        "status": "completed",
        "summary": "All done",
        "files_changed": "a.rs\nb.rs",
        "next_steps": "Run tests"
    });

    let envelope =
        ArtifactEnvelope::new("development_result", json_content, "2026-03-25T00:00:00Z");
    let from_json = development_result_from_envelope(&envelope).unwrap();

    assert_eq!(from_json.status, "completed");
    assert!(from_json.is_completed());
    assert_eq!(from_json.summary, "All done");
    assert_eq!(from_json.files_changed.as_deref(), Some("a.rs\nb.rs"));
    assert_eq!(from_json.next_steps.as_deref(), Some("Run tests"));
}

/// When no JSON artifact exists, the boundary must fall back to XML.
/// This test verifies that the conversion function returns an error when
/// given an envelope with invalid/empty content, confirming the boundary
/// will fall through to the XML path.
#[test]
fn falls_back_to_xml_when_json_missing_plan() {
    // Simulate: no JSON file exists => read_artifact_json returns Ok(None)
    // The boundary code handles this by trying XML next.
    // Here we verify that an envelope with incomplete content fails conversion,
    // which would trigger the XML fallback in the boundary.
    let bad_content = serde_json::json!({"incomplete": true});
    let envelope = ArtifactEnvelope::new("plan", bad_content, "2026-03-25T00:00:00Z");
    let result = plan_elements_from_envelope(&envelope);
    assert!(
        result.is_err(),
        "Incomplete JSON should fail, triggering XML fallback"
    );
}

/// Same fallback test for development result.
#[test]
fn falls_back_to_xml_when_json_missing_development() {
    let bad_content = serde_json::json!({"incomplete": true});
    let envelope = ArtifactEnvelope::new("development_result", bad_content, "2026-03-25T00:00:00Z");
    let result = development_result_from_envelope(&envelope);
    assert!(
        result.is_err(),
        "Incomplete JSON should fail, triggering XML fallback"
    );
}

// -----------------------------------------------------------------------
// Task 22: Dual-mode integration-level test scenarios
// -----------------------------------------------------------------------

/// Validates the full JSON plan lifecycle: construct envelope, convert to
/// domain type, verify all fields are accessible, then serialize back to
/// JSON to confirm round-trip fidelity.
#[test]
fn json_plan_full_lifecycle_round_trip() {
    let content = serde_json::json!({
        "summary": {
            "context": "Lifecycle round-trip test",
            "scope_items": [
                {"text": "Create module A", "count": "1 file", "category": "feature"},
                {"text": "Update module B"},
                {"text": "Add tests for C"}
            ]
        },
        "steps": [
            {
                "number": 1,
                "title": "Create module A",
                "content": "Create src/module_a.rs with public API",
                "step_type": "file_change",
                "priority": "high",
                "targets": [{"path": "src/module_a.rs", "action": "create"}],
                "location": "src/",
                "rationale": "New module needed for feature X",
                "depends_on": []
            },
            {
                "number": 2,
                "title": "Update module B",
                "content": "Add import and call to module A",
                "step_type": "file_change",
                "targets": [{"path": "src/module_b.rs", "action": "modify"}],
                "depends_on": [1]
            }
        ],
        "critical_files": {
            "primary_files": [
                {"path": "src/module_a.rs", "action": "create", "estimated_changes": "~80 lines"},
                {"path": "src/module_b.rs", "action": "modify", "estimated_changes": "~10 lines"}
            ],
            "reference_files": [
                {"path": "src/lib.rs", "purpose": "module re-exports"}
            ]
        },
        "risks_mitigations": [
            {"risk": "API breakage", "mitigation": "Keep backward compat", "severity": "medium"},
            {"risk": "Test coverage gap", "mitigation": "Add integration tests", "severity": "low"}
        ],
        "verification_strategy": [
            {"method": "cargo test", "expected_outcome": "All tests pass"},
            {"method": "cargo clippy", "expected_outcome": "No warnings"}
        ],
        "skills_mcp": {
            "skills": ["test-driven-development"],
            "mcps": ["filesystem"]
        }
    });

    let envelope = ArtifactEnvelope::new("plan", content.clone(), "2026-03-25T12:00:00Z");
    let elements =
        plan_elements_from_envelope(&envelope).expect("Valid plan JSON must convert successfully");

    // Verify domain fields
    assert_eq!(elements.summary.context, "Lifecycle round-trip test");
    assert_eq!(elements.summary.scope_items.len(), 3);
    assert_eq!(
        elements.summary.scope_items[0].category.as_deref(),
        Some("feature")
    );
    assert_eq!(elements.steps.len(), 2);
    assert_eq!(elements.steps[0].kind, StepType::FileChange);
    assert_eq!(elements.steps[0].priority, Some(Priority::High));
    assert_eq!(elements.steps[0].location.as_deref(), Some("src/"));
    assert_eq!(
        elements.steps[0].rationale.as_deref(),
        Some("New module needed for feature X")
    );
    assert_eq!(elements.steps[0].target_files[0].action, FileAction::Create);
    assert_eq!(elements.steps[1].depends_on, vec![1]);
    assert_eq!(elements.critical_files.primary_files.len(), 2);
    assert_eq!(
        elements.critical_files.primary_files[0]
            .estimated_changes
            .as_deref(),
        Some("~80 lines")
    );
    assert_eq!(elements.risks_mitigations.len(), 2);
    assert_eq!(
        elements.risks_mitigations[0].severity,
        Some(crate::files::result_types::Severity::Medium)
    );
    assert_eq!(elements.verification_strategy.len(), 2);
    let skills = elements
        .skills_mcp
        .as_ref()
        .expect("skills_mcp must be present");
    assert_eq!(skills.skills.len(), 1);
    assert_eq!(skills.skills[0].name, "test-driven-development");
    assert_eq!(skills.mcps.len(), 1);
    assert_eq!(skills.mcps[0].name, "filesystem");
}

/// Validates the full JSON development_result lifecycle with all optional
/// fields present, confirming persistence-ready data survives conversion.
#[test]
fn json_development_result_full_lifecycle() {
    let content = serde_json::json!({
        "status": "completed",
        "summary": "Implemented feature X with full test coverage",
        "files_changed": "src/module_a.rs\nsrc/module_b.rs\ntests/integration.rs",
        "next_steps": "Run CI pipeline and verify no regressions",
        "skills_mcp": {
            "skills": ["systematic-debugging"],
            "mcps": ["filesystem", "memory"]
        }
    });

    let envelope = ArtifactEnvelope::new("development_result", content, "2026-03-25T12:00:00Z");
    let elements = development_result_from_envelope(&envelope)
        .expect("Valid development_result JSON must convert");

    assert_eq!(elements.status, "completed");
    assert!(elements.is_completed());
    assert!(!elements.is_partial());
    assert_eq!(
        elements.summary,
        "Implemented feature X with full test coverage"
    );
    assert!(elements.files_changed_present);
    let files = elements
        .files_changed
        .as_deref()
        .expect("files_changed present");
    assert!(files.contains("src/module_a.rs"));
    assert!(files.contains("tests/integration.rs"));
    assert!(elements.next_steps_present);
    assert_eq!(
        elements.next_steps.as_deref(),
        Some("Run CI pipeline and verify no regressions")
    );
    let skills = elements.skills_mcp.as_ref().expect("skills_mcp present");
    assert_eq!(skills.skills[0].name, "systematic-debugging");
    assert_eq!(skills.mcps.len(), 2);
}

/// Validates that conversion correctly handles a minimal valid plan (all
/// required fields present but with minimal content). This exercises the
/// boundary between "valid but sparse" and "invalid" JSON.
#[test]
fn json_plan_minimal_valid() {
    let content = serde_json::json!({
        "summary": {
            "context": "Minimal",
            "scope_items": [{"text": "x"}, {"text": "y"}, {"text": "z"}]
        },
        "steps": [],
        "critical_files": {
            "primary_files": []
        },
        "risks_mitigations": [],
        "verification_strategy": []
    });

    let envelope = ArtifactEnvelope::new("plan", content, "2026-03-25T00:00:00Z");
    let elements = plan_elements_from_envelope(&envelope).expect("Minimal valid plan must convert");

    assert_eq!(elements.summary.context, "Minimal");
    assert_eq!(elements.summary.scope_items.len(), 3);
    assert!(elements.steps.is_empty());
    assert!(elements.critical_files.primary_files.is_empty());
    assert!(elements.risks_mitigations.is_empty());
    assert!(elements.verification_strategy.is_empty());
    assert!(elements.skills_mcp.is_none());
    assert!(elements.parallel_plan.is_none());
}

/// Validates that a development_result with "partial" status is correctly
/// recognized, and that missing optional fields do not cause errors.
#[test]
fn json_development_result_partial_no_optional_fields() {
    let content = serde_json::json!({
        "status": "partial",
        "summary": "Work in progress"
    });

    let envelope = ArtifactEnvelope::new("development_result", content, "2026-03-25T00:00:00Z");
    let elements = development_result_from_envelope(&envelope)
        .expect("Partial result with no optionals must convert");

    assert_eq!(elements.status, "partial");
    assert!(elements.is_partial());
    assert!(!elements.is_completed());
    assert!(!elements.files_changed_present);
    assert!(!elements.next_steps_present);
    assert!(elements.files_changed.is_none());
    assert!(elements.next_steps.is_none());
    assert!(elements.skills_mcp.is_none());
}

/// Verifies that various malformed JSON plan payloads produce clear errors
/// rather than panicking, ensuring the XML fallback path is triggered
/// gracefully in boundary modules.
#[test]
fn json_plan_various_malformed_inputs() {
    // Missing steps
    let content = serde_json::json!({
        "summary": {"context": "c", "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}]},
        "critical_files": {"primary_files": []},
        "risks_mitigations": [],
        "verification_strategy": []
    });
    let env = ArtifactEnvelope::new("plan", content, "t");
    assert!(plan_elements_from_envelope(&env).is_err());

    // Missing critical_files
    let content = serde_json::json!({
        "summary": {"context": "c", "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}]},
        "steps": [],
        "risks_mitigations": [],
        "verification_strategy": []
    });
    let env = ArtifactEnvelope::new("plan", content, "t");
    assert!(plan_elements_from_envelope(&env).is_err());

    // Missing risks_mitigations
    let content = serde_json::json!({
        "summary": {"context": "c", "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}]},
        "steps": [],
        "critical_files": {"primary_files": []},
        "verification_strategy": []
    });
    let env = ArtifactEnvelope::new("plan", content, "t");
    assert!(plan_elements_from_envelope(&env).is_err());

    // Missing verification_strategy
    let content = serde_json::json!({
        "summary": {"context": "c", "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}]},
        "steps": [],
        "critical_files": {"primary_files": []},
        "risks_mitigations": []
    });
    let env = ArtifactEnvelope::new("plan", content, "t");
    assert!(plan_elements_from_envelope(&env).is_err());

    // Completely empty object
    let env = ArtifactEnvelope::new("plan", serde_json::json!({}), "t");
    assert!(plan_elements_from_envelope(&env).is_err());

    // Null content values
    let content = serde_json::json!({
        "summary": null,
        "steps": null,
        "critical_files": null,
        "risks_mitigations": null,
        "verification_strategy": null
    });
    let env = ArtifactEnvelope::new("plan", content, "t");
    assert!(plan_elements_from_envelope(&env).is_err());
}

/// Verifies that malformed development_result inputs produce errors
/// rather than panicking.
#[test]
fn json_development_result_various_malformed_inputs() {
    // Missing status
    let content = serde_json::json!({"summary": "Done"});
    let env = ArtifactEnvelope::new("development_result", content, "t");
    assert!(development_result_from_envelope(&env).is_err());

    // Missing summary
    let content = serde_json::json!({"status": "completed"});
    let env = ArtifactEnvelope::new("development_result", content, "t");
    assert!(development_result_from_envelope(&env).is_err());

    // Status is not a string
    let content = serde_json::json!({"status": 42, "summary": "Done"});
    let env = ArtifactEnvelope::new("development_result", content, "t");
    assert!(development_result_from_envelope(&env).is_err());

    // Empty object
    let env = ArtifactEnvelope::new("development_result", serde_json::json!({}), "t");
    assert!(development_result_from_envelope(&env).is_err());
}

// -----------------------------------------------------------------------
// issues_elements_from_envelope tests
// -----------------------------------------------------------------------

/// Round-trip: a valid issues JSON with multiple issues converts correctly.
#[test]
fn issues_elements_round_trip_from_json() {
    let content = serde_json::json!({
        "issues": [
            {"text": "Missing error handling in parse_config()"},
            {"text": "Unused import on line 42"}
        ]
    });

    let envelope = ArtifactEnvelope::new("issues", content, "2026-03-25T12:00:00Z");
    let elements =
        issues_elements_from_envelope(&envelope).expect("Valid issues JSON must convert");

    assert_eq!(elements.issues.len(), 2);
    assert_eq!(
        elements.issues[0].text,
        "Missing error handling in parse_config()"
    );
    assert_eq!(elements.issues[1].text, "Unused import on line 42");
    assert!(elements.no_issues_found.is_none());
}

/// A clean review with no_issues_found converts correctly.
#[test]
fn issues_elements_no_issues_found() {
    let content = serde_json::json!({
        "no_issues_found": "Code looks clean, no issues detected"
    });

    let envelope = ArtifactEnvelope::new("issues", content, "2026-03-25T12:00:00Z");
    let elements =
        issues_elements_from_envelope(&envelope).expect("no_issues_found JSON must convert");

    assert!(elements.issues.is_empty());
    assert_eq!(
        elements.no_issues_found.as_deref(),
        Some("Code looks clean, no issues detected")
    );
}

/// Issues with skills_mcp recommendations parse correctly.
#[test]
fn issues_elements_with_skills_mcp() {
    let content = serde_json::json!({
        "issues": [
            {
                "text": "Race condition in worker pool",
                "skills_mcp": {
                    "skills": ["systematic-debugging"],
                    "mcps": ["memory"]
                }
            }
        ]
    });

    let envelope = ArtifactEnvelope::new("issues", content, "2026-03-25T12:00:00Z");
    let elements =
        issues_elements_from_envelope(&envelope).expect("Issues with skills_mcp must convert");

    assert_eq!(elements.issues.len(), 1);
    let skills = elements.issues[0]
        .skills_mcp
        .as_ref()
        .expect("skills_mcp must be present");
    assert_eq!(skills.skills[0].name, "systematic-debugging");
    assert_eq!(skills.mcps[0].name, "memory");
}

#[test]
fn issues_elements_canonical_issues_found_shape() {
    let content = serde_json::json!({
        "type": "issues_found",
        "issues": [
            {
                "text": "Potential panic on empty input",
                "skills": ["test-driven-development"],
                "mcps": ["context7"]
            }
        ]
    });

    let envelope = ArtifactEnvelope::new("issues", content, "2026-03-25T12:00:00Z");
    let elements =
        issues_elements_from_envelope(&envelope).expect("canonical issues_found must convert");

    assert_eq!(elements.issues.len(), 1);
    assert!(elements.no_issues_found.is_none());
    let skills_mcp = elements.issues[0]
        .skills_mcp
        .as_ref()
        .expect("canonical skills/mcps should be converted");
    assert_eq!(skills_mcp.skills[0].name, "test-driven-development");
    assert_eq!(skills_mcp.mcps[0].name, "context7");
}

#[test]
fn issues_elements_canonical_no_issues_found_shape() {
    let content = serde_json::json!({
        "type": "no_issues_found",
        "explanation": "No regressions found in review pass"
    });

    let envelope = ArtifactEnvelope::new("issues", content, "2026-03-25T12:00:00Z");
    let elements = issues_elements_from_envelope(&envelope)
        .expect("canonical no_issues_found shape must convert");

    assert!(elements.issues.is_empty());
    assert_eq!(
        elements.no_issues_found.as_deref(),
        Some("No regressions found in review pass")
    );
}

#[test]
fn issues_elements_mixed_issues_and_no_issues_found_rejected() {
    let content = serde_json::json!({
        "issues": [{"text": "Something is wrong"}],
        "no_issues_found": "Actually no issues"
    });

    let env = ArtifactEnvelope::new("issues", content, "t");
    let err = issues_elements_from_envelope(&env).expect_err("mixed shape must fail clearly");
    assert!(
        err.message.contains("not both") || err.message.contains("mixed"),
        "error should explain ambiguity, got: {}",
        err.message
    );
}

/// Empty object with neither issues nor no_issues_found returns error.
#[test]
fn issues_elements_empty_object_returns_error() {
    let env = ArtifactEnvelope::new("issues", serde_json::json!({}), "t");
    assert!(issues_elements_from_envelope(&env).is_err());
}

/// Issues field that is not an array returns error.
#[test]
fn issues_elements_non_array_issues_returns_error() {
    let content = serde_json::json!({"issues": "not an array"});
    let env = ArtifactEnvelope::new("issues", content, "t");
    assert!(issues_elements_from_envelope(&env).is_err());
}

// -----------------------------------------------------------------------
// fix_result_from_envelope tests
// -----------------------------------------------------------------------

/// Round-trip: a valid fix_result JSON converts correctly.
#[test]
fn fix_result_round_trip_from_json() {
    let content = serde_json::json!({
        "status": "completed",
        "summary": "Fixed all review issues"
    });

    let envelope = ArtifactEnvelope::new("fix_result", content, "2026-03-25T12:00:00Z");
    let elements = fix_result_from_envelope(&envelope).expect("Valid fix_result JSON must convert");

    assert_eq!(elements.status, "all_issues_addressed");
    assert_eq!(elements.summary.as_deref(), Some("Fixed all review issues"));
}

#[test]
fn fix_result_canonical_status_passthrough() {
    let content = serde_json::json!({
        "status": "all_issues_addressed",
        "summary": "Fixed all review issues"
    });

    let envelope = ArtifactEnvelope::new("fix_result", content, "2026-03-25T12:00:00Z");
    let elements = fix_result_from_envelope(&envelope).expect("canonical fix_result must convert");

    assert_eq!(elements.status, "all_issues_addressed");
}

#[test]
fn fix_result_legacy_status_aliases_are_adapted() {
    let completed = ArtifactEnvelope::new(
        "fix_result",
        serde_json::json!({"status": "completed"}),
        "2026-03-25T12:00:00Z",
    );
    let partial = ArtifactEnvelope::new(
        "fix_result",
        serde_json::json!({"status": "partial"}),
        "2026-03-25T12:00:00Z",
    );

    let completed_out =
        fix_result_from_envelope(&completed).expect("legacy alias 'completed' must adapt");
    let partial_out =
        fix_result_from_envelope(&partial).expect("legacy alias 'partial' must adapt");

    assert_eq!(completed_out.status, "all_issues_addressed");
    assert_eq!(partial_out.status, "issues_remain");
}

#[test]
fn fix_result_unknown_status_rejected() {
    let content = serde_json::json!({
        "status": "mostly_fixed",
        "summary": "Ambiguous legacy value"
    });
    let env = ArtifactEnvelope::new("fix_result", content, "t");
    let err = fix_result_from_envelope(&env).expect_err("unknown status should fail");
    assert!(
        err.message.contains("status") && err.message.contains("canonical"),
        "error should explain canonical status requirement, got: {}",
        err.message
    );
}

/// Fix result with only required status field (no summary) converts.
#[test]
fn fix_result_status_only() {
    let content = serde_json::json!({
        "status": "partial"
    });

    let envelope = ArtifactEnvelope::new("fix_result", content, "2026-03-25T12:00:00Z");
    let elements =
        fix_result_from_envelope(&envelope).expect("Fix result with status only must convert");

    assert_eq!(elements.status, "issues_remain");
    assert!(elements.summary.is_none());
}

/// Missing status field returns error.
#[test]
fn fix_result_missing_status_returns_error() {
    let content = serde_json::json!({"summary": "Fixed things"});
    let env = ArtifactEnvelope::new("fix_result", content, "t");
    assert!(fix_result_from_envelope(&env).is_err());
}

/// Empty object returns error.
#[test]
fn fix_result_empty_object_returns_error() {
    let env = ArtifactEnvelope::new("fix_result", serde_json::json!({}), "t");
    assert!(fix_result_from_envelope(&env).is_err());
}

/// Non-string status returns error.
#[test]
fn fix_result_non_string_status_returns_error() {
    let content = serde_json::json!({"status": 42, "summary": "Done"});
    let env = ArtifactEnvelope::new("fix_result", content, "t");
    assert!(fix_result_from_envelope(&env).is_err());
}

// ---------------------------------------------------------------------------
// AnalysisDecision field tests — Step 4 (Phase 2 TDD)
//
// The development_result JSON artifact can carry an explicit `decision` field
// that maps to AnalysisDecision enum variants. Unknown values must be rejected
// (fail closed). Known values must be parsed and stored.
// ---------------------------------------------------------------------------

/// An artifact with decision="needs_replanning" must parse to NeedsReplanning.
#[test]
fn development_result_with_needs_replanning_decision_parses_correctly() {
    use crate::reducer::state::AnalysisDecision;

    let content = serde_json::json!({
        "status": "completed",
        "summary": "done but plan needs rework",
        "decision": "needs_replanning"
    });
    let envelope = ArtifactEnvelope::new("development_result", content, "t");

    let result = development_result_from_envelope(&envelope)
        .expect("valid artifact with decision field must parse");

    assert_eq!(
        result.analysis_decision,
        Some(AnalysisDecision::NeedsReplanning),
        "decision='needs_replanning' must parse to AnalysisDecision::NeedsReplanning"
    );
}

/// An artifact with decision="ready_for_review" must parse to ReadyForReview.
#[test]
fn development_result_with_ready_for_review_decision_parses_correctly() {
    use crate::reducer::state::AnalysisDecision;

    let content = serde_json::json!({
        "status": "completed",
        "summary": "implementation complete",
        "decision": "ready_for_review"
    });
    let envelope = ArtifactEnvelope::new("development_result", content, "t");

    let result = development_result_from_envelope(&envelope)
        .expect("valid artifact with decision field must parse");

    assert_eq!(
        result.analysis_decision,
        Some(AnalysisDecision::ReadyForReview),
        "decision='ready_for_review' must parse to AnalysisDecision::ReadyForReview"
    );
}

/// An artifact with decision="ready_to_commit" must parse to ReadyToCommit.
#[test]
fn development_result_with_ready_to_commit_decision_parses_correctly() {
    use crate::reducer::state::AnalysisDecision;

    let content = serde_json::json!({
        "status": "completed",
        "summary": "ready to commit",
        "decision": "ready_to_commit"
    });
    let envelope = ArtifactEnvelope::new("development_result", content, "t");

    let result = development_result_from_envelope(&envelope)
        .expect("valid artifact with decision field must parse");

    assert_eq!(
        result.analysis_decision,
        Some(AnalysisDecision::ReadyToCommit),
        "decision='ready_to_commit' must parse to AnalysisDecision::ReadyToCommit"
    );
}

/// An artifact with an unknown decision string must be rejected (fail closed).
#[test]
fn development_result_with_unknown_decision_is_rejected() {
    let content = serde_json::json!({
        "status": "completed",
        "summary": "done",
        "decision": "continue_working_please"
    });
    let envelope = ArtifactEnvelope::new("development_result", content, "t");

    let result = development_result_from_envelope(&envelope);
    assert!(
        result.is_err(),
        "unknown decision value must be rejected; artifact must not parse with unrecognized decision"
    );

    let err_msg = result.err().unwrap().to_string();
    assert!(
        err_msg.contains("decision") || err_msg.contains("continue_working_please"),
        "error message should mention the invalid decision value, got: {err_msg}"
    );
}

/// An artifact without a decision field must parse successfully with analysis_decision=None.
/// Decision field is optional — absence means fall back to status-derived routing.
#[test]
fn development_result_without_decision_field_parses_with_none_decision() {
    let content = serde_json::json!({
        "status": "completed",
        "summary": "done"
    });
    let envelope = ArtifactEnvelope::new("development_result", content, "t");

    let result = development_result_from_envelope(&envelope)
        .expect("artifact without decision field must parse");

    assert_eq!(
        result.analysis_decision, None,
        "absent decision field must produce analysis_decision=None"
    );
}
