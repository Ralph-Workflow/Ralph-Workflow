use std::io;

use crate::git_helpers::git2_to_io_error;
use std::path::Path;

/// Get a snapshot of the current git status.
///
/// Returns status in porcelain format (similar to `git status --porcelain=v1`).
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_snapshot() -> io::Result<String> {
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
pub fn git_snapshot_in_repo(repo_root: &Path) -> io::Result<String> {
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
    fn unquote_c_style(s: &str) -> Option<String> {
        let bytes = s.as_bytes();
        if bytes.len() < 2 || bytes[0] != b'"' || bytes[bytes.len() - 1] != b'"' {
            return None;
        }

        let mut out = String::with_capacity(s.len().saturating_sub(2));
        let mut i = 1usize;
        while i + 1 < bytes.len() {
            let b = bytes[i];
            if b != b'\\' {
                out.push(b as char);
                i += 1;
                continue;
            }

            // Escape sequence
            i += 1;
            if i + 1 > bytes.len() {
                break;
            }
            let esc = bytes[i];
            match esc {
                b'\\' => out.push('\\'),
                b'"' => out.push('"'),
                b'n' => out.push('\n'),
                b't' => out.push('\t'),
                b'r' => out.push('\r'),
                b'b' => out.push('\x08'),
                b'f' => out.push('\x0C'),
                b'v' => out.push('\x0B'),
                b'0'..=b'7' => {
                    let mut val: u32 = u32::from(esc - b'0');
                    let mut consumed = 1usize;
                    while consumed < 3 {
                        let next_i = i + consumed;
                        if next_i + 1 >= bytes.len() {
                            break;
                        }
                        let nb = bytes[next_i];
                        if !(b'0'..=b'7').contains(&nb) {
                            break;
                        }
                        val = (val * 8) + u32::from(nb - b'0');
                        consumed += 1;
                    }
                    // Advance i by the extra digits consumed.
                    i += consumed - 1;
                    if let Some(ch) = char::from_u32(val) {
                        out.push(ch);
                    }
                }
                other => out.push(other as char),
            }
            i += 1;
        }

        Some(out)
    }

    fn parse_path_component(raw: &str) -> String {
        let raw = raw.trim_end();
        unquote_c_style(raw).unwrap_or_else(|| raw.to_string())
    }

    let mut out: Vec<String> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();

    for line in snapshot.lines() {
        let bytes = line.as_bytes();
        if bytes.len() < 4 {
            continue;
        }
        // Porcelain v1: 2 status chars + space + path
        if bytes[2] != b' ' {
            continue;
        }
        let x = bytes[0] as char;
        let y = bytes[1] as char;
        let mut path_spec = &line[3..];
        path_spec = path_spec.trim_end();
        if path_spec.is_empty() {
            continue;
        }

        // Rename/copy lines: `old -> new` (porcelain v1). Prefer the new path.
        if x == 'R' || y == 'R' || x == 'C' || y == 'C' {
            if let Some((_, new_part)) = path_spec.rsplit_once(" -> ") {
                path_spec = new_part.trim_end();
            }
        }

        let parsed = parse_path_component(path_spec);
        if parsed.is_empty() {
            continue;
        }

        if seen.insert(parsed.clone()) {
            out.push(parsed);
        }
    }

    out
}

/// Implementation of git snapshot.
fn git_snapshot_impl(repo: &git2::Repository) -> io::Result<String> {
    let mut opts = git2::StatusOptions::new();
    opts.include_untracked(true)
        .recurse_untracked_dirs(true)
        .include_ignored(false);
    let statuses = repo
        .statuses(Some(&mut opts))
        .map_err(|e| git2_to_io_error(&e))?;

    let mut result = String::new();
    for entry in statuses.iter() {
        let status = entry.status();
        let path = entry.path().unwrap_or("").to_string();

        // Convert git2 status to porcelain format.
        // Untracked files are represented as "??" in porcelain v1.
        if status.contains(git2::Status::WT_NEW) {
            result.push('?');
            result.push('?');
            result.push(' ');
            result.push_str(&path);
            result.push('\n');
            continue;
        }

        // Index status
        let index_status = if status.contains(git2::Status::INDEX_NEW) {
            'A'
        } else if status.contains(git2::Status::INDEX_MODIFIED) {
            'M'
        } else if status.contains(git2::Status::INDEX_DELETED) {
            'D'
        } else if status.contains(git2::Status::INDEX_RENAMED) {
            'R'
        } else if status.contains(git2::Status::INDEX_TYPECHANGE) {
            'T'
        } else {
            ' '
        };

        // Worktree status
        let wt_status = if status.contains(git2::Status::WT_MODIFIED) {
            'M'
        } else if status.contains(git2::Status::WT_DELETED) {
            'D'
        } else if status.contains(git2::Status::WT_RENAMED) {
            'R'
        } else if status.contains(git2::Status::WT_TYPECHANGE) {
            'T'
        } else {
            ' '
        };

        result.push(index_status);
        result.push(wt_status);
        result.push(' ');
        result.push_str(&path);
        result.push('\n');
    }

    Ok(result)
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
            vec!["src/lib.rs".to_string(), "new file.txt".to_string()]
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
}
