//! Pure git status and diff parsing functions — no I/O.
//!
//! Functions in this module operate on plain values (strings, bit-flag enums,
//! builder structs) and have no filesystem, repository, or process dependencies.
//! They are safe to unit-test without infrastructure.

use itertools::Itertools;

/// Extract repo-relative paths from a porcelain v1-style status snapshot.
///
/// The returned paths are suitable for carry-forward/prompt context and are
/// intentionally resilient to common porcelain edge cases:
/// - rename/copy lines in the form `old -> new` (returns `new`)
/// - quoted paths (returns the unquoted path)
///
/// This parser is used for residual-file detection and must be robust:
/// incorrect path extraction can pollute carry-forward state.
#[must_use]
pub(crate) fn parse_git_status_paths(snapshot: &str) -> Vec<String> {
    snapshot
        .lines()
        .filter_map(parse_status_line)
        .collect::<std::collections::HashSet<_>>()
        .into_iter()
        .sorted()
        .collect()
}

/// Parse state used while iterating through C-style escape sequences.
#[derive(Clone)]
enum UnquoteState {
    /// Position in the `inner` byte slice to process next.
    At(usize),
    /// Sentinel: iteration finished.
    Done,
}

/// Decode one "token" of a C-style quoted string and return the bytes it
/// contributes together with the next parser position.
///
/// Returns `None` when the iteration is exhausted.
fn unquote_step(inner: &[u8], state: &UnquoteState) -> Option<(UnquoteState, Vec<u8>)> {
    let i = match state {
        UnquoteState::Done => return None,
        UnquoteState::At(i) if *i >= inner.len() => return None,
        UnquoteState::At(i) => *i,
    };

    let b = inner[i];

    if b != b'\\' {
        return Some((UnquoteState::At(i + 1), vec![b]));
    }

    // Trailing backslash with nothing after it.
    if i + 1 >= inner.len() {
        return Some((UnquoteState::Done, vec![b'\\']));
    }

    let next = inner[i + 1];
    match next {
        b'\\' => Some((UnquoteState::At(i + 2), vec![b'\\'])),
        b'"' => Some((UnquoteState::At(i + 2), vec![b'"'])),
        b'n' | b't' | b'r' | b'b' | b'f' | b'v' => {
            Some((UnquoteState::At(i + 2), vec![b'\\', next]))
        }
        b'0'..=b'7' => {
            let start = i + 1;
            // Collect up to 3 octal digits.
            let consumed = (0..3)
                .take_while(|&k| {
                    start + k < inner.len() && (b'0'..=b'7').contains(&inner[start + k])
                })
                .count();
            if consumed == 0 {
                return Some((UnquoteState::At(i + 1), vec![b'\\']));
            }
            let octal_val = inner[start..start + consumed]
                .iter()
                .fold(0u32, |acc, &d| acc * 8 + u32::from(d - b'0'));
            let bytes: Vec<u8> = match u8::try_from(octal_val) {
                Ok(byte) if byte < 0x20 || byte == 0x7F => std::iter::once(b'\\')
                    .chain(inner[start..start + consumed].iter().copied())
                    .collect(),
                Ok(byte) => vec![byte],
                Err(_) => std::iter::once(b'\\')
                    .chain(inner[start..start + consumed].iter().copied())
                    .collect(),
            };
            Some((UnquoteState::At(start + consumed), bytes))
        }
        _ => Some((UnquoteState::At(i + 2), vec![b'\\', next])),
    }
}

fn unquote_c_style(s: &str) -> Option<String> {
    let bytes = s.as_bytes();
    if bytes.len() < 2 || bytes[0] != b'"' || bytes[bytes.len() - 1] != b'"' {
        return None;
    }
    let inner = &bytes[1..bytes.len() - 1];

    let result: Vec<u8> =
        std::iter::successors(Some((UnquoteState::At(0), vec![])), |(state, _)| {
            unquote_step(inner, state)
        })
        .skip(1) // skip the seed (empty vec)
        .flat_map(|(_, bytes)| bytes)
        .collect();

    String::from_utf8(result).ok()
}

