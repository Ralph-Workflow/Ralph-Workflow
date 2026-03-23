use std::path::{Component, Path};

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(crate) struct IssueSnippetRequest {
    pub file: String,
    pub start: u32,
    pub end: u32,
}

pub(crate) fn collect_issue_snippet_requests(
    _issues: &[String],
    _workspace_root: &Path,
) -> Vec<IssueSnippetRequest> {
    let location_re = super::boundary_domain::issue_location_regex();
    let gh_location_re = super::boundary_domain::issue_gh_location_regex();

    let requests: std::collections::HashSet<IssueSnippetRequest> = _issues
        .iter()
        .filter_map(|issue| {
            let capture = location_re
                .captures(issue)
                .or_else(|| gh_location_re.captures(issue))?;
            let file = capture.name("file")?.as_str().trim().replace('\\', "/");
            let file = normalize_issue_file_path_to_workspace_relative(&file, _workspace_root)?;
            let start = capture.name("start")?.as_str().parse::<u32>().ok()?;
            let end = capture
                .name("end")
                .and_then(|m| m.as_str().parse::<u32>().ok())
                .unwrap_or(start);
            Some(IssueSnippetRequest { file, start, end })
        })
        .collect();

    requests.into_iter().collect()
}

pub(crate) fn normalize_issue_file_path_to_workspace_relative(
    _file: &str,
    _workspace_root: &Path,
) -> Option<String> {
    let trimmed = _file.trim();
    if trimmed.is_empty() || trimmed.starts_with("//") {
        return None;
    }

    let normalized = trimmed.replace('\\', "/");

    if is_safe_workspace_relative_path(&normalized) {
        return Some(normalized);
    }

    let path = Path::new(&normalized);

    if path.is_absolute() {
        let stripped = path.strip_prefix(_workspace_root).ok()?;
        let candidate = stripped.to_string_lossy().replace('\\', "/");
        return is_safe_workspace_relative_path(&candidate).then_some(candidate);
    }

    let bytes = normalized.as_bytes();
    if bytes.len() >= 2 && bytes[1] == b':' {
        let first = bytes[0] as char;
        if first.is_ascii_alphabetic() {
            let remainder = normalized[2..].trim_start_matches('/');
            let base = _workspace_root.file_name()?.to_str()?;
            let remainder = remainder.strip_prefix(base)?;
            let remainder = remainder.trim_start_matches('/');
            if remainder.is_empty() {
                return None;
            }

            let candidate = remainder.to_string();
            return is_safe_workspace_relative_path(&candidate).then_some(candidate);
        }
    }

    None
}

pub(crate) fn is_safe_workspace_relative_path(_path_str: &str) -> bool {
    let trimmed = _path_str.trim();
    if trimmed.is_empty() {
        return false;
    }

    let bytes = trimmed.as_bytes();
    if bytes.len() >= 2 && bytes[1] == b':' {
        let first = bytes[0] as char;
        if first.is_ascii_alphabetic() {
            return false;
        }
    }

    if trimmed.starts_with("//") {
        return false;
    }

    let path = Path::new(trimmed);
    if path.is_absolute() {
        return false;
    }

    !path.components().any(|component| {
        matches!(
            component,
            Component::ParentDir | Component::RootDir | Component::Prefix(_)
        )
    })
}

pub(crate) fn extract_snippet_lines(_content: &str, _start: u32, _end: u32) -> Option<String> {
    if _start < 1 || _end < 1 || _end < _start {
        return None;
    }

    let lines: Vec<&str> = _content.lines().collect();
    if lines.is_empty() {
        return None;
    }

    let start_idx = _start.saturating_sub(1) as usize;
    if start_idx >= lines.len() {
        return None;
    }

    let end_idx = (_end.saturating_sub(1) as usize).min(lines.len().saturating_sub(1));

    let snippet = lines[start_idx..=end_idx]
        .iter()
        .enumerate()
        .map(|(offset, line)| {
            let line_no = u32::try_from(offset)
                .ok()
                .map(|offset| _start + offset)
                .unwrap_or(0);
            format!("{line_no} | {line}")
        })
        .collect::<Vec<_>>()
        .join("\n");

    Some(snippet)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn collect_issue_snippet_requests_parses_standard_and_github_locations() {
        let root = Path::new("/repo");
        let issues = vec![
            "src/lib.rs:10-12 problem".to_string(),
            "src/main.rs#L4-L5 something".to_string(),
        ];

        let requests = collect_issue_snippet_requests(&issues, root);
        assert_eq!(requests.len(), 2);
        assert!(requests
            .iter()
            .any(|r| { r.file == "src/lib.rs" && r.start == 10 && r.end == 12 }));
        assert!(requests
            .iter()
            .any(|r| { r.file == "src/main.rs" && r.start == 4 && r.end == 5 }));
    }

    #[test]
    fn normalize_issue_file_path_to_workspace_relative_handles_absolute_path() {
        let root = Path::new("/repo");
        let normalized = normalize_issue_file_path_to_workspace_relative("/repo/src/lib.rs", root);
        assert_eq!(normalized.as_deref(), Some("src/lib.rs"));
    }

    #[test]
    fn normalize_issue_file_path_to_workspace_relative_rejects_parent_dir_escape() {
        let root = Path::new("/repo");
        assert!(normalize_issue_file_path_to_workspace_relative("../secret", root).is_none());
    }

    #[test]
    fn is_safe_workspace_relative_path_rejects_windows_drive_path() {
        assert!(!is_safe_workspace_relative_path("C:/repo/src/lib.rs"));
    }

    #[test]
    fn extract_snippet_lines_formats_expected_line_numbers() {
        let content = "one\ntwo\nthree\n";
        let snippet = extract_snippet_lines(content, 2, 3).expect("snippet");
        assert_eq!(snippet, "2 | two\n3 | three");
    }
}
