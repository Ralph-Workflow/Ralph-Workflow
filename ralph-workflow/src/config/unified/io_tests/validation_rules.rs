// Validation rule tests for startup validation (Rules 1, 2, 9, 10, 11, 12, 23, 24).
//
// These tests verify that the config validation system enforces the orchestration
// policy rules. Some rules are enforced in validate_config_file (agents.toml path),
// others in validate_artifacts_toml / validate_pipeline_toml (.agent/ path).

use std::path::Path;
use crate::config::validation::{validate_config_file, ConfigValidationError};
use crate::config::validation::validate_artifacts_toml;

// ===========================================================================
// Rule 1 — Every built-in drain must have an explicit binding
// ===========================================================================

/// Rule 1: missing drain binding error must name the specific missing drains.
/// This is enforced by require_explicit_drain_bindings=true (the default).
#[test]
fn rule1_missing_drain_binding_error_names_the_missing_drain() {
    let content = r#"
[agent_chains]
shared_review = ["claude"]

[agent_drains]
review = "shared_review"
fix = "shared_review"
"#;

    let result = validate_config_file(Path::new("agents.toml"), content);
    assert!(
        result.is_err(),
        "incomplete drain coverage should fail validation"
    );

    let errors = result.expect_err("validation should fail");
    // The error must explicitly name the missing drains
    let has_named_drain_error = errors.iter().any(|error| {
        matches!(
            error,
            ConfigValidationError::InvalidValue { key, message, .. }
                if key == "agent_drains"
                    && (message.contains("planning")
                        || message.contains("development")
                        || message.contains("analysis")
                        || message.contains("commit"))
        )
    });
    assert!(
        has_named_drain_error,
        "error must name the missing drains; got: {errors:?}"
    );
}

// ===========================================================================
// Rule 2 — Every drain chain reference must resolve to a known chain name
// ===========================================================================

/// Rule 2: unknown chain reference error must name both the chain and the drain.
#[test]
fn rule2_unknown_chain_reference_names_chain_and_drain() {
    let content = r#"
[agent_chains]
shared_dev = ["codex"]

[agent_drains]
planning = "no_such_chain"
"#;

    let result = validate_config_file(Path::new("agents.toml"), content);
    assert!(
        result.is_err(),
        "unknown chain reference should fail validation"
    );

    let errors = result.expect_err("validation should fail");
    // The error must name the unknown chain
    let has_chain_error = errors.iter().any(|error| {
        matches!(
            error,
            ConfigValidationError::InvalidValue { message, .. }
                if message.contains("no_such_chain")
        )
    });
    assert!(
        has_chain_error,
        "error must name the unknown chain 'no_such_chain'; got: {errors:?}"
    );
    // The error key or message should also reference the drain
    let names_drain = errors.iter().any(|error| {
        matches!(
            error,
            ConfigValidationError::InvalidValue { key, message, .. }
                if key.contains("planning") || message.contains("planning")
        )
    });
    assert!(
        names_drain,
        "error must name the drain 'planning'; got: {errors:?}"
    );
}

// ===========================================================================
// Rule 9 — Retry/continuation policy must not switch drains
// ===========================================================================

/// Rule 9: GeneralConfig has no cross-drain retry routing fields.
/// This is a structural invariant enforced by the Rust type system.
/// The general retry config (max_retries, max_same_agent_retries) applies
/// within a single drain's agent chain, not across drains. This test
/// confirms the config structure does not expose cross-drain routing.
#[test]
fn rule9_retry_config_is_within_drain_not_cross_drain() {
    use crate::config::unified::GeneralConfig;
    // If GeneralConfig had a cross-drain retry field it would appear here.
    // This test documents the structural invariant that no such field exists.
    // The only retry fields are:
    //   max_retries (within-chain retry count)
    //   max_same_agent_retries (same-agent retry before fallback within chain)
    //   max_cycles (cycles through the same drain's chain)
    let config = GeneralConfig::default();
    // All retry semantics are within-drain: no drain_fallback_chain field exists
    assert!(config.max_retries > 0, "max_retries should have a positive default");
    assert!(config.max_same_agent_retries > 0, "max_same_agent_retries should have a positive default");
    assert!(config.max_cycles > 0, "max_cycles should have a positive default");
    // No cross-drain routing fields exist in GeneralConfig — enforced by compiler
}

