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
    /// JSON-path-subset pointing to the offending field (e.g., "steps\[0\].title").
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

// Conversion from mcp_server::ValidationError to workspace::validation_error::ValidationError.
impl From<mcp_server::ValidationError> for ValidationError {
    fn from(err: mcp_server::ValidationError) -> Self {
        ValidationError {
            code: err.code.into(),
            field_path: err.field_path,
            expected: err.expected,
            got: err.got.as_str().map(String::from).filter(|s| !s.is_empty()),
            next_actions: err.next_actions,
            prohibition: err.prohibition,
        }
    }
}

// Conversion from workspace::validation_error::ValidationError to mcp_server::ValidationError.
impl From<ValidationError> for mcp_server::ValidationError {
    fn from(err: ValidationError) -> Self {
        mcp_server::ValidationError {
            code: err.code.into(),
            field_path: err.field_path,
            expected: err.expected,
            got: err
                .got
                .map(serde_json::Value::String)
                .unwrap_or(serde_json::Value::Null),
            next_actions: err.next_actions,
            prohibition: err.prohibition,
        }
    }
}

// Conversion from mcp_server::ErrorCode to workspace::validation_error::ErrorCode.
impl From<mcp_server::ErrorCode> for ErrorCode {
    fn from(code: mcp_server::ErrorCode) -> Self {
        match code {
            mcp_server::ErrorCode::MissingField => ErrorCode::MissingField,
            mcp_server::ErrorCode::InvalidEnum => ErrorCode::InvalidEnum,
            mcp_server::ErrorCode::TypeMismatch => ErrorCode::TypeMismatch,
            mcp_server::ErrorCode::ConstraintViolation => ErrorCode::ConstraintViolation,
        }
    }
}

// Conversion from workspace::validation_error::ErrorCode to mcp_server::ErrorCode.
impl From<ErrorCode> for mcp_server::ErrorCode {
    fn from(code: ErrorCode) -> Self {
        match code {
            ErrorCode::MissingField => mcp_server::ErrorCode::MissingField,
            ErrorCode::InvalidEnum => mcp_server::ErrorCode::InvalidEnum,
            ErrorCode::TypeMismatch => mcp_server::ErrorCode::TypeMismatch,
            ErrorCode::ConstraintViolation => mcp_server::ErrorCode::ConstraintViolation,
        }
    }
}
