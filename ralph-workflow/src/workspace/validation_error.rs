// Validation error types for artifact validation.
//
// These types are used by `ArtifactEnvelope` to store validation errors
// encountered during artifact submission validation.

use serde::{Deserialize, Serialize};

// ============================================================================
// Error Codes
// ============================================================================

/// Error codes for artifact validation failures.
///
/// Exactly 4 variants covering all JSON Schema validation failure categories.
/// Serializes to SCREAMING_SNAKE_CASE for machine parseability.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ErrorCode {
    /// Required field is absent or null.
    MissingField,
    /// Value not in allowed enum set.
    InvalidEnum,
    /// Value type does not match schema (e.g., string where integer expected).
    TypeMismatch,
    /// Value violates a schema constraint (minItems, pattern, etc.).
    ConstraintViolation,
}

// ============================================================================
// Validation Error
// ============================================================================

/// A single field-level validation error with directive-style recovery actions.
///
/// Each error pinpoints a specific field and tells the agent exactly what to do.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ValidationError {
    /// Which category of validation failure occurred.
    pub code: ErrorCode,
    /// JSON-path-subset pointing to the offending field (e.g., "steps[0].title").
    pub field_path: String,
    /// What the schema expected (e.g., "non-empty string", "integer >= 1").
    pub expected: String,
    /// What was actually found, serialized as a string. None if the field was absent.
    pub got: Option<String>,
    /// 1-3 imperative sentences telling the agent what to do.
    pub next_actions: Vec<String>,
    /// Optional imperative sentence telling the agent what NOT to do.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prohibition: Option<String>,
}

impl ValidationError {
    /// Create a missing field error.
    pub fn missing_field(
        field_path: impl Into<String>,
        expected: impl Into<String>,
        next_actions: Vec<String>,
    ) -> Self {
        Self {
            code: ErrorCode::MissingField,
            field_path: field_path.into(),
            expected: expected.into(),
            got: None,
            next_actions,
            prohibition: None,
        }
    }

    /// Create an invalid enum error.
    pub fn invalid_enum(
        field_path: impl Into<String>,
        expected: impl Into<String>,
        got: impl Into<String>,
        next_actions: Vec<String>,
    ) -> Self {
        Self {
            code: ErrorCode::InvalidEnum,
            field_path: field_path.into(),
            expected: expected.into(),
            got: Some(got.into()),
            next_actions,
            prohibition: None,
        }
    }

    /// Create a type mismatch error.
    pub fn type_mismatch(
        field_path: impl Into<String>,
        expected: impl Into<String>,
        got: impl Into<String>,
        next_actions: Vec<String>,
    ) -> Self {
        Self {
            code: ErrorCode::TypeMismatch,
            field_path: field_path.into(),
            expected: expected.into(),
            got: Some(got.into()),
            next_actions,
            prohibition: None,
        }
    }

    /// Create a constraint violation error.
    pub fn constraint_violation(
        field_path: impl Into<String>,
        expected: impl Into<String>,
        got: impl Into<String>,
        next_actions: Vec<String>,
    ) -> Self {
        Self {
            code: ErrorCode::ConstraintViolation,
            field_path: field_path.into(),
            expected: expected.into(),
            got: Some(got.into()),
            next_actions,
            prohibition: None,
        }
    }

    /// Add a prohibition to this error.
    pub fn with_prohibition(mut self, prohibition: impl Into<String>) -> Self {
        self.prohibition = Some(prohibition.into());
        self
    }
}

// Conversion from mcp_server::types::ValidationError to workspace::validation_error::ValidationError.
impl From<crate::mcp_server::types::ValidationError> for ValidationError {
    fn from(err: crate::mcp_server::types::ValidationError) -> Self {
        ValidationError {
            code: err.code.into(),
            field_path: err.field_path,
            expected: err.expected,
            got: err.got,
            next_actions: err.next_actions,
            prohibition: err.prohibition,
        }
    }
}

// Conversion from workspace::validation_error::ValidationError to mcp_server::types::ValidationError.
impl From<ValidationError> for crate::mcp_server::types::ValidationError {
    fn from(err: ValidationError) -> Self {
        crate::mcp_server::types::ValidationError {
            code: err.code.into(),
            field_path: err.field_path,
            expected: err.expected,
            got: err.got,
            next_actions: err.next_actions,
            prohibition: err.prohibition,
        }
    }
}

// Conversion from mcp_server::types::ErrorCode to workspace::validation_error::ErrorCode.
impl From<crate::mcp_server::types::ErrorCode> for ErrorCode {
    fn from(code: crate::mcp_server::types::ErrorCode) -> Self {
        match code {
            crate::mcp_server::types::ErrorCode::MissingField => ErrorCode::MissingField,
            crate::mcp_server::types::ErrorCode::InvalidEnum => ErrorCode::InvalidEnum,
            crate::mcp_server::types::ErrorCode::TypeMismatch => ErrorCode::TypeMismatch,
            crate::mcp_server::types::ErrorCode::ConstraintViolation => {
                ErrorCode::ConstraintViolation
            }
        }
    }
}

// Conversion from workspace::validation_error::ErrorCode to mcp_server::types::ErrorCode.
impl From<ErrorCode> for crate::mcp_server::types::ErrorCode {
    fn from(code: ErrorCode) -> Self {
        match code {
            ErrorCode::MissingField => crate::mcp_server::types::ErrorCode::MissingField,
            ErrorCode::InvalidEnum => crate::mcp_server::types::ErrorCode::InvalidEnum,
            ErrorCode::TypeMismatch => crate::mcp_server::types::ErrorCode::TypeMismatch,
            ErrorCode::ConstraintViolation => {
                crate::mcp_server::types::ErrorCode::ConstraintViolation
            }
        }
    }
}
