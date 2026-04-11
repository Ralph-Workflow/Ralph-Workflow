//! Artifact submission tool handler for MCP server.
//!
//! Provides the `ralph_submit_artifact` tool that accepts structured artifacts
//! (plans, development results, fix results, etc.) and validates them against
//! JSON Schema with structured, directive-style error responses.

use crate::agents::session::{AgentSession, Capability, PolicyOutcome};
use crate::workspace::{ArtifactEnvelope, Workspace};
use mcp_server::dispatch::registry::ToolError;
use mcp_server::protocol::types::{
    ErrorCode, ErrorResponse, ToolContent, ToolResult, ValidationError,
};

/// Known artifact types and their schema file names.
const ARTIFACT_TYPES: &[&str] = &[
    "plan",
    "development_result",
    "issues",
    "fix_result",
    "commit_message",
];

/// Load the embedded JSON Schema for an artifact type.
///
/// Schemas are stored in `ralph-workflow/schemas/{artifact_type}.schema.json`.
/// Currently only `plan` has a schema; others return `None` until their
/// schemas are created in later tasks (T13, T17, T18, T19).
fn load_schema(artifact_type: &str) -> Option<serde_json::Value> {
    let schema_str = match artifact_type {
        "plan" => include_str!("../../schemas/plan.schema.json"),
        "development_result" => include_str!("../../schemas/development_result.schema.json"),
        "issues" => include_str!("../../schemas/issues.schema.json"),
        "fix_result" => include_str!("../../schemas/fix_result.schema.json"),
        "commit_message" => include_str!("../../schemas/commit_message.schema.json"),
        _ => return None,
    };
    serde_json::from_str(schema_str).ok()
}

/// Convert jsonschema validation errors to directive-style `ValidationError`s.
fn convert_schema_errors(
    errors: impl Iterator<Item = jsonschema::ValidationError<'static>>,
) -> Vec<ValidationError> {
    errors
        .map(|err| {
            let (code, expected, got) = classify_error(&err);
            let field_path = normalized_field_path(&err, code);

            let next_action = format!(
                "Fix {}: expected {}, got {}",
                field_path,
                expected,
                got.as_deref().unwrap_or("nothing")
            );

            ValidationError {
                code,
                field_path,
                expected,
                got: got
                    .map(serde_json::Value::String)
                    .unwrap_or(serde_json::Value::Null),
                next_actions: vec![next_action],
                prohibition: None,
            }
        })
        .collect()
}

fn pointer_to_field_path(pointer: &str) -> String {
    let segments: Vec<_> = pointer
        .trim_start_matches('/')
        .split('/')
        .filter(|segment| !segment.is_empty())
        .map(|segment| segment.replace("~1", "/").replace("~0", "~"))
        .collect();

    if segments.is_empty() {
        return "(root)".to_string();
    }

    segments.into_iter().fold(String::new(), |acc, segment| {
        if segment.parse::<usize>().is_ok() {
            format!("{acc}[{segment}]")
        } else if acc.is_empty() {
            segment
        } else {
            format!("{acc}.{segment}")
        }
    })
}

/// Qualify a field path with a missing field name extracted from an error message.
fn qualify_with_missing_field(base_path: String, err_string: &str) -> String {
    let missing_field = extract_required_field(err_string);
    if missing_field == "unknown" {
        return base_path;
    }
    if base_path == "(root)" {
        missing_field
    } else {
        format!("{base_path}.{missing_field}")
    }
}

fn normalized_field_path(err: &jsonschema::ValidationError<'_>, code: ErrorCode) -> String {
    let base_path = pointer_to_field_path(err.instance_path.as_str());
    if code != ErrorCode::MissingField {
        return base_path;
    }
    qualify_with_missing_field(base_path, &err.to_string())
}

/// Classify a required-property error into (ErrorCode, expected, got).
fn classify_required_property(message: &str) -> (ErrorCode, String, Option<String>) {
    let expected = extract_required_field(message);
    (
        ErrorCode::MissingField,
        format!("required field: {expected}"),
        None,
    )
}

