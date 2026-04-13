//! Validation key constants and diagnostic types for configuration schema enforcement.
//!
//! These constants define the valid key names for each configuration section.
//! They are used for detecting unknown keys and suggesting corrections.
//!
//! `ValidationDiagnostic` is the structured error type emitted during startup
//! config validation (rules 1–16 and 23 from the configurable-orchestration plan).

/// A single validation diagnostic emitted during startup config checking.
///
/// Each diagnostic identifies the source file, section, and key that failed,
/// describes the kind of failure, and provides an actionable message for
/// the config author.
///
/// Diagnostics are collected rather than returned immediately so that all
/// violations in a config file can be reported in one pass rather than
/// failing on the first error encountered.
#[derive(Debug, Clone)]
pub struct ValidationDiagnostic {
    /// The config file that contains the violation
    /// (e.g., `"agents.toml"`, `"pipeline.toml"`, `"artifacts.toml"`).
    pub file: &'static str,
    /// The section or key path within that file
    /// (e.g., `"agent_drains.planning"`, `"budgets.max_development_cycles"`).
    pub location: String,
    /// What kind of failure this is.
    pub kind: DiagnosticKind,
    /// Actionable description of the failure for the config author.
    pub message: String,
    /// What Ralph expected instead (optional — omit when the fix is obvious from `message`).
    pub expected: Option<String>,
}

impl ValidationDiagnostic {
    /// Construct a `UnknownKey` diagnostic.
    #[must_use]
    pub fn unknown_key(
        file: &'static str,
        location: impl Into<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            file,
            location: location.into(),
            kind: DiagnosticKind::UnknownKey,
            message: message.into(),
            expected: None,
        }
    }

    /// Construct a `MissingRequired` diagnostic.
    #[must_use]
    pub fn missing_required(
        file: &'static str,
        location: impl Into<String>,
        message: impl Into<String>,
        expected: impl Into<String>,
    ) -> Self {
        Self {
            file,
            location: location.into(),
            kind: DiagnosticKind::MissingRequired,
            message: message.into(),
            expected: Some(expected.into()),
        }
    }

    /// Construct a `PolicyViolation` diagnostic.
    #[must_use]
    pub fn policy_violation(
        file: &'static str,
        location: impl Into<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            file,
            location: location.into(),
            kind: DiagnosticKind::PolicyViolation,
            message: message.into(),
            expected: None,
        }
    }

    /// Construct an `UnresolvedReference` diagnostic.
    #[must_use]
    pub fn unresolved_reference(
        file: &'static str,
        location: impl Into<String>,
        message: impl Into<String>,
        expected: impl Into<String>,
    ) -> Self {
        Self {
            file,
            location: location.into(),
            kind: DiagnosticKind::UnresolvedReference,
            message: message.into(),
            expected: Some(expected.into()),
        }
    }
}

impl std::fmt::Display for ValidationDiagnostic {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "[{}] {} ({}): {}",
            self.file, self.location, self.kind, self.message
        )?;
        if let Some(expected) = &self.expected {
            write!(f, "; expected: {expected}")?;
        }
        Ok(())
    }
}

/// The kind of validation failure in a `ValidationDiagnostic`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiagnosticKind {
    /// An unknown key was found where only specific keys are allowed.
    UnknownKey,
    /// A required key is missing.
    MissingRequired,
    /// A field value violates a policy invariant.
    PolicyViolation,
    /// A field value has the wrong type or shape.
    TypeMismatch,
    /// A reference (e.g., to a chain or phase) resolves to nothing.
    UnresolvedReference,
    /// Two declarations conflict with each other.
    Conflict,
}

impl std::fmt::Display for DiagnosticKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownKey => write!(f, "unknown_key"),
            Self::MissingRequired => write!(f, "missing_required"),
            Self::PolicyViolation => write!(f, "policy_violation"),
            Self::TypeMismatch => write!(f, "type_mismatch"),
            Self::UnresolvedReference => write!(f, "unresolved_reference"),
            Self::Conflict => write!(f, "conflict"),
        }
    }
}

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
    "phase_documents",
    "extra_phase_documents",
    "top_level_phases",
    "cycle_accounting",
    "embedded_decision_points",
    "decision_routes",
    "budgets",
    "artifact_acceptance",
    "validation",
    "parallel_execution",
    "phase_side_effects",
];

