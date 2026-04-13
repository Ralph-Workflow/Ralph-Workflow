/// Wrapper type for a commit message string.
///
/// Used in `CommitExtractionOutcome::Valid` as the extracted result.
#[derive(Clone, Debug)]
struct CommitExtractionResult(String);

impl CommitExtractionResult {
    fn new(msg: String) -> Self {
        Self(msg)
    }

    fn into_message(self) -> String {
        self.0
    }
}

#[derive(Debug)]
enum CommitExtractionOutcome {
    MissingFile(String),
    InvalidXml(String),
    Valid {
        extracted: CommitExtractionResult,
        files: Vec<String>,
        excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile>,
    },
    Skipped(String),
}


pub(crate) enum ParsedCommitXmlOutcome {
    Skipped(String),
    Invalid(String),
    Valid {
        message: String,
        files: Vec<String>,
        excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile>,
    },
}

#[cfg(test)]
mod json_extraction_tests {
    use super::*;
    use crate::workspace::memory_workspace::MemoryWorkspace;
    use crate::workspace::ArtifactEnvelope;

    fn workspace_with_commit_json(content: serde_json::Value) -> MemoryWorkspace {
        let ws = MemoryWorkspace::new_test();
        let envelope = ArtifactEnvelope::new("commit_message", content, "2026-01-01T00:00:00Z");
        ws.write_artifact_json(&envelope).unwrap();
        ws
    }

    #[test]
    fn json_artifact_subject_only() {
        let ws = workspace_with_commit_json(serde_json::json!({
            "type": "commit",
            "subject": "feat: add feature"
        }));
        let outcome = extract_commit_message_from_file_with_workspace(&ws);
        match outcome {
            CommitExtractionOutcome::Valid { extracted, .. } => {
                assert_eq!(extracted.into_message(), "feat: add feature");
            }
            other => panic!("Expected Valid, got: {other:?}"),
        }
    }

    #[test]
    fn json_artifact_with_simple_body() {
        let ws = workspace_with_commit_json(serde_json::json!({
            "type": "commit",
            "subject": "fix(auth): prevent race",
            "body": "Token refresh now uses a mutex."
        }));
        let outcome = extract_commit_message_from_file_with_workspace(&ws);
        match outcome {
            CommitExtractionOutcome::Valid { extracted, .. } => {
                let msg = extracted.into_message();
                assert!(msg.starts_with("fix(auth): prevent race"));
                assert!(msg.contains("Token refresh now uses a mutex."));
            }
            other => panic!("Expected Valid, got: {other:?}"),
        }
    }

    #[test]
    fn json_artifact_with_detailed_body() {
        let ws = workspace_with_commit_json(serde_json::json!({
            "type": "commit",
            "subject": "feat: add CSV export",
            "body_summary": "Add CSV export for reports.",
            "body_details": "- Supports date range filter\n- Custom columns",
            "body_footer": "Fixes #42"
        }));
        let outcome = extract_commit_message_from_file_with_workspace(&ws);
        match outcome {
            CommitExtractionOutcome::Valid { extracted, .. } => {
                let msg = extracted.into_message();
                assert!(msg.contains("Add CSV export for reports."));
                assert!(msg.contains("Supports date range filter"));
                assert!(msg.contains("Fixes #42"));
            }
            other => panic!("Expected Valid, got: {other:?}"),
        }
    }

    #[test]
    fn json_artifact_skip() {
        let ws = workspace_with_commit_json(serde_json::json!({
            "type": "skip",
            "reason": "No meaningful changes"
        }));
        let outcome = extract_commit_message_from_file_with_workspace(&ws);
        match outcome {
            CommitExtractionOutcome::Skipped(reason) => {
                assert_eq!(reason, "No meaningful changes");
            }
            other => panic!("Expected Skipped, got: {other:?}"),
        }
    }

    #[test]
    fn json_artifact_legacy_skip_field() {
        let ws = workspace_with_commit_json(serde_json::json!({
            "skip": "Nothing to commit"
        }));
        let outcome = extract_commit_message_from_file_with_workspace(&ws);
        match outcome {
            CommitExtractionOutcome::Skipped(reason) => {
                assert_eq!(reason, "Nothing to commit");
            }
            other => panic!("Expected Skipped, got: {other:?}"),
        }
    }