pub(crate) fn parse_status_line(line: &str) -> Option<String> {
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

pub(crate) fn parse_path_component(raw: &str) -> String {
    let raw = raw.trim_end();
    unquote_c_style(raw).unwrap_or_else(|| raw.to_string())
}

/// Validate a path string for use in snapshot output.
///
/// Pure: inspects byte content of the path, performs no I/O.
pub(crate) fn validate_path_for_snapshot(path: &str) -> Result<(), super::types::GitError> {
    if path.bytes().any(|b| b < 0x20 || b == 0x7F) {
        return Err(super::types::GitError::ParseFailed {
            context: "control characters in path; cannot safely snapshot".to_string(),
        });
    }
    Ok(())
}

/// Format a git status entry in porcelain v1 style.
///
/// Pure: computes output string from status flags and path, performs no I/O.
pub(crate) fn format_status_porcelain(status: git2::Status, path: &str) -> String {
    if status.contains(git2::Status::WT_NEW) {
        return format!("?? {path}\n");
    }

    let index_status = compute_index_status(status);
    let wt_status = compute_wt_status(status);
    format!("{index_status}{wt_status} {path}\n")
}

/// Map a git2 `Status` to the porcelain index column character.
///
/// Pure: bit-flag match, no I/O.
pub(crate) fn compute_index_status(status: git2::Status) -> char {
    if status.contains(git2::Status::INDEX_NEW) {
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
    }
}

/// Map a git2 `Status` to the porcelain worktree column character.
///
/// Pure: bit-flag match, no I/O.
pub(crate) fn compute_wt_status(status: git2::Status) -> char {
    if status.contains(git2::Status::WT_MODIFIED) {
        'M'
    } else if status.contains(git2::Status::WT_DELETED) {
        'D'
    } else if status.contains(git2::Status::WT_RENAMED) {
        'R'
    } else if status.contains(git2::Status::WT_TYPECHANGE) {
        'T'
    } else {
        ' '
    }
}

#[cfg(test)]
mod parse_status_line_tests {
    use super::{parse_path_component, parse_status_line};

    // --- parse_status_line ---

    #[test]
    fn test_parse_status_line_returns_none_for_empty_string() {
        assert!(parse_status_line("").is_none());
    }

    #[test]
    fn test_parse_status_line_returns_none_for_line_shorter_than_4_bytes() {
        assert!(parse_status_line("AB ").is_none());
    }

    #[test]
    fn test_parse_status_line_returns_none_when_third_byte_is_not_space() {
        assert!(parse_status_line("ABXsrc/file.rs").is_none());
    }

    #[test]
    fn test_parse_status_line_returns_none_for_empty_path_spec() {
        assert!(parse_status_line("?? ").is_none());
    }

    #[test]
    fn test_parse_status_line_untracked() {
        let result = parse_status_line("?? new_file.txt");
        assert_eq!(result, Some("new_file.txt".to_string()));
    }

    #[test]
    fn test_parse_status_line_modified_in_worktree() {
        let result = parse_status_line(" M src/lib.rs");
        assert_eq!(result, Some("src/lib.rs".to_string()));
    }

    #[test]
    fn test_parse_status_line_added_to_index() {
        let result = parse_status_line("A  src/new.rs");
        assert_eq!(result, Some("src/new.rs".to_string()));
    }

    #[test]
    fn test_parse_status_line_deleted() {
        let result = parse_status_line(" D src/gone.rs");
        assert_eq!(result, Some("src/gone.rs".to_string()));
    }

    #[test]
    fn test_parse_status_line_rename_takes_new_path() {
        let result = parse_status_line("R  old/name.rs -> new/name.rs");
        assert_eq!(result, Some("new/name.rs".to_string()));
    }

    #[test]
    fn test_parse_status_line_copy_takes_new_path() {
        let result = parse_status_line("C  original.rs -> copy.rs");
        assert_eq!(result, Some("copy.rs".to_string()));
    }

    #[test]
    fn test_parse_status_line_rename_in_worktree_column() {
        let result = parse_status_line(" R old.rs -> new.rs");
        assert_eq!(result, Some("new.rs".to_string()));
    }

    #[test]
    fn test_parse_status_line_rename_no_arrow_uses_full_path_spec() {
        let result = parse_status_line("R  only-one-name.rs");
        assert_eq!(result, Some("only-one-name.rs".to_string()));
    }

    #[test]
    fn test_parse_status_line_quoted_path() {
        let result = parse_status_line("?? \"dir with spaces/file.rs\"");
        assert_eq!(result, Some("dir with spaces/file.rs".to_string()));
    }

    // --- parse_path_component ---

    #[test]
    fn test_parse_path_component_plain_path_returned_as_is() {
        assert_eq!(parse_path_component("src/lib.rs"), "src/lib.rs");
    }

    #[test]
    fn test_parse_path_component_quoted_path_unquoted() {
        assert_eq!(
            parse_path_component("\"dir with spaces/file.rs\""),
            "dir with spaces/file.rs"
        );
    }

    #[test]
    fn test_parse_path_component_trims_trailing_whitespace() {
        assert_eq!(parse_path_component("src/lib.rs   "), "src/lib.rs");
    }

    #[test]
    fn test_parse_path_component_empty_string_returns_empty() {
        assert_eq!(parse_path_component(""), "");
    }

    #[test]
    fn test_parse_path_component_single_quote_char_returned_as_is() {
        assert_eq!(parse_path_component("\""), "\"");
    }
}

#[cfg(test)]
mod proptest_parse_git_status_paths {
    use super::parse_git_status_paths;
    use proptest::prelude::*;

    proptest! {
        #[test]
        fn parse_git_status_paths_is_panic_free(input in ".*") {
            let result = parse_git_status_paths(&input);
            prop_assert!(result.windows(2).all(|win| win[0] <= win[1]));
        }
    }
}

#[cfg(test)]
mod porcelain_format_tests {
    use super::{compute_index_status, compute_wt_status, format_status_porcelain};
    use git2::Status;

    #[test]
    fn test_compute_index_status_new() {
        assert_eq!(compute_index_status(Status::INDEX_NEW), 'A');
    }

    #[test]
    fn test_compute_index_status_modified() {
        assert_eq!(compute_index_status(Status::INDEX_MODIFIED), 'M');
    }

    #[test]
    fn test_compute_index_status_deleted() {
        assert_eq!(compute_index_status(Status::INDEX_DELETED), 'D');
    }

    #[test]
    fn test_compute_index_status_renamed() {
        assert_eq!(compute_index_status(Status::INDEX_RENAMED), 'R');
    }

    #[test]
    fn test_compute_index_status_typechange() {
        assert_eq!(compute_index_status(Status::INDEX_TYPECHANGE), 'T');
    }

    #[test]
    fn test_compute_index_status_unmodified_returns_space() {
        assert_eq!(compute_index_status(Status::CURRENT), ' ');
    }

    #[test]
    fn test_compute_wt_status_modified() {
        assert_eq!(compute_wt_status(Status::WT_MODIFIED), 'M');
    }

    #[test]
    fn test_compute_wt_status_deleted() {
        assert_eq!(compute_wt_status(Status::WT_DELETED), 'D');
    }

    #[test]
    fn test_compute_wt_status_renamed() {
        assert_eq!(compute_wt_status(Status::WT_RENAMED), 'R');
    }

    #[test]
    fn test_compute_wt_status_typechange() {
        assert_eq!(compute_wt_status(Status::WT_TYPECHANGE), 'T');
    }

    #[test]
    fn test_compute_wt_status_unmodified_returns_space() {
        assert_eq!(compute_wt_status(Status::CURRENT), ' ');
    }

    #[test]
    fn test_format_status_porcelain_untracked_uses_question_marks() {
        let result = format_status_porcelain(Status::WT_NEW, "new_file.txt");
        assert_eq!(
            result,
            "?? new_file.txt
"
        );
    }

    #[test]
    fn test_format_status_porcelain_index_new() {
        let result = format_status_porcelain(Status::INDEX_NEW, "src/added.rs");
        assert_eq!(
            result,
            "A  src/added.rs
"
        );
    }

    #[test]
    fn test_format_status_porcelain_index_modified() {
        let result = format_status_porcelain(Status::INDEX_MODIFIED, "src/lib.rs");
        assert_eq!(
            result,
            "M  src/lib.rs
"
        );
    }

    #[test]
    fn test_format_status_porcelain_wt_modified() {
        let result = format_status_porcelain(Status::WT_MODIFIED, "src/lib.rs");
        assert_eq!(
            result,
            " M src/lib.rs
"
        );
    }

    #[test]
    fn test_format_status_porcelain_index_deleted() {
        let result = format_status_porcelain(Status::INDEX_DELETED, "src/gone.rs");
        assert_eq!(
            result,
            "D  src/gone.rs
"
        );
    }

    #[test]
    fn test_format_status_porcelain_wt_deleted() {
        let result = format_status_porcelain(Status::WT_DELETED, "src/gone.rs");
        assert_eq!(
            result,
            " D src/gone.rs
"
        );
    }

    #[test]
    fn test_format_status_porcelain_combined_index_and_wt_modified() {
        let result =
            format_status_porcelain(Status::INDEX_MODIFIED | Status::WT_MODIFIED, "src/both.rs");
        assert_eq!(
            result,
            "MM src/both.rs
"
        );
    }

    #[test]
    fn test_format_status_porcelain_current_status_produces_space_space() {
        let result = format_status_porcelain(Status::CURRENT, "src/untouched.rs");
        assert_eq!(
            result,
            "   src/untouched.rs
"
        );
    }
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

#[cfg(test)]
mod typed_error_tests {
    use super::validate_path_for_snapshot;
    use crate::git_helpers::domain::types::GitError;

    #[test]
    fn test_validate_path_for_snapshot_returns_parse_failed_on_control_char() {
        let err = validate_path_for_snapshot("x\nfile.rs").unwrap_err();
        assert!(
            matches!(err, GitError::ParseFailed { .. }),
            "expected ParseFailed, got {err:?}"
        );
    }

    #[test]
    fn test_validate_path_for_snapshot_includes_control_char_info_in_context() {
        let err = validate_path_for_snapshot("bad\x1Bpath").unwrap_err();
        match err {
            GitError::ParseFailed { context } => {
                assert!(
                    context.contains("control"),
                    "expected context to mention 'control', got {context:?}"
                );
            }
            other => panic!("expected ParseFailed, got {other:?}"),
        }
    }

    #[test]
    fn test_validate_path_for_snapshot_returns_ok_for_normal_path() {
        assert!(validate_path_for_snapshot("src/lib.rs").is_ok());
    }

    #[test]
    fn test_validate_path_for_snapshot_returns_ok_for_path_with_spaces() {
        assert!(validate_path_for_snapshot("my dir/file.rs").is_ok());
    }
}