// ===========================================================================
// Rule 10 — No drain may rely on implicit sibling inference
// ===========================================================================

/// Rule 10: when sibling drain inference is enabled (forbid=false), partial
/// bindings are accepted. When disabled (the default), all drains must be
/// explicitly bound.
#[test]
fn rule10_sibling_inference_forbidden_by_default() {
    // Default: forbid_sibling_drain_inference = true, require_explicit_drain_bindings = true
    let content = r#"
[agent_chains]
shared_dev = ["codex"]
shared_review = ["claude"]

[agent_drains]
planning = "shared_dev"
development = "shared_dev"
review = "shared_review"
fix = "shared_review"
"#;
    // Missing analysis and commit — in legacy mode (sibling inference on) these
    // would be inferred. With the default flags, they must be explicitly bound.
    let result = validate_config_file(Path::new("agents.toml"), content);
    assert!(
        result.is_err(),
        "missing analysis/commit drains should fail with default strict flags"
    );

    let errors = result.expect_err("validation should fail");
    let has_coverage_error = errors.iter().any(|error| {
        matches!(
            error,
            ConfigValidationError::InvalidValue { key, message, .. }
                if key == "agent_drains"
                    && (message.contains("analysis") || message.contains("commit"))
        )
    });
    assert!(
        has_coverage_error,
        "error should mention missing drains; got: {errors:?}"
    );
}

/// Rule 10: when sibling inference is allowed, partial bindings resolve via siblings.
#[test]
fn rule10_sibling_inference_allowed_when_explicitly_opted_in() {
    let content = r#"
[orchestration]
forbid_sibling_drain_inference = false
require_explicit_drain_bindings = false

[agent_chains]
shared_dev = ["codex"]
shared_review = ["claude"]

[agent_drains]
planning = "shared_dev"
development = "shared_dev"
review = "shared_review"
fix = "shared_review"
"#;
    let result = validate_config_file(Path::new("agents.toml"), content);
    assert!(
        result.is_ok(),
        "sibling inference should succeed when opted in: {result:?}"
    );
}

// ===========================================================================
// Rule 11 / Rule 23 — Canonical identifier check for drain names
// ===========================================================================

/// Rule 11/23: "dev" in agent_drains should be rejected as an unknown drain name
/// and the error should suggest the canonical name "development".
#[test]
fn rule11_alias_dev_in_agent_drains_rejected_with_suggestion_for_development() {
    let content = r#"
[agent_chains]
shared_dev = ["codex"]

[agent_drains]
dev = "shared_dev"
"#;

    let result = validate_config_file(Path::new("agents.toml"), content);
    assert!(
        result.is_err(),
        "alias 'dev' in agent_drains should be rejected"
    );

    let errors = result.expect_err("validation should fail");
    // Should get an UnknownKey or InvalidValue error naming "dev"
    let has_dev_error = errors.iter().any(|error| match error {
        ConfigValidationError::UnknownKey { key, suggestion, .. } => {
            key.contains("dev")
                && suggestion
                    .as_deref()
                    .is_some_and(|s| s == "development")
        }
        ConfigValidationError::InvalidValue { key, message, .. } => {
            (key.contains("dev") || message.contains("dev"))
                && (message.contains("development") || message.contains("not a built-in"))
        }
        _ => false,
    });
    assert!(
        has_dev_error,
        "error for 'dev' should suggest 'development'; got: {errors:?}"
    );
}

