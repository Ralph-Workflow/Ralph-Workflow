use crate::common::domain_types::GitOid;

/// Validation errors for file system state.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum ValidationError {
    /// A file that should exist is missing
    FileMissing { path: String },

    /// A file that shouldn't exist unexpectedly exists
    FileUnexpectedlyExists { path: String },

    /// A file's content has changed
    FileContentChanged { path: String },

    /// Git HEAD has changed
    GitHeadChanged { expected: GitOid, actual: GitOid },

    /// Git working tree has changes (files modified, staged, etc.)
    GitWorkingTreeChanged { changes: String },

    /// Git state is invalid
    GitStateInvalid { reason: String },
}

impl std::fmt::Display for ValidationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::FileMissing { path } => write!(f, "File missing: {path}"),
            Self::FileUnexpectedlyExists { path } => write!(f, "File unexpectedly exists: {path}"),
            Self::FileContentChanged { path } => write!(f, "File content changed: {path}"),
            Self::GitHeadChanged { expected, actual } => {
                write!(f, "Git HEAD changed: expected {expected}, got {actual}")
            }
            Self::GitWorkingTreeChanged { changes } => {
                write!(f, "Git working tree changed: {changes}")
            }
            Self::GitStateInvalid { reason } => write!(f, "Git state invalid: {reason}"),
        }
    }
}

impl std::error::Error for ValidationError {}