/// Valid top-level keys for artifacts.toml.
pub const VALID_ARTIFACTS_KEYS: &[&str] = &[
    "planning",
    "development",
    "development_analysis",
    "review_analysis",
    "analysis",
    "review",
    "fix",
    "commit",
    "development_commit",
    "review_commit",
];

/// Valid keys within a drain artifact config section.
pub const VALID_DRAIN_ARTIFACT_CONFIG_KEYS: &[&str] = &[
    "artifact_type",
    "required_sections",
    "submission_mode",
    "required_decision_outcomes",
    "status_field",
    "allowed_statuses",
    "decision_vocabulary",
    "prompt_template",
    "template_variables",
    "continuation_template",
    "artifact_output_path",
];

/// Canonical development analysis decision outcome keys, in declaration order.
///
/// These are the only valid values for `decision` in a `development_analysis` artifact.
pub const CANONICAL_DEVELOPMENT_ANALYSIS_DECISIONS: &[&str] =
    &["needs_more_work", "cycle_complete"];

/// Canonical review analysis decision outcome keys, in declaration order.
///
/// These are the only valid values for `decision` in a `review_analysis` artifact.
pub const CANONICAL_REVIEW_ANALYSIS_DECISIONS: &[&str] = &["needs_more_fix", "cycle_complete"];

/// Legacy canonical analysis decision outcome keys, in declaration order.
///
/// Kept for backward-compatibility assertions. New code should use
/// `CANONICAL_DEVELOPMENT_ANALYSIS_DECISIONS` or `CANONICAL_REVIEW_ANALYSIS_DECISIONS`.
pub const CANONICAL_ANALYSIS_DECISION_OUTCOMES: &[&str] = &[
    "needs_more_work",
    "needs_replanning",
    "ready_for_review",
    "ready_to_commit",
    "needs_another_review",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn development_analysis_decisions_are_binary() {
        assert_eq!(CANONICAL_DEVELOPMENT_ANALYSIS_DECISIONS.len(), 2);
        assert!(CANONICAL_DEVELOPMENT_ANALYSIS_DECISIONS.contains(&"needs_more_work"));
        assert!(CANONICAL_DEVELOPMENT_ANALYSIS_DECISIONS.contains(&"cycle_complete"));
    }

    #[test]
    fn review_analysis_decisions_are_binary() {
        assert_eq!(CANONICAL_REVIEW_ANALYSIS_DECISIONS.len(), 2);
        assert!(CANONICAL_REVIEW_ANALYSIS_DECISIONS.contains(&"needs_more_fix"));
        assert!(CANONICAL_REVIEW_ANALYSIS_DECISIONS.contains(&"cycle_complete"));
    }

    #[test]
    fn valid_pipeline_keys_includes_new_keys() {
        assert!(VALID_PIPELINE_KEYS.contains(&"phase_documents"));
        assert!(VALID_PIPELINE_KEYS.contains(&"cycle_accounting"));
        assert!(VALID_PIPELINE_KEYS.contains(&"phase_side_effects"));
        assert!(VALID_PIPELINE_KEYS.contains(&"extra_phase_documents"));
    }

    #[test]
    fn valid_artifacts_keys_includes_analysis_variants() {
        assert!(VALID_ARTIFACTS_KEYS.contains(&"development_analysis"));
        assert!(VALID_ARTIFACTS_KEYS.contains(&"review_analysis"));
        assert!(VALID_ARTIFACTS_KEYS.contains(&"development_commit"));
        assert!(VALID_ARTIFACTS_KEYS.contains(&"review_commit"));
    }

    #[test]
    fn diagnostic_display_includes_file_and_location() {
        let d = ValidationDiagnostic::unknown_key(
            "agents.toml",
            "agent_drains.unknown_drain",
            "unknown drain 'unknown_drain'; valid drains are: planning, development, ...",
        );
        let s = d.to_string();
        assert!(s.contains("agents.toml"));
        assert!(s.contains("agent_drains.unknown_drain"));
        assert!(s.contains("unknown_key"));
    }

    #[test]
    fn diagnostic_missing_required_includes_expected() {
        let d = ValidationDiagnostic::missing_required(
            "pipeline.toml",
            "budgets",
            "max_development_cycles is required",
            "a positive integer",
        );
        assert_eq!(d.kind, DiagnosticKind::MissingRequired);
        assert!(d.expected.is_some());
        assert!(d.to_string().contains("a positive integer"));
    }
}