fn is_required_property_msg(m: &str) -> bool {
    m.contains("is a required property") || m.contains("required properties")
}
fn is_enum_msg(m: &str) -> bool {
    m.contains("is not one of") || m.contains("not valid under any")
}

/// Map a jsonschema error message to the appropriate ErrorCode.
fn error_code_for_message(message: &str) -> ErrorCode {
    if is_required_property_msg(message) {
        ErrorCode::MissingField
    } else if is_enum_msg(message) {
        ErrorCode::InvalidEnum
    } else if message.contains("is not of type") {
        ErrorCode::TypeMismatch
    } else {
        ErrorCode::ConstraintViolation
    }
}

/// Classify a jsonschema error into our error code taxonomy.
fn classify_error(err: &jsonschema::ValidationError<'_>) -> (ErrorCode, String, Option<String>) {
    let message = err.to_string();
    let instance = format!("{}", err.instance);
    let code = error_code_for_message(&message);
    if code == ErrorCode::MissingField {
        classify_required_property(&message)
    } else {
        (code, message, Some(instance))
    }
}

/// Extract the field name from a "required property" error message.
fn extract_required_field(message: &str) -> String {
    extract_quoted(message, '\'')
        .or_else(|| extract_quoted(message, '"'))
        .or_else(|| extract_quoted(message, '`'))
        .unwrap_or_else(|| "unknown".to_string())
}

fn extract_quoted(message: &str, quote: char) -> Option<String> {
    let start = message.find(quote)?;
    let rest = message.get(start + quote.len_utf8()..)?;
    let end = rest.find(quote)?;
    rest.get(..end).map(ToString::to_string)
}

/// Parsed parameters from a `submit_artifact` call.
struct ArtifactParams<'a> {
    artifact_type: &'a str,
    content_str: &'a str,
    partial: bool,
}

/// Resolve and validate the artifact type, normalizing "review_issues" → "issues".
fn resolve_artifact_type(params: &serde_json::Value) -> Result<&str, ToolError> {
    let raw_type = params
        .get("artifact_type")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'artifact_type' parameter".to_string()))?;
    let artifact_type = if raw_type == "review_issues" {
        "issues"
    } else {
        raw_type
    };
    if ARTIFACT_TYPES.contains(&artifact_type) {
        Ok(artifact_type)
    } else {
        Err(ToolError::InvalidParams(format!(
            "Unknown artifact type '{}'. Valid types: {}",
            artifact_type,
            ARTIFACT_TYPES.join(", ")
        )))
    }
}

/// Extract the non-empty content string parameter.
fn extract_content_str(params: &serde_json::Value) -> Result<&str, ToolError> {
    let content_str = params
        .get("content")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'content' parameter".to_string()))?;
    if content_str.is_empty() {
        Err(ToolError::InvalidParams(
            "Artifact content is empty".to_string(),
        ))
    } else {
        Ok(content_str)
    }
}

/// Extract and validate the raw parameters for artifact submission.
fn parse_artifact_params(params: &serde_json::Value) -> Result<ArtifactParams<'_>, ToolError> {
    let artifact_type = resolve_artifact_type(params)?;
    let content_str = extract_content_str(params)?;
    let partial = params
        .get("partial")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    Ok(ArtifactParams {
        artifact_type,
        content_str,
        partial,
    })
}

/// Run JSON Schema validation and return any errors.
fn run_schema_validation(
    artifact_type: &str,
    content: &serde_json::Value,
) -> Result<Vec<ValidationError>, ToolError> {
    let Some(schema_value) = load_schema(artifact_type) else {
        return Ok(Vec::new());
    };
    let validator = jsonschema::validator_for(&schema_value).map_err(|e| {
        ToolError::ExecutionError(format!(
            "Failed to compile schema for '{}': {}",
            artifact_type, e
        ))
    })?;
    let errors: Vec<_> = validator
        .iter_errors(content)
        .map(|e| e.to_owned())
        .collect();
    Ok(convert_schema_errors(errors.into_iter()))
}

