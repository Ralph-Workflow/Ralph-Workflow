//! Validation key constants for configuration schema enforcement.
//!
//! These constants define the valid key names for each configuration section.
//! They are used for detecting unknown keys and suggesting corrections.

/// Valid keys for the built-in [`agent_drains`] section.
pub const VALID_AGENT_DRAIN_KEYS: &[&str] = &[
    "planning",
    "development",
    "review",
    "fix",
    "commit",
    "analysis",
];

/// Valid keys for the [`orchestration`] section.
pub const VALID_ORCHESTRATION_KEYS: &[&str] = &[
    "forbid_sibling_drain_inference",
    "require_explicit_drain_bindings",
];

/// Valid keys for per-drain config table (used when agent_drains.<drain> = { ... }).
pub const VALID_DRAIN_CONFIG_KEYS: &[&str] = &["chain"];

/// Valid top-level keys for pipeline.toml.
pub const VALID_PIPELINE_KEYS: &[&str] = &[
    "top_level_phases",
    "embedded_decision_points",
    "decision_routes",
    "budgets",
    "artifact_acceptance",
    "validation",
    "parallel_execution",
];

/// Valid top-level keys for artifacts.toml.
pub const VALID_ARTIFACTS_KEYS: &[&str] = &[
    "planning",
    "development",
    "analysis",
    "review",
    "fix",
    "commit",
];

/// Valid keys within a drain artifact config section.
pub const VALID_DRAIN_ARTIFACT_CONFIG_KEYS: &[&str] = &[
    "artifact_type",
    "required_sections",
    "submission_mode",
    "required_decision_outcomes",
];

/// Canonical analysis decision outcome keys, in declaration order.
pub const CANONICAL_ANALYSIS_DECISION_OUTCOMES: &[&str] = &[
    "needs_more_work",
    "needs_replanning",
    "ready_for_review",
    "ready_to_commit",
    "needs_another_review",
];
