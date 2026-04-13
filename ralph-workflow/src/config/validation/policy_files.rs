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
//! - Rule 12: The analysis drain's declared artifact type does not define the decision outcomes
//!            the runtime needs
//! - Rule 13: The documented decision routes do not allow the target flow
//! - Rule 14: Cycle policy and decision-route policy disagree

use std::path::Path;
use super::{ConfigValidationError, ValidationResult};

/// Validate `.agent/pipeline.toml` content against the `PipelineConfig` schema.
///
/// Returns `Ok(warnings)` on success or `Err(errors)` if any invariant is violated.
///
/// # Errors
///
/// Returns validation errors for TOML syntax failures, unknown fields, or invariant
/// violations (budget inconsistencies, missing required sections).
pub fn validate_pipeline_toml(path: &Path, content: &str) -> ValidationResult {
    use ralph_workflow_policy::config::PipelineConfig;
    use ralph_workflow_policy::validation::VALID_PIPELINE_KEYS;
    use super::levenshtein;

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

    // Step 4: Budget-consistency checks (Rule 14).
    let mut invariant_errors: Vec<ConfigValidationError> = Vec::new();

    if pipeline.budgets.max_development_cycles == 0 {
        invariant_errors.push(ConfigValidationError::InvalidValue {
            file: path.to_path_buf(),
            key: "budgets.max_development_cycles".to_string(),
            message: "must be at least 1".to_string(),
        });
    }
    if pipeline.budgets.max_review_cycles == 0 {
        invariant_errors.push(ConfigValidationError::InvalidValue {
            file: path.to_path_buf(),
            key: "budgets.max_review_cycles".to_string(),
            message: "must be at least 1".to_string(),
        });
    }

    // Step 5: Parallel execution consistency checks (Rules 15-16).
    if pipeline.parallel_execution.default_concurrent_agents
        > pipeline.parallel_execution.max_concurrent_agents
    {
        invariant_errors.push(ConfigValidationError::InvalidValue {
            file: path.to_path_buf(),
            key: "parallel_execution.default_concurrent_agents".to_string(),
            message: format!(
                "default_concurrent_agents ({}) must not exceed max_concurrent_agents ({})",
                pipeline.parallel_execution.default_concurrent_agents,
                pipeline.parallel_execution.max_concurrent_agents
            ),
        });
    }

    let all_errors: Vec<ConfigValidationError> = unknown_errors
        .into_iter()
        .chain(invariant_errors)
        .collect();

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
/// - Rule 13: At least the two required flows (`development → review` and `fix → review`) can be
///            expressed through the declared decision outcomes.
///
/// Returns `Ok(warnings)` on success or `Err(errors)` if any invariant is violated.
///
/// # Errors
///
/// Returns validation errors for schema violations or invariant violations.
pub fn validate_artifacts_toml(path: &Path, content: &str) -> ValidationResult {
    use ralph_workflow_policy::config::{drain_invariant, ArtifactsConfig};
    use ralph_workflow_policy::validation::{
        CANONICAL_ANALYSIS_DECISION_OUTCOMES, VALID_ARTIFACTS_KEYS,
    };
    use super::levenshtein;

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

    let mut invariant_errors: Vec<ConfigValidationError> = Vec::new();

    // Step 4: Rule 7 — artifact_type must match DRAIN_INVARIANTS.
    macro_rules! check_artifact_type {
        ($drain_name:literal, $config:expr) => {
            if let Some(drain_config) = $config {
                if let Some(invariant) = drain_invariant($drain_name) {
                    if drain_config.artifact_type != invariant.artifact_type {
                        invariant_errors.push(ConfigValidationError::InvalidValue {
                            file: path.to_path_buf(),
                            key: concat!($drain_name, ".artifact_type").to_owned(),
                            message: format!(
                                "artifact_type '{}' does not match canonical value '{}' for the {} drain",
                                drain_config.artifact_type, invariant.artifact_type, $drain_name
                            ),
                        });
                    }
                }
            }
        };
    }

    check_artifact_type!("planning", &artifacts.planning);
    check_artifact_type!("development", &artifacts.development);
    check_artifact_type!("review", &artifacts.review);
    check_artifact_type!("fix", &artifacts.fix);
    check_artifact_type!("commit", &artifacts.commit);

    // Step 5: Rule 7 — analysis artifact_type.
    if let Some(analysis) = &artifacts.analysis {
        if let Some(invariant) = drain_invariant("analysis") {
            if analysis.artifact_type != invariant.artifact_type {
                invariant_errors.push(ConfigValidationError::InvalidValue {
                    file: path.to_path_buf(),
                    key: "analysis.artifact_type".to_owned(),
                    message: format!(
                        "artifact_type '{}' does not match canonical value '{}' for the analysis drain",
                        analysis.artifact_type, invariant.artifact_type
                    ),
                });
            }
        }

        // Step 6: Rule 12 — required_decision_outcomes must cover all canonical outcomes.
        let canonical: &[&str] = CANONICAL_ANALYSIS_DECISION_OUTCOMES;
        let declared = &analysis.required_decision_outcomes;

        // Every canonical outcome must appear in declared.
        for expected in canonical {
            if !declared.iter().any(|d| d == expected) {
                invariant_errors.push(ConfigValidationError::InvalidValue {
                    file: path.to_path_buf(),
                    key: "analysis.required_decision_outcomes".to_owned(),
                    message: format!(
                        "missing canonical outcome '{expected}'; \
                         all of {canonical:?} must be declared"
                    ),
                });
            }
        }

        // No unknown outcomes are allowed.
        for declared_outcome in declared {
            if !canonical.contains(&declared_outcome.as_str()) {
                invariant_errors.push(ConfigValidationError::InvalidValue {
                    file: path.to_path_buf(),
                    key: "analysis.required_decision_outcomes".to_owned(),
                    message: format!(
                        "unknown decision outcome '{declared_outcome}'; \
                         valid outcomes are: {canonical:?}"
                    ),
                });
            }
        }

        // Step 7: Rule 13 — verify the two required flows are expressible.
        // The flow `development → review` requires "ready_for_review" to be present.
        // The flow `fix → commit` requires "ready_to_commit" to be present.
        let required_for_dev_flow = ["ready_for_review", "needs_more_work", "needs_replanning"];
        let required_for_fix_flow = ["ready_to_commit", "needs_another_review"];
        for outcome in &required_for_dev_flow {
            if !declared.iter().any(|d| d == outcome) {
                invariant_errors.push(ConfigValidationError::InvalidValue {
                    file: path.to_path_buf(),
                    key: "analysis.required_decision_outcomes".to_owned(),
                    message: format!(
                        "outcome '{outcome}' is required for the development→analysis flow \
                         (planning → development → analysis)"
                    ),
                });
            }
        }
        for outcome in &required_for_fix_flow {
            if !declared.iter().any(|d| d == outcome) {
                invariant_errors.push(ConfigValidationError::InvalidValue {
                    file: path.to_path_buf(),
                    key: "analysis.required_decision_outcomes".to_owned(),
                    message: format!(
                        "outcome '{outcome}' is required for the review→fix→analysis flow \
                         (review → fix → analysis)"
                    ),
                });
            }
        }
    }

    let all_errors: Vec<ConfigValidationError> = unknown_errors
        .into_iter()
        .chain(invariant_errors)
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
        assert!(result.is_ok(), "valid pipeline.toml should pass: {result:?}");
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

[analysis]
artifact_type = "analysis_decision"
submission_mode = "mcp_artifact"
required_decision_outcomes = [
  "needs_more_work",
  "needs_replanning",
  "ready_for_review",
  "ready_to_commit",
  "needs_another_review",
]

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
        assert!(result.is_ok(), "valid artifacts.toml should pass: {result:?}");
    }

    #[test]
    fn test_validate_artifacts_toml_rejects_wrong_analysis_artifact_type() {
        let content = r#"
[analysis]
artifact_type = "development_result"
submission_mode = "mcp_artifact"
required_decision_outcomes = [
  "needs_more_work",
  "needs_replanning",
  "ready_for_review",
  "ready_to_commit",
  "needs_another_review",
]
"#;
        let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
        assert!(result.is_err(), "wrong analysis artifact_type must fail");
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
    fn test_validate_artifacts_toml_rejects_missing_decision_outcome() {
        let content = r#"
[analysis]
artifact_type = "analysis_decision"
submission_mode = "mcp_artifact"
required_decision_outcomes = [
  "needs_more_work",
  "needs_replanning",
  "ready_for_review",
]
"#;
        // Missing "ready_to_commit" and "needs_another_review"
        let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
        assert!(result.is_err(), "missing decision outcomes must fail");
        let errors = result.unwrap_err();
        assert!(
            errors.iter().any(|e| matches!(
                e,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key.contains("required_decision_outcomes")
                        && message.contains("ready_to_commit")
            )),
            "expected missing outcome error, got: {errors:?}"
        );
    }

    #[test]
    fn test_validate_artifacts_toml_rejects_unknown_decision_outcome() {
        let content = r#"
[analysis]
artifact_type = "analysis_decision"
submission_mode = "mcp_artifact"
required_decision_outcomes = [
  "needs_more_work",
  "needs_replanning",
  "ready_for_review",
  "ready_to_commit",
  "needs_another_review",
  "unknown_outcome",
]
"#;
        let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
        assert!(result.is_err(), "unknown decision outcome must fail");
        let errors = result.unwrap_err();
        assert!(
            errors.iter().any(|e| matches!(
                e,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key.contains("required_decision_outcomes")
                        && message.contains("unknown_outcome")
            )),
            "expected unknown outcome error, got: {errors:?}"
        );
    }
}
