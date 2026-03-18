use std::io;

use crate::git_helpers::git2_to_io_error;
use itertools::Itertools;
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
    snapshot
        .lines()
        .filter_map(parse_status_line)
        .collect::<std::collections::HashSet<_>>()
        .into_iter()
        .sorted()
        .collect()
}

fn unquote_c_style(s: &str) -> Option<String> {
    let bytes = s.as_bytes();
    if bytes.len() < 2 || bytes[0] != b'"' || bytes[bytes.len() - 1] != b'"' {
        return None;
    }
    let inner = &bytes[1..bytes.len() - 1];
    let result = process_bytes(inner, 0, false, None);
    String::from_utf8(result).ok()
}

fn process_bytes(
    bytes: &[u8],
    i: usize,
    in_escape: bool,
    octal_val: Option<(u32, usize, usize)>,
) -> Vec<u8> {
    if i >= bytes.len() {
        return Vec::new();
    }

    let b = bytes[i];
    let mut result = Vec::new();

    if in_escape {
        match b {
            b'\\' => {
                result.push(b'\\');
                result.extend_from_slice(&process_bytes(bytes, i + 1, false, None));
            }
            b'"' => {
                result.push(b'"');
                result.extend_from_slice(&process_bytes(bytes, i + 1, false, None));
            }
            b'n' | b't' | b'r' | b'b' | b'f' | b'v' => {
                result.push(b'\\');
                result.push(b);
                result.extend_from_slice(&process_bytes(bytes, i + 1, false, None));
            }
            b'0'..=b'7' => {
                let octal = (u32::from(b - b'0'), 1, i);
                result.extend_from_slice(&process_bytes(bytes, i + 1, true, Some(octal)));
            }
            _ => {
                result.push(b'\\');
                result.push(b);
                result.extend_from_slice(&process_bytes(bytes, i + 1, false, None));
            }
        }
    } else if let Some((val, consumed, start_idx)) = octal_val {
        if b.is_ascii_digit() && consumed < 3 {
            let new_val = (val * 8) + u32::from(b - b'0');
            let new_consumed = consumed + 1;
            result.extend_from_slice(&process_bytes(
                bytes,
                i + 1,
                true,
                Some((new_val, new_consumed, start_idx)),
            ));
        } else {
            if let Ok(byte) = u8::try_from(val) {
                if byte < 0x20 || byte == 0x7F {
                    result.push(b'\\');
                    result.extend_from_slice(&bytes[start_idx..start_idx + consumed]);
                } else {
                    result.push(byte);
                }
            } else {
                result.push(b'\\');
                result.extend_from_slice(&bytes[start_idx..start_idx + consumed]);
            }
            if b == b'\\' {
                result.extend_from_slice(&process_bytes(bytes, i + 1, true, None));
            } else {
                result.push(b);
                result.extend_from_slice(&process_bytes(bytes, i + 1, false, None));
            }
        }
    } else {
        if b == b'\\' {
            result.extend_from_slice(&process_bytes(bytes, i + 1, true, None));
        } else {
            result.push(b);
            result.extend_from_slice(&process_bytes(bytes, i + 1, false, None));
        }
    }

    result
}

fn parse_status_line(line: &str) -> Option<String> {
    let bytes = line.as_bytes();
    if bytes.len() < 4 {
        return None;
    }
    if bytes[2] != b' ' {
        return None;
    }
    let x = bytes[0] as char;
    let y = bytes[1] as char;
    let path_spec = line[3..].trim_end();
    if path_spec.is_empty() {
        return None;
    }

    let path_spec = if x == 'R' || y == 'R' || x == 'C' || y == 'C' {
        path_spec
            .rsplit_once(" -> ")
            .map_or(path_spec, |(_, new_part)| new_part.trim_end())
    } else {
        path_spec
    };

    let parsed = parse_path_component(path_spec);
    if parsed.is_empty() {
        return None;
    }

    Some(parsed)
}

fn parse_path_component(raw: &str) -> String {
    let raw = raw.trim_end();
    unquote_c_style(raw).unwrap_or_else(|| raw.to_string())
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

    let lines: Vec<String> = statuses
        .iter()
        .map(|entry| {
            let status = entry.status();
            let path = entry.path().ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    "non-UTF8 path encountered in git status; cannot safely track residual files",
                )
            })?;
            let path = path.to_string();
            if path.bytes().any(|b| b < 0x20 || b == 0x7F) {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "control characters in path encountered in git status; cannot safely snapshot",
                ));
            }

            // Convert git2 status to porcelain format.
            // Untracked files are represented as "??" in porcelain v1.
            if status.contains(git2::Status::WT_NEW) {
                return Ok(format!("?? {path}\n"));
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

            Ok(format!("{index_status}{wt_status} {path}\n"))
        })
        .collect::<io::Result<Vec<_>>>()?;

    Ok(lines.into_iter().collect())
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
        use std::io;
        use std::os::unix::ffi::OsStrExt;

        let tmp = tempfile::tempdir().expect("tempdir");
        let root = tmp.path();
        let _repo = git2::Repository::init(root).expect("init repo");

        // Create a filename with bytes that are not valid UTF-8.
        let name = std::ffi::OsStr::from_bytes(&[0xFF, 0xFE, b'.', b't', b'x', b't']);
        std::fs::write(root.join(name), "x\n").expect("write non-utf8 file");

        let err = git_snapshot_in_repo(root).expect_err("expected error");
        assert_eq!(err.kind(), io::ErrorKind::InvalidData);
    }
}

#[cfg(test)]
mod snapshot_control_char_tests {
    use super::git_snapshot_in_repo;

    #[test]
    fn test_git_snapshot_in_repo_errors_on_control_characters_in_paths() {
        use std::io;

        let tmp = tempfile::tempdir().expect("tempdir");
        let root = tmp.path();
        let _repo = git2::Repository::init(root).expect("init repo");

        // Newlines are legal on Unix but cannot be safely represented in a newline-delimited
        // snapshot format. Reject to avoid snapshot injection.
        std::fs::write(root.join("x\nfile.rs"), "x\n").expect("write file with newline");

        let err = git_snapshot_in_repo(root).expect_err("expected error");
        assert_eq!(err.kind(), io::ErrorKind::InvalidData);
    }
}