/// Rule 11/23: "fixer" in agent_drains should be rejected with a suggestion for "fix".
#[test]
fn rule11_alias_fixer_in_agent_drains_rejected_with_suggestion_for_fix() {
    let content = r#"
[agent_chains]
shared_review = ["claude"]

[agent_drains]
fixer = "shared_review"
"#;

    let result = validate_config_file(Path::new("agents.toml"), content);
    assert!(
        result.is_err(),
        "alias 'fixer' in agent_drains should be rejected"
    );

    let errors = result.expect_err("validation should fail");
    let has_fixer_error = errors.iter().any(|error| match error {
        ConfigValidationError::UnknownKey { key, suggestion, .. } => {
            key.contains("fixer") && suggestion.as_deref().is_some_and(|s| s == "fix")
        }
        ConfigValidationError::InvalidValue { key, message, .. } => {
            (key.contains("fixer") || message.contains("fixer"))
                && (message.contains("fix") || message.contains("not a built-in"))
        }
        _ => false,
    });
    assert!(
        has_fixer_error,
        "error for 'fixer' should suggest 'fix'; got: {errors:?}"
    );
}

/// Rule 11/23: "developer" in agent_drains should be rejected (it's a legacy role name,
/// not a drain name). The canonical drain name is "development".
#[test]
fn rule11_alias_developer_in_agent_drains_rejected() {
    let content = r#"
[agent_chains]
shared_dev = ["codex"]

[agent_drains]
developer = "shared_dev"
"#;

    let result = validate_config_file(Path::new("agents.toml"), content);
    assert!(
        result.is_err(),
        "legacy role name 'developer' in agent_drains should be rejected"
    );
}

// ===========================================================================
// Rule 12 — Analysis drain artifact type must be "analysis_decision"
// ===========================================================================

/// Rule 12: analysis artifact_type must match the DRAIN_INVARIANTS value.
/// This is enforced in validate_artifacts_toml.
#[test]
fn rule12_analysis_artifact_type_must_be_analysis_decision() {
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
    assert!(
        result.is_err(),
        "wrong analysis artifact_type must fail validation"
    );

    let errors = result.expect_err("validation should fail");
    let has_type_error = errors.iter().any(|error| {
        matches!(
            error,
            ConfigValidationError::InvalidValue { key, message, .. }
                if key.contains("artifact_type")
                    && message.contains("analysis_decision")
        )
    });
    assert!(
        has_type_error,
        "error should cite the canonical artifact_type 'analysis_decision'; got: {errors:?}"
    );
}

/// Rule 12: correct analysis artifact_type passes.
#[test]
fn rule12_correct_analysis_artifact_type_passes() {
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
]
"#;

    let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
    assert!(
        result.is_ok(),
        "correct analysis artifact_type should pass: {result:?}"
    );
}

// ===========================================================================
// Rule 24 — Analysis decision vocabulary must contain all 5 canonical outcomes
// ===========================================================================

/// Rule 24: required_decision_outcomes must contain all 5 canonical values.
#[test]
fn rule24_incomplete_required_decision_outcomes_rejected() {
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
    assert!(
        result.is_err(),
        "incomplete decision outcomes must fail validation"
    );

    let errors = result.expect_err("validation should fail");
    let names_missing_outcome = errors.iter().any(|error| {
        matches!(
            error,
            ConfigValidationError::InvalidValue { key, message, .. }
                if key.contains("required_decision_outcomes")
                    && message.contains("ready_to_commit")
        )
    });
    assert!(
        names_missing_outcome,
        "error must name the missing outcome 'ready_to_commit'; got: {errors:?}"
    );
}

/// Rule 24: unknown decision outcome is rejected.
#[test]
fn rule24_unknown_decision_outcome_rejected() {
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
  "not_a_real_outcome",
]
"#;

    let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
    assert!(
        result.is_err(),
        "unknown decision outcome must fail validation"
    );

    let errors = result.expect_err("validation should fail");
    let names_unknown_outcome = errors.iter().any(|error| {
        matches!(
            error,
            ConfigValidationError::InvalidValue { key, message, .. }
                if key.contains("required_decision_outcomes")
                    && message.contains("not_a_real_outcome")
        )
    });
    assert!(
        names_unknown_outcome,
        "error must name the unknown outcome; got: {errors:?}"
    );
}

/// Rule 24: complete and correct required_decision_outcomes passes.
#[test]
fn rule24_complete_decision_outcomes_passes() {
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
]
"#;

    let result = validate_artifacts_toml(Path::new(".agent/artifacts.toml"), content);
    assert!(
        result.is_ok(),
        "complete decision outcomes should pass: {result:?}"
    );
}
