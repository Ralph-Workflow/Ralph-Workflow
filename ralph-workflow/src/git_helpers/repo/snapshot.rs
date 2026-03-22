use crate::git_helpers::domain::parse as domain_parse;
use crate::git_helpers::git2_to_io_error;
use std::path::Path;

/// Get a snapshot of the current git status.
///
/// Returns status in porcelain format (similar to `git status --porcelain=v1`).
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_snapshot() -> std::io::Result<String> {
    git_snapshot_in_repo(Path::new("."))
}

/// Get a snapshot of git status for a specific repository root.
///
/// Prefer this in pipeline code where `ctx.repo_root` is known, to avoid
/// accidentally discovering/inspecting the wrong repository.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_snapshot_in_repo(repo_root: &Path) -> std::io::Result<String> {
    let repo = git2::Repository::discover(repo_root).map_err(|e| git2_to_io_error(&e))?;
    git_snapshot_impl(&repo)
}

/// Extract repo-relative paths from a porcelain v1-style status snapshot.
///
/// The returned paths are suitable for carry-forward/prompt context and are intentionally
/// resilient to common porcelain edge cases:
/// - rename/copy lines in the form `old -> new` (returns `new`)
/// - quoted paths (returns the unquoted path)
///
/// This parser is used for residual-file detection and must be robust: incorrect path
/// extraction can pollute carry-forward state.
#[must_use]
pub fn parse_git_status_paths(snapshot: &str) -> Vec<String> {
    domain_parse::parse_git_status_paths(snapshot)
}

/// Implementation of git snapshot.
fn git_snapshot_impl(repo: &git2::Repository) -> std::io::Result<String> {
    let statuses = {
        let mut opts = domain_parse::configured_status_options();
        repo.statuses(Some(&mut opts))
            .map_err(|e| git2_to_io_error(&e))?
    };

    let lines = collect_status_lines(statuses)?;
    Ok(lines.into_iter().collect())
}

fn collect_status_lines(statuses: git2::Statuses) -> std::io::Result<Vec<String>> {
    statuses
        .iter()
        .map(|entry| status_entry_to_porcelain(&entry))
        .collect::<std::io::Result<Vec<_>>>()
}

fn status_entry_to_porcelain(entry: &git2::StatusEntry) -> std::io::Result<String> {
    let status = entry.status();
    let path = entry.path().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "non-UTF8 path encountered in git status; cannot safely track residual files",
        )
    })?;
    let path = path.to_string();
    domain_parse::validate_path_for_snapshot(&path).map_err(std::io::Error::from)?;
    Ok(domain_parse::format_status_porcelain(status, &path))
}

#[cfg(test)]
mod parse_tests {
    use super::parse_git_status_paths;

    #[test]
    fn test_parses_basic_xy_lines() {
        let snapshot = " M src/lib.rs\n?? new file.txt\n";
        let paths = parse_git_status_paths(snapshot);
        assert_eq!(
            paths,
            vec!["new file.txt".to_string(), "src/lib.rs".to_string()]
        );
    }

    #[test]
    fn test_parses_rename_arrow_takes_new_path() {
        let snapshot = "R  old/name.rs -> new/name.rs\n";
        let paths = parse_git_status_paths(snapshot);
        assert_eq!(paths, vec!["new/name.rs".to_string()]);
    }

    #[test]
    fn test_parses_quoted_paths_and_rename() {
        let snapshot = "?? \"dir with spaces/file.rs\"\nR  \"old name.rs\" -> \"new name.rs\"\n";
        let paths = parse_git_status_paths(snapshot);
        assert_eq!(
            paths,
            vec![
                "dir with spaces/file.rs".to_string(),
                "new name.rs".to_string()
            ]
        );
    }

    #[test]
    fn test_unquote_c_style_decodes_utf8_octal_bytes() {
        // Git porcelain uses C-style quoting with octal escapes for non-ASCII bytes.
        // "caf\303\251.txt" represents the UTF-8 bytes for "café.txt".
        let snapshot = "?? \"caf\\303\\251.txt\"\n";
        let paths = parse_git_status_paths(snapshot);
        assert_eq!(paths, vec!["café.txt".to_string()]);
    }

    #[test]
    fn test_unquote_c_style_preserves_control_escapes() {
        // Control-character escapes must not be decoded into real control characters.
        // This prevents control-character injection into prompts/state/logs.
        let snapshot = "?? \"x\\nsrc/file.rs\"\n";
        let paths = parse_git_status_paths(snapshot);
        assert_eq!(paths, vec!["x\\nsrc/file.rs".to_string()]);
        assert!(!paths[0].contains('\n'));
    }

    #[test]
    fn test_parse_git_status_paths_returns_sorted_paths() {
        let snapshot = "?? b.txt\n?? a.txt\n";
        let paths = parse_git_status_paths(snapshot);
        assert_eq!(paths, vec!["a.txt".to_string(), "b.txt".to_string()]);
    }
}

#[cfg(all(test, not(target_os = "macos")))]
mod snapshot_tests {
    use super::git_snapshot_in_repo;

    #[test]
    fn test_git_snapshot_in_repo_errors_on_non_utf8_paths() {
        use std::os::unix::ffi::OsStrExt;

        let tmp = tempfile::tempdir().expect("tempdir");
        let root = tmp.path();
        let _repo = git2::Repository::init(root).expect("init repo");

        // Create a filename with bytes that are not valid UTF-8.
        let name = std::ffi::OsStr::from_bytes(&[0xFF, 0xFE, b'.', b't', b'x', b't']);
        std::fs::write(root.join(name), "x\n").expect("write non-utf8 file");

        let err = git_snapshot_in_repo(root).expect_err("expected error");
        assert_eq!(err.kind(), std::io::ErrorKind::InvalidData);
    }
}

#[cfg(test)]
mod snapshot_control_char_tests {
    use super::git_snapshot_in_repo;

    #[test]
    fn test_git_snapshot_in_repo_errors_on_control_characters_in_paths() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let root = tmp.path();
        let _repo = git2::Repository::init(root).expect("init repo");

        // Newlines are legal on Unix but cannot be safely represented in a newline-delimited
        // snapshot format. Reject to avoid snapshot injection.
        std::fs::write(root.join("x\nfile.rs"), "x\n").expect("write file with newline");

        let err = git_snapshot_in_repo(root).expect_err("expected error");
        assert_eq!(err.kind(), std::io::ErrorKind::InvalidData);
    }
}
