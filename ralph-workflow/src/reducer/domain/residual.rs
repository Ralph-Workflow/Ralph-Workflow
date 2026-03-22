use crate::git_helpers::parse_git_status_paths;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResidualFilesStatusParseError {
    Empty,
}

/// Parse the `git status` snapshot produced by `git_snapshot_in_repo`.
///
/// Returns the list of paths when files remain, and `Empty` when the working
/// tree is clean after trimming whitespace.
#[must_use = "handle residual file status outcomes"]
pub fn parse_residual_files_status(
    snapshot: &str,
) -> Result<Vec<String>, ResidualFilesStatusParseError> {
    let trimmed = snapshot.trim();
    if trimmed.is_empty() {
        return Err(ResidualFilesStatusParseError::Empty);
    }

    Ok(parse_git_status_paths(snapshot))
}

#[cfg(test)]
mod tests {
    use super::{parse_residual_files_status, ResidualFilesStatusParseError};

    #[test]
    fn rejects_empty_snapshot() {
        assert_eq!(
            parse_residual_files_status("").unwrap_err(),
            ResidualFilesStatusParseError::Empty
        );
    }

    #[test]
    fn rejects_whitespace_only_snapshot() {
        assert_eq!(
            parse_residual_files_status("   \n  ").unwrap_err(),
            ResidualFilesStatusParseError::Empty
        );
    }

    #[test]
    fn accepts_non_empty_snapshot() {
        let snapshot = "?? foo.rs\n";
        let files = parse_residual_files_status(snapshot).expect("should parse non-empty snapshot");
        assert_eq!(files, vec!["foo.rs".to_string()]);
    }
}