/// Persist a fully-valid artifact and return the accepted ToolResult.
fn persist_accepted_artifact(
    workspace: &dyn Workspace,
    artifact_type: &str,
    content: serde_json::Value,
    now: &str,
) -> Result<ToolResult, ToolError> {
    let envelope = ArtifactEnvelope::new(artifact_type, content, now);
    workspace
        .write_artifact_json(&envelope)
        .map_err(|e| ToolError::ExecutionError(format!("Failed to persist artifact: {}", e)))?;
    let result_json =
        serde_json::json!({"accepted": true, "artifact_type": artifact_type, "validated_at": now});
    Ok(ToolResult {
        content: vec![ToolContent::text(
            serde_json::to_string_pretty(&result_json).unwrap_or_default(),
        )],
        is_error: Some(false),
    })
}

/// Persist a partial artifact (has errors) and return the accepted-partial ToolResult.
fn persist_partial_artifact(
    workspace: &dyn Workspace,
    artifact_type: &str,
    content: serde_json::Value,
    now: &str,
    validation_errors: Vec<ValidationError>,
) -> Result<ToolResult, ToolError> {
    // Convert to workspace validation error type for ArtifactEnvelope
    let workspace_errors: Vec<crate::workspace::ValidationError> = validation_errors
        .iter()
        .cloned()
        .map(|e| e.into())
        .collect();
    let envelope = ArtifactEnvelope::new_partial(artifact_type, content, now, workspace_errors);
    workspace
        .write_partial_artifact_json(&envelope)
        .map_err(|e| {
            ToolError::ExecutionError(format!("Failed to persist partial artifact: {}", e))
        })?;
    let result_json = serde_json::json!({
        "accepted": true, "partial": true, "artifact_type": artifact_type, "validated_at": now,
        "errors": serde_json::to_value(&validation_errors).unwrap_or_default(),
    });
    Ok(ToolResult {
        content: vec![ToolContent::text(
            serde_json::to_string_pretty(&result_json).unwrap_or_default(),
        )],
        is_error: Some(false),
    })
}

/// Build a rejection ToolResult from validation errors.
fn build_rejection_result(
    artifact_type: &str,
    validation_errors: Vec<ValidationError>,
) -> ToolResult {
    let error_response = ErrorResponse {
        errors: validation_errors,
        artifact_type: artifact_type.to_string(),
    };
    ToolResult {
        content: vec![ToolContent::text(
            serde_json::to_string_pretty(&error_response).unwrap_or_default(),
        )],
        is_error: Some(true),
    }
}

/// Dispatch the artifact result: accepted, partial, or rejected.
fn dispatch_artifact_result(
    workspace: &dyn Workspace,
    artifact_type: &str,
    content: serde_json::Value,
    partial: bool,
    validation_errors: Vec<ValidationError>,
) -> Result<ToolResult, ToolError> {
    if validation_errors.is_empty() {
        let now = chrono::Utc::now().to_rfc3339();
        persist_accepted_artifact(workspace, artifact_type, content, &now)
    } else if partial {
        let now = chrono::Utc::now().to_rfc3339();
        persist_partial_artifact(workspace, artifact_type, content, &now, validation_errors)
    } else {
        Ok(build_rejection_result(artifact_type, validation_errors))
    }
}

/// Check that ArtifactSubmit capability is approved for the session.
fn require_artifact_submit_capability(session: &AgentSession) -> Result<(), ToolError> {
    let outcome = session.check_capability(Capability::ArtifactSubmit);
    if matches!(outcome, PolicyOutcome::Approved) {
        return Ok(());
    }
    Err(ToolError::CapabilityDenied(format!(
        "Artifact submission requires capability '{}': {:?}",
        Capability::ArtifactSubmit.identifier(),
        outcome
    )))
}

/// Parse, validate and dispatch an artifact.
fn validate_and_dispatch_artifact(
    workspace: &dyn Workspace,
    params: &serde_json::Value,
) -> Result<ToolResult, ToolError> {
    let artifact_params = parse_artifact_params(params)?;
    let content: serde_json::Value = serde_json::from_str(artifact_params.content_str)
        .map_err(|e| ToolError::ExecutionError(format!("Content is not valid JSON: {}", e)))?;
    let validation_errors = run_schema_validation(artifact_params.artifact_type, &content)?;
    dispatch_artifact_result(
        workspace,
        artifact_params.artifact_type,
        content,
        artifact_params.partial,
        validation_errors,
    )
}