    #[test]
    fn json_artifact_with_files() {
        let ws = workspace_with_commit_json(serde_json::json!({
            "type": "commit",
            "subject": "fix(auth): prevent race",
            "files": ["src/auth/token.rs", "tests/auth_test.rs"]
        }));
        let outcome = extract_commit_message_from_file_with_workspace(&ws);
        match outcome {
            CommitExtractionOutcome::Valid { files, .. } => {
                assert_eq!(files, vec!["src/auth/token.rs", "tests/auth_test.rs"]);
            }
            other => panic!("Expected Valid, got: {other:?}"),
        }
    }

    #[test]
    fn json_artifact_with_excluded_files() {
        let ws = workspace_with_commit_json(serde_json::json!({
            "type": "commit",
            "subject": "feat: add feature",
            "excluded_files": [
                {"path": "src/other.rs", "reason": "deferred"},
                {"path": ".env", "reason": "sensitive"}
            ]
        }));
        let outcome = extract_commit_message_from_file_with_workspace(&ws);
        match outcome {
            CommitExtractionOutcome::Valid { excluded_files, .. } => {
                assert_eq!(excluded_files.len(), 2);
                assert_eq!(excluded_files[0].path, "src/other.rs");
                assert_eq!(excluded_files[1].path, ".env");
            }
            other => panic!("Expected Valid, got: {other:?}"),
        }
    }

    #[test]
    fn json_artifact_missing_subject_is_invalid() {
        let ws = workspace_with_commit_json(serde_json::json!({
            "type": "commit",
            "body": "No subject provided"
        }));
        let outcome = extract_commit_message_from_file_with_workspace(&ws);
        assert!(matches!(outcome, CommitExtractionOutcome::InvalidXml(_)));
    }

    #[test]
    fn json_artifact_present_is_used() {
        let ws = workspace_with_commit_json(serde_json::json!({
            "type": "commit",
            "subject": "feat: from JSON"
        }));
        let outcome = extract_commit_message_from_file_with_workspace(&ws);
        match outcome {
            CommitExtractionOutcome::Valid { extracted, .. } => {
                assert_eq!(extracted.into_message(), "feat: from JSON");
            }
            other => panic!("Expected Valid with JSON content, got: {other:?}"),
        }
    }

    #[test]
    fn missing_json_returns_missing_file() {
        let ws = MemoryWorkspace::new_test();
        let outcome = extract_commit_message_from_file_with_workspace(&ws);
        assert!(matches!(outcome, CommitExtractionOutcome::MissingFile(_)));
    }
}

pub(crate) fn commit_outcome_event_from_validated(
    message: Option<String>,
    reason: Option<String>,
    attempt: u32,
) -> crate::reducer::event::PipelineEvent {
    match (message, reason) {
        (Some(message), _) => {
            crate::reducer::event::PipelineEvent::commit_message_generated(message, attempt)
        }
        (None, Some(reason)) => {
            crate::reducer::event::PipelineEvent::commit_message_validation_failed(reason, attempt)
        }
        (None, None) => crate::reducer::event::PipelineEvent::commit_generation_failed(
            "Commit validation outcome missing message and reason".to_string(),
        ),
    }
}

