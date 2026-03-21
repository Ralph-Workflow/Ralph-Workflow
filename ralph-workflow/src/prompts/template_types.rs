//! Template validation types and error definitions.
//!
//! This module contains all the types used by the template validator:
//! validation results, variable info, errors, and warnings.

/// Template validation result.
#[derive(Debug, Clone)]
pub struct ValidationResult {
    /// Whether validation passed
    pub is_valid: bool,
    /// Variables referenced in the template
    pub variables: Vec<VariableInfo>,
    /// Partials referenced in the template
    pub partials: Vec<String>,
    /// Validation errors found
    pub errors: Vec<ValidationError>,
    /// Validation warnings found
    pub warnings: Vec<ValidationWarning>,
}

/// Information about a variable reference in a template.
#[derive(Debug, Clone)]
pub struct VariableInfo {
    /// Name of the variable
    pub name: String,
    /// Line number where variable appears (0-indexed)
    pub line: usize,
    /// Whether the variable has a default value
    pub has_default: bool,
    /// Default value if present
    pub default_value: Option<String>,
    /// Raw placeholder content between `{{` and `}}`
    pub placeholder: String,
}

/// Template validation error.
#[derive(Debug, Clone)]
pub enum ValidationError {
    /// Unclosed conditional block
    UnclosedConditional { line: usize },
    /// Unclosed loop block
    UnclosedLoop { line: usize },
    /// Invalid conditional syntax
    InvalidConditional { line: usize, syntax: String },
    /// Invalid loop syntax
    InvalidLoop { line: usize, syntax: String },
    /// Unclosed comment
    UnclosedComment { line: usize },
    /// Partial reference not found
    PartialNotFound { name: String },
}

/// Template validation warning.
#[derive(Debug, Clone)]
pub enum ValidationWarning {
    /// Variable appears to be unused (no default, might error if not provided)
    VariableMayError { name: String },
}

/// Error type for rendered prompt validation failures.
///
/// Returned when a rendered prompt still contains unresolved template
/// placeholders, indicating missing variables or template rendering failures.
#[derive(Debug, Clone)]
pub struct RenderedPromptError {
    /// Placeholder patterns that remain unresolved in the rendered output.
    pub unresolved_placeholders: Vec<String>,
}

impl std::fmt::Display for RenderedPromptError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Rendered prompt contains unresolved placeholders: {}",
            self.unresolved_placeholders.join(", ")
        )
    }
}

impl std::error::Error for RenderedPromptError {}

/// Error type for template variable enforcement failures.
///
/// This is used when a prompt template was rendered but the resulting prompt still
/// contains `{{...}}` patterns (unresolved placeholders) or when template rendering
/// cannot proceed due to missing variables.
///
/// The reducer consumes these failures via `AgentEvent::TemplateVariablesInvalid`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TemplateVariablesInvalidError {
    /// The template key/name (e.g. "`planning_xml`").
    pub template_name: String,
    /// Missing required variables (best-effort; may be empty when the renderer
    /// succeeded but placeholders remained in the output).
    pub missing_variables: Vec<String>,
    /// Unresolved `{{...}}` placeholder strings found in the rendered output.
    pub unresolved_placeholders: Vec<String>,
}

impl std::fmt::Display for TemplateVariablesInvalidError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Template variables invalid for template '{}': missing=[{}], unresolved=[{}]",
            self.template_name,
            self.missing_variables.join(", "),
            self.unresolved_placeholders.join(", ")
        )
    }
}

impl std::error::Error for TemplateVariablesInvalidError {}

/// Template metadata extracted from header comments.
#[derive(Debug, Clone)]
pub struct TemplateMetadata {
    /// Template version
    pub version: Option<String>,
    /// Template purpose description
    pub purpose: Option<String>,
}

// =========================================================================
// Substitution Log Types (for log-based validation)
// =========================================================================

/// How a placeholder was resolved during template rendering.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum SubstitutionSource {
    /// Value was provided by the caller.
    Value,
    /// Used the default value from template (e.g., {{VAR|default="x"}}).
    Default,
    /// Variable was provided but empty, used default.
    EmptyWithDefault,
}

/// Record of a single placeholder substitution.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct SubstitutionEntry {
    /// The placeholder name (e.g., "DIFF", "NAME").
    pub name: String,
    /// How it was resolved.
    pub source: SubstitutionSource,
}

/// Record of template substitution - stored in reducer state for validation.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct SubstitutionLog {
    /// Name of the template that was rendered.
    pub template_name: String,
    /// Placeholders that were successfully substituted (with source).
    pub substituted: Vec<SubstitutionEntry>,
    /// Placeholders that had no value AND no default (truly missing).
    pub unsubstituted: Vec<String>,
}

impl SubstitutionLog {
    /// Check if all required placeholders were substituted.
    /// Returns true if no placeholders are left unsubstituted.
    #[must_use]
    pub const fn is_complete(&self) -> bool {
        self.unsubstituted.is_empty()
    }

    /// Get names of placeholders that used their default values.
    #[must_use]
    pub fn defaults_used(&self) -> Vec<&str> {
        self.substituted
            .iter()
            .filter(|e| {
                matches!(
                    e.source,
                    SubstitutionSource::Default | SubstitutionSource::EmptyWithDefault
                )
            })
            .map(|e| e.name.as_str())
            .collect()
    }
}

/// Result of template rendering with substitution log.
#[derive(Debug, Clone)]
pub struct RenderedTemplate {
    pub content: String,
    pub log: SubstitutionLog,
}