/// Submit a structured artifact to the Ralph workflow.
///
/// # Method Identifier
///
/// `ralph_submit_artifact`
///
/// # Capability Requirements
///
/// Requires: `McpCapability::ArtifactSubmit` — available to all drain types
/// (Planning, Development, Fix, Analysis, Review, Coordination).
///
/// # Access Mode
///
/// ReadOnly-safe: YES — `ArtifactSubmit` is classified as non-mutating by
/// `capability_is_mutating()`, so this tool is available in ReadOnly mode (e.g., Planning drain).
/// Artifact submission is a workflow coordination signal; all drain types (Planning, Development,
/// Fix, Analysis, Review, Commit) include `ArtifactSubmit` in their default capability set.
///
/// # Request Shape
///
/// ```json
/// {
///   "artifact_type": "plan",
///   "content": "{\"summary\": {\"context\": \"...\", \"scope_items\": [...] }, ...}",
///   "partial": false
/// }
/// ```
///
/// ## Required Fields
///
/// - `artifact_type` (`string`): One of `"plan"`, `"development_result"`, `"issues"`,
///   `"fix_result"`, `"commit_message"`. The `"review_issues"` alias is also accepted
///   and normalized to `"issues"`.
/// - `content` (`string`): JSON-serialized artifact payload (see schema for each type below).
///
/// ## Optional Fields
///
/// - `partial` (`bool`, default `false`): If `true`, the artifact is written to
///   `.agent/tmp/{artifact_type}.partial.json` for incremental preview. Partial artifacts
///   pass validation but are not recorded as the final artifact for the workflow.
///
/// # Response Shape
///
/// On success:
/// ```json
/// {
///   "content": [
///     {
///       "type": "text",
///       "text": "{\"accepted\": true, \"artifact_type\": \"plan\", \"partial\": false}"
///     }
///   ]
/// }
/// ```
///
/// On validation failure (schema errors):
/// ```json
/// {
///   "content": [
///     {
///       "type": "text",
///       "text": "{\"accepted\": false, \"validation_errors\": [{\"code\": \"MISSING_REQUIRED\", \"field_path\": \"summary.context\", ...}]}"
///     }
///   ],
///   "isError": true
/// }
/// ```
///
/// # Error Codes
///
/// - JSON-RPC `-32000` (Tool error): Returned when capability is denied, artifact_type
///   is unrecognized, or `content` is not valid JSON. Validation failures against the
///   artifact schema return `isError: true` in the `ToolResult` (not a JSON-RPC error).
/// - JSON-RPC `-32001` (NotInitialized): Returned if called before `initialize` handshake.
///
/// # Artifact Types and Schemas
///
/// Each artifact type has a JSON Schema enforced at submission time. The schema files
/// are embedded in the binary from `ralph-workflow/schemas/{artifact_type}.schema.json`.
///
/// - **`plan`**: Strategic execution plan with `summary`, `steps`, `critical_files`,
///   `risks_mitigations`, and `verification_strategy` fields. Required by the Planning
///   drain before Development agents are spawned.
/// - **`development_result`**: Outcome of a development iteration including
///   `result_type`, `evidence`, and optional `next_steps`.
/// - **`issues`** (alias `review_issues`): Structured issue list produced by Review drain.
/// - **`fix_result`**: Outcome of a fix attempt, referencing the original issue.
/// - **`commit_message`**: A formatted commit message artifact with `subject` and `body`.
///
/// # Side Effects
///
/// - Writes the artifact to `.agent/{artifact_type}.json` in the workspace (non-partial).
/// - Triggers a workflow state transition in the Ralph pipeline consumer on the next event loop tick.
/// - Partial submissions write to `.agent/tmp/{artifact_type}.partial.json` only.
///
/// # Idempotency
///
/// Not idempotent: each submission overwrites the previous artifact of the same type.
/// The last submitted value is authoritative.
///
/// # Stability
///
/// The `artifact_type` values and top-level response shape (`accepted`, `artifact_type`,
/// `partial`) are stable. Schema details within each artifact type may evolve across
/// Ralph versions.
pub fn handle_submit_artifact(
    session: &AgentSession,
    workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    require_artifact_submit_capability(session)?;
    validate_and_dispatch_artifact(workspace, &params)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::SessionDrain;
    use crate::workspace::memory_workspace::MemoryWorkspace;
    use std::sync::Arc;

    fn test_session() -> AgentSession {
        AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
    }

    fn test_workspace() -> Arc<MemoryWorkspace> {
        Arc::new(MemoryWorkspace::new_test())
    }

    #[test]
    fn test_submit_artifact_valid_plan_json() {
        let session = test_session();
        let workspace = test_workspace();

        let valid_plan = serde_json::json!({
            "summary": {
                "context": "Adding error types for MCP",
                "scope_items": [
                    {"text": "Add ErrorCode enum"},
                    {"text": "Add ValidationError struct"},
                    {"text": "Add ErrorResponse struct"}
                ]
            },
            "steps": [
                {
                    "number": 1,
                    "title": "Define error types",
                    "content": "Add ErrorCode, ValidationError, and ErrorResponse to types.rs"
                }
            ],
            "critical_files": {
                "primary_files": [
                    {"path": "src/types.rs", "action": "modify"}
                ]
            },
            "risks_mitigations": [
                {"risk": "Breaking existing API", "mitigation": "Add types only, no changes to existing"}
            ],
            "verification_strategy": [
                {"method": "cargo test", "expected_outcome": "All tests pass"}
            ]
        });

        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "plan",
                "content": serde_json::to_string(&valid_plan).unwrap()
            }),
        );

        assert!(result.is_ok(), "Valid plan should pass: {:?}", result.err());
        let tool_result = result.unwrap();
        assert!(!tool_result.is_error.unwrap_or(true));
        assert!(tool_result.content[0].text.contains("\"accepted\": true"));

        // Verify persistence
        let envelope = workspace.read_artifact_json("plan").unwrap();
        assert!(envelope.is_some());
        assert_eq!(envelope.unwrap().artifact_type, "plan");
    }

    #[test]
    fn test_submit_artifact_invalid_plan_returns_structured_error() {
        let session = test_session();
        let workspace = test_workspace();

        // Missing required fields: summary, steps, critical_files, etc.
        let invalid_plan = serde_json::json!({
            "steps": []
        });

        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "plan",
                "content": serde_json::to_string(&invalid_plan).unwrap()
            }),
        );

        let tool_result = result.expect("handle_submit_artifact must return Ok(ToolResult)");
        assert_eq!(
            tool_result.is_error,
            Some(true),
            "validation failure must set isError:true"
        );
        let content_text = &tool_result.content[0].text;
        assert!(
            content_text.contains("MISSING_FIELD") || content_text.contains("CONSTRAINT_VIOLATION"),
            "error JSON should contain structured error code, got: {}",
            content_text
        );
    }

    #[test]
    fn test_submit_artifact_missing_nested_field_uses_deterministic_field_path() {
        let session = test_session();
        let workspace = test_workspace();

        let invalid_plan = serde_json::json!({
            "summary": {
                "context": "Validation path test",
                "scope_items": [
                    {"text": "item-1"},
                    {"text": "item-2"},
                    {"text": "item-3"}
                ]
            },
            "steps": [
                {
                    "number": 1,
                    "content": "Step content is present"
                }
            ],
            "critical_files": {
                "primary_files": [
                    {"path": "src/example.rs", "action": "modify"}
                ]
            },
            "risks_mitigations": [
                {"risk": "R", "mitigation": "M"}
            ],
            "verification_strategy": [
                {"method": "cargo test", "expected_outcome": "pass"}
            ]
        });

        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "plan",
                "content": serde_json::to_string(&invalid_plan).unwrap()
            }),
        );

        let tool_result = result.expect("handle_submit_artifact must return Ok(ToolResult)");
        assert_eq!(
            tool_result.is_error,
            Some(true),
            "validation failure must set isError:true"
        );

        let content_text = &tool_result.content[0].text;
        let parsed: ErrorResponse = serde_json::from_str(content_text)
            .expect("content text must be valid ErrorResponse JSON");
        let has_target_path = parsed
            .errors
            .iter()
            .any(|error| error.field_path == "steps[0].title");

        assert!(
            has_target_path,
            "expected deterministic field path steps[0].title in errors: {:?}",
            parsed.errors
        );
    }

    #[test]
    fn test_submit_artifact_partial_mode() {
        let session = test_session();
        let workspace = test_workspace();

        // Partial plan missing some required fields
        let partial_plan = serde_json::json!({
            "steps": [
                {"number": 1, "title": "First step", "content": "Do the thing"}
            ]
        });

        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "plan",
                "content": serde_json::to_string(&partial_plan).unwrap(),
                "partial": true
            }),
        );

        // Partial mode accepts with errors
        assert!(
            result.is_ok(),
            "Partial should be accepted: {:?}",
            result.err()
        );
        let tool_result = result.unwrap();
        let text = &tool_result.content[0].text;
        assert!(text.contains("\"accepted\": true"));
        assert!(text.contains("\"partial\": true"));

        // Verify partial file was written
        let path = std::path::Path::new(".agent/tmp/plan.partial.json");
        assert!(workspace.exists(path));
    }

    #[test]
    fn test_submit_artifact_issues_canonical_name() {
        let session = test_session();
        let workspace = test_workspace();

        // Use "issues" (canonical name)
        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "issues",
                "content": r#"{"type": "issues_found", "issues": [{"text": "Bug found"}]}"#
            }),
        );

        // No schema yet for issues, so it should pass
        assert!(result.is_ok());
    }

    #[test]
    fn test_submit_artifact_review_issues_alias() {
        let session = test_session();
        let workspace = test_workspace();

        // Use "review_issues" (old name, should be accepted as alias)
        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "review_issues",
                "content": r#"{"type": "issues_found", "issues": [{"text": "Bug found"}]}"#
            }),
        );

        assert!(result.is_ok());
    }

    /// Verify that Planning session can submit artifacts.
    ///
    /// `ArtifactSubmit` is classified as a non-mutating capability (workflow coordination
    /// signal), so it is allowed in ReadOnly mode (Planning drain). The Planning agent's
    /// job is to write and submit a plan — blocking this would break the workflow.
    #[test]
    fn submit_artifact_allowed_for_planning_session() {
        // Planning session should have ArtifactSubmit (non-mutating coordination signal)
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
        let workspace = test_workspace();

        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "plan",
                "content": r#"{"summary":{"context":"test","scope_items":[{"text":"a"},{"text":"b"},{"text":"c"}]},"steps":[{"number":1,"title":"t","content":"c"}],"critical_files":{"primary_files":[{"path":"f","action":"modify"}]},"risks_mitigations":[{"risk":"r","mitigation":"m"}],"verification_strategy":[{"method":"m","expected_outcome":"o"}]}"#
            }),
        );

        assert!(
            result.is_ok(),
            "Planning session should be able to submit artifacts: {:?}",
            result.err()
        );
    }

    #[test]
    fn test_submit_artifact_missing_type() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "content": "{}"
            }),
        );

        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), ToolError::InvalidParams(_)));
    }

    #[test]
    fn test_submit_artifact_missing_content() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "plan"
            }),
        );

        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), ToolError::InvalidParams(_)));
    }

    #[test]
    fn test_submit_artifact_unknown_type() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "unknown_type",
                "content": "{}"
            }),
        );

        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(err.to_string().contains("Unknown artifact type"));
    }

    #[test]
    fn test_submit_artifact_empty_content() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "plan",
                "content": ""
            }),
        );

        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("empty"));
    }

    #[test]
    fn test_submit_artifact_invalid_json_content() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "plan",
                "content": "this is not json"
            }),
        );

        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("not valid JSON"));
    }

    #[test]
    fn test_submit_artifact_no_schema_passthrough() {
        let session = test_session();
        let workspace = test_workspace();

        // development_result has no schema yet — should pass through
        let result = handle_submit_artifact(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "artifact_type": "development_result",
                "content": r#"{"status": "completed", "summary": "Done"}"#
            }),
        );

        assert!(result.is_ok());

        // Should still persist the artifact
        let envelope = workspace.read_artifact_json("development_result").unwrap();
        assert!(envelope.is_some());
    }
}
