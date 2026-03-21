// File path extraction implementation.
// All imports for the module are defined here.

use regex::Regex;
use std::collections::BTreeSet;

/// Pattern 1: Bracketed format with optional line numbers.
/// Matches: [src/main.rs:42], [src/lib.rs], [path/to/file.rs:100]
fn bracket_pattern() -> Regex {
    Regex::new(r"\[([^\]]+?\.[a-z]+(?::\d+)?)\]")
        .expect("BRACKET_PATTERN: invalid regex - this is a developer error")
}

/// Pattern 2: Parenthesized format.
/// Matches: (src/main.rs), (path/to/file.rs)
fn paren_pattern() -> Regex {
    Regex::new(r"\(([^\)]+?\.[a-z]+)\)")
        .expect("PAREN_PATTERN: invalid regex - this is a developer error")
}

/// Pattern 3: Backtick format (used by some agents like Codex).
/// Matches: `src/main.rs:42`, `path/to/file.rs`
fn backtick_pattern() -> Regex {
    Regex::new(r"`([^`]+?\.[a-z]+(?::\d+)?)`")
        .expect("BACKTICK_PATTERN: invalid regex - this is a developer error")
}

/// Pattern 4: Bare colon format (file.rs:line).
/// Matches: src/main.rs:42, lib.rs:123 (but not URLs or similar)
fn bare_pattern() -> Regex {
    Regex::new(r"\b([\w/-]+?\.[a-z]+:\d+)\b")
        .expect("BARE_PATTERN: invalid regex - this is a developer error")
}

/// Extract file paths from ISSUES markdown content.
///
/// This function parses common issue citation formats to find file references:
/// - Bracketed with line numbers: `[src/main.rs:42]`
/// - Bracketed without line numbers: `[src/lib.rs]`
/// - Parenthesized: `(src/utils.rs)`
/// - Bare colon format: `src/helpers.rs:123`
/// - Backtick format: `` `src/file.rs:42` ``
///
/// File paths are deduplicated and sorted alphabetically for consistency.
///
/// # Arguments
///
/// * `content` - The ISSUES markdown content to parse
///
/// # Returns
///
/// A sorted vector of unique file paths found in the content.
///
/// # Examples
///
/// ```
/// use ralph_workflow::files::result_extraction::extract_file_paths_from_issues;
///
/// let issues = r#"
/// # Issues
///
/// Critical:
/// - [ ] [src/main.rs:42] Bug in main function
/// - [ ] High: [src/lib.rs:10] Style issue
///
/// Medium:
/// - [ ] (src/utils.rs) Missing documentation
/// "#;
///
/// let files = extract_file_paths_from_issues(issues);
/// assert_eq!(files, vec!["src/lib.rs", "src/main.rs", "src/utils.rs"]);
/// ```
pub fn extract_file_paths_from_issues(content: &str) -> Vec<String> {
    let bracket_pattern = bracket_pattern();
    let paren_pattern = paren_pattern();
    let backtick_pattern = backtick_pattern();
    let bare_pattern = bare_pattern();
    let mut files = BTreeSet::new();

    // Extract from bracketed format
    for caps in bracket_pattern.captures_iter(content) {
        if let Some(file_ref) = caps.get(1) {
            let path = file_ref.as_str().trim();
            // Remove line number if present
            let file_path = path.split(':').next().unwrap_or(path);
            if looks_like_file_path(file_path) {
                files.insert(file_path.to_string());
            }
        }
    }

    // Extract from parenthesized format
    for caps in paren_pattern.captures_iter(content) {
        if let Some(file_ref) = caps.get(1) {
            let path = file_ref.as_str().trim();
            if looks_like_file_path(path) {
                files.insert(path.to_string());
            }
        }
    }

    // Extract from backtick format
    for caps in backtick_pattern.captures_iter(content) {
        if let Some(file_ref) = caps.get(1) {
            let path = file_ref.as_str().trim();
            // Remove line number if present
            let file_path = path.split(':').next().unwrap_or(path);
            if looks_like_file_path(file_path) {
                files.insert(file_path.to_string());
            }
        }
    }

    // Extract from bare colon format
    for caps in bare_pattern.captures_iter(content) {
        if let Some(file_ref) = caps.get(1) {
            let path = file_ref.as_str().trim();
            // Remove line number
            let file_path = path.split(':').next().unwrap_or(path);
            // Avoid duplicates of already-found bracketed/parenthesized files
            if looks_like_file_path(file_path) && !files.contains(file_path) {
                files.insert(file_path.to_string());
            }
        }
    }

    files.into_iter().collect()
}

/// Check if a string looks like a source file path.
///
/// This is a conservative check to avoid false positives from URLs,
/// issue numbers, or other colon-separated values.
///
/// # Arguments
///
/// * `s` - The string to check
///
/// # Returns
///
/// `true` if the string appears to be a file path, `false` otherwise.
fn looks_like_file_path(s: &str) -> bool {
    // Must have a file extension
    if !s.contains('.') {
        return false;
    }

    // Avoid common non-file patterns
    // Check for things that look like URLs or domains
    if s.contains("://") || s.contains("www.") || s.starts_with("http") {
        return false;
    }

    // Avoid short patterns that are likely not file paths
    if s.len() < 4 {
        return false;
    }

    // Must have a recognized file extension (common source file types)
    let extensions = [
        "rs", "toml", "md", "txt", "json", "yaml", "yml", "xml", "html", "css", "js", "ts", "py",
        "go", "java", "c", "cpp", "h", "hpp", "cs", "rb", "php", "sh", "bash", "zsh",
    ];
    let has_known_extension = s.split('.').any(|ext| {
        // Remove any line number suffix from the extension check
        let ext = ext.split(':').next().unwrap_or(ext);
        extensions.contains(&ext)
    });

    has_known_extension
}