/// Try to extract a commit message from a JSON artifact envelope.
///
/// Returns `Some(outcome)` if `.agent/tmp/commit_message.json` exists and
/// was parseable, `None` if the file is absent (caller should fall back to XML).
fn try_extract_from_json_artifact(workspace: &dyn Workspace) -> Option<CommitExtractionOutcome> {
    let envelope = match workspace.read_artifact_json("commit_message") {
        Ok(Some(env)) => env,
        Ok(None) => return None,
        Err(err) => {
            return Some(CommitExtractionOutcome::InvalidXml(format!(
                "Invalid JSON artifact 'commit_message': {err}"
            )))
        }
    };

    let v = &envelope.content;

    // Check for skip variant
    if v.get("type").and_then(|t| t.as_str()) == Some("skip") {
        let reason = v
            .get("reason")
            .and_then(|r| r.as_str())
            .unwrap_or("no reason provided")
            .to_string();
        return Some(CommitExtractionOutcome::Skipped(reason));
    }

    // Also support legacy skip field (without type discriminator)
    if let Some(reason) = v.get("skip").and_then(|r| r.as_str()) {
        return Some(CommitExtractionOutcome::Skipped(reason.to_string()));
    }

    let Some(subject) = v.get("subject").and_then(|s| s.as_str()) else {
        return Some(CommitExtractionOutcome::InvalidXml(
            "JSON artifact missing required 'subject' field".to_string(),
        ));
    };

    let subject = subject.trim();
    if subject.is_empty() {
        return Some(CommitExtractionOutcome::InvalidXml(
            "JSON artifact has empty 'subject' field".to_string(),
        ));
    }

    // Build body from either simple "body" or detailed fields
    let body = if let Some(b) = v.get("body").and_then(|b| b.as_str()) {
        b.to_string()
    } else {
        let parts: Vec<&str> = [
            v.get("body_summary").and_then(|s| s.as_str()),
            v.get("body_details").and_then(|s| s.as_str()),
            v.get("body_footer").and_then(|s| s.as_str()),
        ]
        .into_iter()
        .flatten()
        .collect();
        if parts.is_empty() {
            String::new()
        } else {
            parts.join("\n\n")
        }
    };

    let message = if body.trim().is_empty() {
        subject.to_string()
    } else {
        format!("{subject}\n\n{}", body.trim())
    };

    let files: Vec<String> = v
        .get("files")
        .and_then(|f| f.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|item| item.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();

    let excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile> = v
        .get("excluded_files")
        .and_then(|f| f.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|item| {
                    let path = item.get("path")?.as_str()?.to_string();
                    let reason_str = item.get("reason")?.as_str()?;
                    let reason = match reason_str {
                        "internal_ignore" => {
                            crate::reducer::state::pipeline::ExcludedFileReason::InternalIgnore
                        }
                        "not_task_related" => {
                            crate::reducer::state::pipeline::ExcludedFileReason::NotTaskRelated
                        }
                        "sensitive" => {
                            crate::reducer::state::pipeline::ExcludedFileReason::Sensitive
                        }
                        "deferred" => crate::reducer::state::pipeline::ExcludedFileReason::Deferred,
                        _ => crate::reducer::state::pipeline::ExcludedFileReason::NotTaskRelated,
                    };
                    Some(crate::reducer::state::pipeline::ExcludedFile { path, reason })
                })
                .collect()
        })
        .unwrap_or_default();

    Some(CommitExtractionOutcome::Valid {
        extracted: CommitExtractionResult::new(message),
        files,
        excluded_files,
    })
}

/// Try to parse commit message from JSON artifact, returning a `ParsedCommitXmlOutcome`.
///
/// Returns `None` if no JSON artifact exists (caller should fall back to XML).
pub(crate) fn try_parse_commit_from_json_artifact(
    workspace: &dyn Workspace,
) -> Option<ParsedCommitXmlOutcome> {
    match try_extract_from_json_artifact(workspace)? {
        CommitExtractionOutcome::Skipped(reason) => Some(ParsedCommitXmlOutcome::Skipped(reason)),
        CommitExtractionOutcome::InvalidXml(detail) => {
            Some(ParsedCommitXmlOutcome::Invalid(detail))
        }
        CommitExtractionOutcome::MissingFile(_) => None,
        CommitExtractionOutcome::Valid {
            extracted,
            files,
            excluded_files,
        } => Some(ParsedCommitXmlOutcome::Valid {
            message: extracted.into_message(),
            files,
            excluded_files,
        }),
    }
}

/// Check whether a JSON commit artifact exists.
pub(crate) fn has_json_commit_artifact(workspace: &dyn Workspace) -> bool {
    match workspace.read_artifact_json("commit_message") {
        Ok(Some(_)) | Err(_) => true,
        Ok(None) => false,
    }
}

fn extract_commit_message_from_file_with_workspace(
    workspace: &dyn Workspace,
) -> CommitExtractionOutcome {
    try_extract_from_json_artifact(workspace).unwrap_or_else(|| {
        CommitExtractionOutcome::MissingFile(
            "No commit message found: JSON artifact (.agent/tmp/commit_message.json) absent"
                .to_string(),
        )
    })
}
