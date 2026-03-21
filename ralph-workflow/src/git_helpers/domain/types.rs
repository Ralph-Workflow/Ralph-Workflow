//! Typed error variants for git domain operations.
//!
//! Domain functions return `GitError` instead of `std::io::Error` so that
//! callers can match on specific failure modes without parsing string messages.
//! Boundary code converts `GitError` to `std::io::Error` via the `From` impl.

/// Typed error for git domain operations.
///
/// Pure domain functions return `Result<T, GitError>`.  Boundary code
/// converts to `std::io::Error` using the provided `From` impl.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GitError {
    /// Git repository could not be found at the given path.
    NotARepository,
    /// Repository has no commits yet (unborn branch).
    NoCommits,
    /// Parsing or interpreting git data failed.
    ParseFailed {
        /// Short description of what failed and where.
        context: String,
    },
    /// A git operation produced an unexpected error.
    ExecutionFailed(String),
}

impl std::fmt::Display for GitError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NotARepository => write!(f, "not a git repository"),
            Self::NoCommits => write!(f, "repository has no commits"),
            Self::ParseFailed { context } => write!(f, "parse failed: {context}"),
            Self::ExecutionFailed(msg) => write!(f, "git operation failed: {msg}"),
        }
    }
}

impl From<GitError> for std::io::Error {
    fn from(err: GitError) -> Self {
        let kind = match &err {
            GitError::NotARepository | GitError::NoCommits => std::io::ErrorKind::NotFound,
            GitError::ParseFailed { .. } => std::io::ErrorKind::InvalidData,
            GitError::ExecutionFailed(_) => std::io::ErrorKind::Other,
        };
        std::io::Error::new(kind, err.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::GitError;

    #[test]
    fn test_git_error_display_not_a_repository() {
        assert_eq!(GitError::NotARepository.to_string(), "not a git repository");
    }

    #[test]
    fn test_git_error_display_no_commits() {
        assert_eq!(GitError::NoCommits.to_string(), "repository has no commits");
    }

    #[test]
    fn test_git_error_display_parse_failed() {
        let err = GitError::ParseFailed {
            context: "control chars in path".to_string(),
        };
        assert_eq!(err.to_string(), "parse failed: control chars in path");
    }

    #[test]
    fn test_git_error_display_execution_failed() {
        let err = GitError::ExecutionFailed("exit 1".to_string());
        assert_eq!(err.to_string(), "git operation failed: exit 1");
    }

    #[test]
    fn test_git_error_into_io_error_kind_not_a_repository() {
        let io_err: std::io::Error = GitError::NotARepository.into();
        assert_eq!(io_err.kind(), std::io::ErrorKind::NotFound);
    }

    #[test]
    fn test_git_error_into_io_error_kind_no_commits() {
        let io_err: std::io::Error = GitError::NoCommits.into();
        assert_eq!(io_err.kind(), std::io::ErrorKind::NotFound);
    }

    #[test]
    fn test_git_error_into_io_error_kind_parse_failed() {
        let io_err: std::io::Error = GitError::ParseFailed {
            context: "bad data".to_string(),
        }
        .into();
        assert_eq!(io_err.kind(), std::io::ErrorKind::InvalidData);
    }

    #[test]
    fn test_git_error_into_io_error_kind_execution_failed() {
        let io_err: std::io::Error = GitError::ExecutionFailed("oops".to_string()).into();
        assert_eq!(io_err.kind(), std::io::ErrorKind::Other);
    }

    #[test]
    fn test_git_error_into_io_error_message_is_display() {
        let err = GitError::ParseFailed {
            context: "test context".to_string(),
        };
        let expected_msg = err.to_string();
        let io_err: std::io::Error = err.into();
        assert_eq!(io_err.to_string(), expected_msg);
    }

    #[test]
    fn test_git_error_clone_and_partial_eq() {
        let a = GitError::ParseFailed {
            context: "x".to_string(),
        };
        let b = a.clone();
        assert_eq!(a, b);
        assert_ne!(a, GitError::NotARepository);
    }
}
