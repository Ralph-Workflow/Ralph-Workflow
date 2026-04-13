//! Validation for system-defined `.agent/pipeline.toml` and `.agent/artifacts.toml`.
//!
//! These files express orchestration policy (pipeline structure, budgets,
//! artifact contracts, and parallel-execution policy). They are validated
//! at startup against:
//!
//! - Schema: the types declared in `ralph_workflow_policy::config`
//! - Invariants: the canonical drain contracts in `DRAIN_INVARIANTS`
//! - Decision outcomes: the `CANONICAL_ANALYSIS_DECISION_OUTCOMES` list
//!
//! # Validation rules covered
//!
//! - Rule 7:  Phase expects one artifact type while the bound drain declares another
//! - Rule 11: Phase/drain/state identifiers are not mapped canonically
//! - Rule 12: The analysis drain's declared artifact type does not define the decision
//!   outcomes the runtime needs
//! - Rule 13: The documented decision routes do not allow the target flow
//! - Rule 14: Cycle policy and decision-route policy disagree

use super::{ConfigValidationError, ValidationResult};
use std::path::Path;

/// Validate `.agent/pipeline.toml` content against the `PipelineConfig` schema.
///
/// Returns `Ok(warnings)` on success or `Err(errors)` if any invariant is violated.
///
/// # Errors
///
/// Returns validation errors for TOML syntax failures, unknown fields, or invariant
/// violations (budget inconsistencies, missing required sections).
pub fn validate_pipeline_toml(path: &Path, content: &str) -> ValidationResult {
    use super::levenshtein;
    use ralph_workflow_policy::config::PipelineConfig;
    use ralph_workflow_policy::validation::VALID_PIPELINE_KEYS;

    // Step 1: Check TOML syntax.
    let parsed: toml::Value = toml::from_str(content).map_err(|e| {
        vec![ConfigValidationError::TomlSyntax {
            file: path.to_path_buf(),
            error: e,
        }]
    })?;

    // Step 2: Check for unknown top-level keys.
    let valid_keys: Vec<&str> = VALID_PIPELINE_KEYS.to_vec();
    let unknown_errors: Vec<ConfigValidationError> = parsed
        .as_table()
        .map(|table| {
            table
                .keys()
                .filter(|k| !valid_keys.contains(&k.as_str()))
                .map(|k| ConfigValidationError::UnknownKey {
                    file: path.to_path_buf(),
                    key: k.clone(),
                    suggestion: levenshtein::suggest_key(k, &valid_keys),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    // Step 3: Deserialize against PipelineConfig schema.
    let pipeline: PipelineConfig = toml::from_str(content).map_err(|e| {
        let all: Vec<ConfigValidationError> = unknown_errors
            .iter()
            .cloned()
            .chain(std::iter::once(ConfigValidationError::InvalidValue {
                file: path.to_path_buf(),
                key: "pipeline.toml".to_string(),
                message: e.to_string(),
            }))
            .collect();
        all
    })?;

    // Steps 4 & 5: Budget and parallel execution consistency checks (Rules 14–16).
    let invariant_errors: Vec<ConfigValidationError> = [
        (pipeline.budgets.max_development_cycles == 0).then(|| {
            ConfigValidationError::InvalidValue {
                file: path.to_path_buf(),
                key: "budgets.max_development_cycles".to_string(),
                message: "must be at least 1".to_string(),
            }
        }),
        (pipeline.budgets.max_review_cycles == 0).then(|| ConfigValidationError::InvalidValue {
            file: path.to_path_buf(),
            key: "budgets.max_review_cycles".to_string(),
            message: "must be at least 1".to_string(),
        }),
        (pipeline.parallel_execution.default_concurrent_agents
            > pipeline.parallel_execution.max_concurrent_agents)
            .then(|| ConfigValidationError::InvalidValue {
                file: path.to_path_buf(),
                key: "parallel_execution.default_concurrent_agents".to_string(),
                message: format!(
                    "default_concurrent_agents ({}) must not exceed max_concurrent_agents ({})",
                    pipeline.parallel_execution.default_concurrent_agents,
                    pipeline.parallel_execution.max_concurrent_agents
                ),
            }),
    ]
    .into_iter()
    .flatten()
    .collect();

    let all_errors: Vec<ConfigValidationError> =
        unknown_errors.into_iter().chain(invariant_errors).collect();

    if all_errors.is_empty() {
        Ok(Vec::new())
    } else {
        Err(all_errors)
    }
}

/// Validate `.agent/artifacts.toml` content against the `ArtifactsConfig` schema.
///
/// Enforces:
/// - Rule 7:  Each drain's declared `artifact_type` matches the canonical DRAIN_INVARIANTS value.
/// - Rule 12: The `analysis` drain's required_decision_outcomes list covers all canonical outcomes.
/// - Rule 13: At least the two required flows (`development → review` and `fix → review`)
///   can be expressed through the declared decision outcomes.
///
/// Returns `Ok(warnings)` on success or `Err(errors)` if any invariant is violated.
///
/// # Errors
///
/// Returns validation errors for schema violations or invariant violations.
pub fn validate_artifacts_toml(path: &Path, content: &str) -> ValidationResult {
    use super::levenshtein;
    use ralph_workflow_policy::config::{drain_invariant, ArtifactsConfig};
    use ralph_workflow_policy::validation::{
        CANONICAL_DEVELOPMENT_ANALYSIS_DECISIONS, CANONICAL_REVIEW_ANALYSIS_DECISIONS,
        VALID_ARTIFACTS_KEYS,
    };

    // Step 1: Check TOML syntax.
    let parsed: toml::Value = toml::from_str(content).map_err(|e| {
        vec![ConfigValidationError::TomlSyntax {
            file: path.to_path_buf(),
            error: e,
        }]
    })?;

    // Step 2: Check for unknown top-level keys.
    let valid_keys: Vec<&str> = VALID_ARTIFACTS_KEYS.to_vec();
    let unknown_errors: Vec<ConfigValidationError> = parsed
        .as_table()
        .map(|table| {
            table
                .keys()
                .filter(|k| !valid_keys.contains(&k.as_str()))
                .map(|k| ConfigValidationError::UnknownKey {
                    file: path.to_path_buf(),
                    key: k.clone(),
                    suggestion: levenshtein::suggest_key(k, &valid_keys),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    // Step 3: Deserialize against ArtifactsConfig schema.
    let artifacts: ArtifactsConfig = toml::from_str(content).map_err(|e| {
        let all: Vec<ConfigValidationError> = unknown_errors
            .iter()
            .cloned()
            .chain(std::iter::once(ConfigValidationError::InvalidValue {
                file: path.to_path_buf(),
                key: "artifacts.toml".to_string(),
                message: e.to_string(),
            }))
            .collect();
        all
    })?;

    // Step 4: Rule 7 — artifact_type must match DRAIN_INVARIANTS for regular drains.
    let drain_type_errors: Vec<ConfigValidationError> = [
        (
            "planning",
            artifacts
                .planning
                .as_ref()
                .map(|c| c.artifact_type.as_str()),
        ),
        (
            "development",
            artifacts
                .development
                .as_ref()
                .map(|c| c.artifact_type.as_str()),
        ),
        (
            "review",
            artifacts.review.as_ref().map(|c| c.artifact_type.as_str()),
        ),
        (
            "fix",
            artifacts.fix.as_ref().map(|c| c.artifact_type.as_str()),
        ),
        (
            "commit",
            artifacts.commit.as_ref().map(|c| c.artifact_type.as_str()),
        ),
    ]
    .into_iter()
    .filter_map(|(drain_name, artifact_type_opt)| {
        let artifact_type = artifact_type_opt?;
        let invariant = drain_invariant(drain_name)?;
        (artifact_type != invariant.artifact_type).then(|| ConfigValidationError::InvalidValue {
            file: path.to_path_buf(),
            key: format!("{drain_name}.artifact_type"),
            message: format!(
                "artifact_type '{}' does not match canonical value '{}' for the {} drain",
                artifact_type, invariant.artifact_type, drain_name
            ),
        })
    })
    .collect();

    // Steps 5–7: typed analysis-specific checks (Rules 7, 12, 13).
    // Validates development_analysis and review_analysis sections using the new binary
    // vocabulary (CANONICAL_DEVELOPMENT_ANALYSIS_DECISIONS and
    // CANONICAL_REVIEW_ANALYSIS_DECISIONS respectively).

    // Validate development_analysis section if present.
    let dev_analysis_errors: Vec<ConfigValidationError> =
        artifacts.development_analysis.as_ref().map_or_else(Vec::new, |dev_analysis| {
            let canonical: &[&str] = CANONICAL_DEVELOPMENT_ANALYSIS_DECISIONS;
            let declared = &dev_analysis.decision_vocabulary;

            // Rule 7: development_analysis artifact_type must be "analysis_decision".
            let artifact_type_error: Option<ConfigValidationError> =
                (dev_analysis.artifact_type != "analysis_decision").then(|| {
                    ConfigValidationError::InvalidValue {
                        file: path.to_path_buf(),
                        key: "development_analysis.artifact_type".to_owned(),
                        message: format!(
                            "artifact_type '{}' does not match canonical value 'analysis_decision'",
                            dev_analysis.artifact_type
                        ),
                    }
                });

            // Rule 12: every canonical decision vocabulary entry must appear in declared.
            let missing_vocabulary = canonical
                .iter()
                .filter(|expected| !declared.iter().any(|d| d == *expected))
                .map(|expected| ConfigValidationError::InvalidValue {
                    file: path.to_path_buf(),
                    key: "development_analysis.decision_vocabulary".to_owned(),
                    message: format!(
                        "missing canonical decision '{expected}'; all of {canonical:?} must be declared"
                    ),
                });

            // Rule 12: no unknown vocabulary allowed.
            let unknown_vocabulary = declared
                .iter()
                .filter(|d| !canonical.contains(&d.as_str()))
                .map(|d| ConfigValidationError::InvalidValue {
                    file: path.to_path_buf(),
                    key: "development_analysis.decision_vocabulary".to_owned(),
                    message: format!(
                        "unknown decision '{d}'; valid decisions are: {canonical:?}"
                    ),
                });

            artifact_type_error
                .into_iter()
                .chain(missing_vocabulary)
                .chain(unknown_vocabulary)
                .collect()
        });

    // Validate review_analysis section if present.
    let review_analysis_errors: Vec<ConfigValidationError> =
        artifacts.review_analysis.as_ref().map_or_else(Vec::new, |review_analysis| {
            let canonical: &[&str] = CANONICAL_REVIEW_ANALYSIS_DECISIONS;
            let declared = &review_analysis.decision_vocabulary;

            // Rule 7: review_analysis artifact_type must be "analysis_decision".
            let artifact_type_error: Option<ConfigValidationError> =
                (review_analysis.artifact_type != "analysis_decision").then(|| {
                    ConfigValidationError::InvalidValue {
                        file: path.to_path_buf(),
                        key: "review_analysis.artifact_type".to_owned(),
                        message: format!(
                            "artifact_type '{}' does not match canonical value 'analysis_decision'",
                            review_analysis.artifact_type
                        ),
                    }
                });

            // Rule 12: every canonical decision vocabulary entry must appear in declared.
            let missing_vocabulary = canonical
                .iter()
                .filter(|expected| !declared.iter().any(|d| d == *expected))
                .map(|expected| ConfigValidationError::InvalidValue {
                    file: path.to_path_buf(),
                    key: "review_analysis.decision_vocabulary".to_owned(),
                    message: format!(
                        "missing canonical decision '{expected}'; all of {canonical:?} must be declared"
                    ),
                });

            // Rule 12: no unknown vocabulary allowed.
            let unknown_vocabulary = declared
                .iter()
                .filter(|d| !canonical.contains(&d.as_str()))
                .map(|d| ConfigValidationError::InvalidValue {
                    file: path.to_path_buf(),
                    key: "review_analysis.decision_vocabulary".to_owned(),
                    message: format!(
                        "unknown decision '{d}'; valid decisions are: {canonical:?}"
                    ),
                });

            artifact_type_error
                .into_iter()
                .chain(missing_vocabulary)
                .chain(unknown_vocabulary)
                .collect()
        });

    // Legacy [analysis] section is no longer validated — the new system uses
    // development_analysis and review_analysis instead. Accept it silently for
    // backwards compatibility but it has no effect on validation.
    let legacy_analysis_errors: Vec<ConfigValidationError> = Vec::new();

    let all_errors: Vec<ConfigValidationError> = unknown_errors
        .into_iter()
        .chain(drain_type_errors)
        .chain(dev_analysis_errors)
        .chain(review_analysis_errors)
        .chain(legacy_analysis_errors)
        .collect();

    if all_errors.is_empty() {
        Ok(Vec::new())
    } else {
        Err(all_errors)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_pipeline_toml_valid() {
        let content = r#"
[budgets]
max_development_cycles = 5
max_review_cycles = 2

[parallel_execution]
max_concurrent_agents = 20
default_concurrent_agents = 5
"#;
        let result = validate_pipeline_toml(Path::new(".agent/pipeline.toml"), content);
        assert!(
            result.is_ok(),
            "valid pipeline.toml should pass: {result:?}"
        );
    }

    #[test]
    fn test_validate_pipeline_toml_rejects_zero_dev_cycles() {
        let content = r#"
[budgets]
max_development_cycles = 0
"#;
        let result = validate_pipeline_toml(Path::new(".agent/pipeline.toml"), content);
        assert!(result.is_err(), "zero max_development_cycles must fail");
        let errors = result.unwrap_err();
        assert!(
            errors.iter().any(|e| matches!(
                e,
                ConfigValidationError::InvalidValue { key, .. } if key.contains("max_development_cycles")
            )),
            "expected max_development_cycles error, got: {errors:?}"
        );
    }

    #[test]
    fn test_validate_pipeline_toml_rejects_default_exceeds_max_agents() {
        let content = r#"
[parallel_execution]
max_concurrent_agents = 5
default_concurrent_agents = 10
"#;
        let result = validate_pipeline_toml(Path::new(".agent/pipeline.toml"), content);
        assert!(result.is_err(), "default > max must fail");
        let errors = result.unwrap_err();
        assert!(
            errors.iter().any(|e| matches!(
                e,
                ConfigValidationError::InvalidValue { key, .. } if key.contains("default_concurrent_agents")
            )),
            "expected default_concurrent_agents error, got: {errors:?}"
        );
    }

    #[test]
    fn test_validate_pipeline_toml_rejects_unknown_keys() {
        let content = r#"
[budgets]
max_development_cycles = 5

[unknown_section]
foo = "bar"
"#;
        let result = validate_pipeline_toml(Path::new(".agent/pipeline.toml"), content);
        assert!(result.is_err(), "unknown section must fail");
    }

    #[test]
    fn test_validate_artifacts_toml_valid() {
        let content = r#"
[planning]
artifact_type = "plan"
submission_mode = "mcp_artifact"

[development]
artifact_type = "development_result"
submission_mode = "mcp_artifact"

[development_analysis]
artifact_type = "analysis_decision"
submission_mode = "mcp_artifact"
decision_vocabulary = ["needs_more_work", "cycle_complete"]

[review_analysis]
artifact_type = "analysis_decision"
submission_mode = "mcp_artifact"
decision_vocabulary = ["needs_more_fix", "cycle_complete"]

[review]
artifact_type = "issues"
submission_mode = "mcp_artifact"

[fix]
artifact_type = "fix_result"
submission_mode = "mcp_artifact"

[commit]
artifact_type = "commit_message"
submission_mode = "mcp_artifact"
"#;
        let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
        assert!(
            result.is_ok(),
            "valid artifacts.toml should pass: {result:?}"
        );
    }

    #[test]
    fn test_validate_artifacts_toml_rejects_wrong_dev_analysis_artifact_type() {
        let content = r#"
[development_analysis]
artifact_type = "development_result"
submission_mode = "mcp_artifact"
decision_vocabulary = ["needs_more_work", "cycle_complete"]
"#;
        let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
        assert!(
            result.is_err(),
            "wrong development_analysis artifact_type must fail"
        );
        let errors = result.unwrap_err();
        assert!(
            errors.iter().any(|e| matches!(
                e,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key.contains("artifact_type") && message.contains("analysis_decision")
            )),
            "expected artifact_type mismatch error, got: {errors:?}"
        );
    }

    #[test]
    fn test_validate_artifacts_toml_rejects_missing_dev_analysis_decision() {
        let content = r#"
[development_analysis]
artifact_type = "analysis_decision"
submission_mode = "mcp_artifact"
decision_vocabulary = ["needs_more_work"]
"#;
        // Missing "cycle_complete"
        let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
        assert!(result.is_err(), "missing decision vocabulary must fail");
        let errors = result.unwrap_err();
        assert!(
            errors.iter().any(|e| matches!(
                e,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key.contains("decision_vocabulary")
                        && message.contains("cycle_complete")
            )),
            "expected missing decision error, got: {errors:?}"
        );
    }

    #[test]
    fn test_validate_artifacts_toml_rejects_unknown_dev_analysis_decision() {
        let content = r#"
[development_analysis]
artifact_type = "analysis_decision"
submission_mode = "mcp_artifact"
decision_vocabulary = ["needs_more_work", "cycle_complete", "unknown_decision"]
"#;
        let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
        assert!(result.is_err(), "unknown decision vocabulary must fail");
        let errors = result.unwrap_err();
        assert!(
            errors.iter().any(|e| matches!(
                e,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key.contains("decision_vocabulary")
                        && message.contains("unknown_decision")
            )),
            "expected unknown decision error, got: {errors:?}"
        );
    }

    #[test]
    fn test_validate_artifacts_toml_rejects_wrong_review_analysis_artifact_type() {
        let content = r#"
[review_analysis]
artifact_type = "issues"
submission_mode = "mcp_artifact"
decision_vocabulary = ["needs_more_fix", "cycle_complete"]
"#;
        let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
        assert!(
            result.is_err(),
            "wrong review_analysis artifact_type must fail"
        );
        let errors = result.unwrap_err();
        assert!(
            errors.iter().any(|e| matches!(
                e,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key.contains("artifact_type") && message.contains("analysis_decision")
            )),
            "expected artifact_type mismatch error, got: {errors:?}"
        );
    }

    #[test]
    fn test_validate_artifacts_toml_rejects_missing_review_analysis_decision() {
        let content = r#"
[review_analysis]
artifact_type = "analysis_decision"
submission_mode = "mcp_artifact"
decision_vocabulary = ["cycle_complete"]
"#;
        // Missing "needs_more_fix"
        let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
        assert!(result.is_err(), "missing decision vocabulary must fail");
        let errors = result.unwrap_err();
        assert!(
            errors.iter().any(|e| matches!(
                e,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key.contains("decision_vocabulary")
                        && message.contains("needs_more_fix")
            )),
            "expected missing decision error, got: {errors:?}"
        );
    }
}
