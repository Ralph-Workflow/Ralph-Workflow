//! Git local exclude management.
//!
//! Manages `.git/info/exclude` to suppress agent-generated files from
//! appearing in `git status` output without modifying shared `.gitignore`.
//!
//! # Safety
//!
//! Only paths that begin with approved prefixes are added to the exclude file.
//! This prevents accidental suppression of user-owned files.

use std::io;
use std::path::Path;

/// Approved path prefixes that may be added to `.git/info/exclude`.
///
/// Only agent-internal artifact directories are permitted. User-owned files are
/// never written to the local exclude to avoid masking uncommitted work.
const APPROVED_PREFIXES: &[&str] = &[".agent/tmp/", ".agent/logs-"];

fn is_safe_exclude_pattern(pattern: &str) -> bool {
    // `.git/info/exclude` uses gitignore syntax, where newlines create additional rules.
    // Reject control characters and glob metacharacters to avoid injection and overly-broad rules.
    if pattern.chars().any(char::is_control) {
        return false;
    }
    if pattern.contains('\\') {
        return false;
    }
    if pattern.contains(['*', '?', '[', ']', '{', '}']) {
        return false;
    }

    let path = Path::new(pattern);
    if path.is_absolute() {
        return false;
    }
    for component in path.components() {
        match component {
            std::path::Component::CurDir
            | std::path::Component::ParentDir
            | std::path::Component::RootDir
            | std::path::Component::Prefix(_) => return false,
            std::path::Component::Normal(_) => {}
        }
    }

    true
}

/// Add `patterns` to `.git/info/exclude` that are not already present.
///
/// Only patterns whose path begins with an approved prefix are accepted;
/// all others are silently skipped. The file is created (including the
/// parent directory) if it does not exist.
///
/// # Errors
///
/// Returns an `io::Error` if reading or writing `.git/info/exclude` fails.
pub fn ensure_local_excludes(repo_root: &Path, patterns: &[&str]) -> io::Result<()> {
    // Filter to approved patterns only.
    let approved: Vec<&str> = patterns
        .iter()
        .copied()
        .filter(|p| APPROVED_PREFIXES.iter().any(|prefix| p.starts_with(prefix)))
        .filter(|p| is_safe_exclude_pattern(p))
        .collect();

    if approved.is_empty() {
        return Ok(());
    }

    let git_dir = repo_root.join(".git");
    let info_dir = git_dir.join("info");
    let exclude_path = info_dir.join("exclude");

    // Ensure `.git/info/` directory exists.
    std::fs::create_dir_all(&info_dir)?;

    // Read existing content (empty string if file doesn't exist).
    let existing = if exclude_path.exists() {
        std::fs::read_to_string(&exclude_path)?
    } else {
        String::new()
    };

    // Collect lines already present to avoid duplicates.
    let existing_lines: std::collections::HashSet<&str> = existing.lines().collect();

    let mut additions = String::new();
    for pattern in approved {
        if !existing_lines.contains(pattern) {
            additions.push_str(pattern);
            additions.push('\n');
        }
    }

    if additions.is_empty() {
        return Ok(());
    }

    // Append new patterns, ensuring the file ends with a newline before them.
    let mut content = existing;
    if !content.is_empty() && !content.ends_with('\n') {
        content.push('\n');
    }
    content.push_str(&additions);

    std::fs::write(&exclude_path, content)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn setup_fake_git_repo() -> TempDir {
        let dir = tempfile::tempdir().expect("tempdir");
        fs::create_dir_all(dir.path().join(".git/info")).expect("create .git/info");
        dir
    }

    #[test]
    fn test_does_not_add_broad_agent_prefix_pattern() {
        // Safety regression test: catch-all `.agent/` patterns are too broad and can
        // mask important agent state from `git status`.
        let repo = setup_fake_git_repo();
        let root = repo.path();
        let exclude = root.join(".git/info/exclude");

        ensure_local_excludes(root, &[".agent/"]).unwrap();

        if exclude.exists() {
            let content = fs::read_to_string(&exclude).unwrap();
            assert!(
                !content.lines().any(|l| l.trim() == ".agent/"),
                "Broad `.agent/` pattern must not be added: {content}"
            );
        }
    }

    #[test]
    fn test_adds_approved_pattern_to_new_file() {
        let repo = setup_fake_git_repo();
        let root = repo.path();
        let exclude = root.join(".git/info/exclude");

        ensure_local_excludes(root, &[".agent/tmp/commit_message.xml"]).unwrap();

        let content = fs::read_to_string(&exclude).unwrap();
        assert!(
            content.contains(".agent/tmp/commit_message.xml"),
            "Pattern should be written: {content}"
        );
    }

    #[test]
    fn test_does_not_add_unapproved_pattern() {
        let repo = setup_fake_git_repo();
        let root = repo.path();
        let exclude = root.join(".git/info/exclude");

        ensure_local_excludes(root, &["src/user_file.rs"]).unwrap();

        // File should not be created (or be empty if it was).
        if exclude.exists() {
            let content = fs::read_to_string(&exclude).unwrap();
            assert!(
                !content.contains("src/user_file.rs"),
                "Unapproved pattern must not be added: {content}"
            );
        }
    }

    #[test]
    fn test_does_not_duplicate_existing_pattern() {
        let repo = setup_fake_git_repo();
        let root = repo.path();
        let exclude = root.join(".git/info/exclude");

        // Write once.
        ensure_local_excludes(root, &[".agent/tmp/commit_message.xml"]).unwrap();
        // Write again.
        ensure_local_excludes(root, &[".agent/tmp/commit_message.xml"]).unwrap();

        let content = fs::read_to_string(&exclude).unwrap();
        let count = content
            .lines()
            .filter(|l| *l == ".agent/tmp/commit_message.xml")
            .count();
        assert_eq!(count, 1, "Pattern must appear exactly once: {content}");
    }

    #[test]
    fn test_appends_to_existing_file() {
        let repo = setup_fake_git_repo();
        let root = repo.path();
        let exclude = root.join(".git/info/exclude");

        // Pre-populate with one entry.
        fs::write(&exclude, "# git exclude file\n.agent/logs-old/\n").unwrap();

        ensure_local_excludes(root, &[".agent/tmp/debug.log"]).unwrap();

        let content = fs::read_to_string(&exclude).unwrap();
        assert!(
            content.contains("# git exclude file"),
            "Existing content preserved"
        );
        assert!(
            content.contains(".agent/logs-old/"),
            "Existing entry preserved"
        );
        assert!(content.contains(".agent/tmp/debug.log"), "New entry added");
    }

    #[test]
    fn test_creates_info_dir_if_missing() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path();
        // Create .git but NOT .git/info
        fs::create_dir_all(root.join(".git")).expect("create .git");

        ensure_local_excludes(root, &[".agent/tmp/test.xml"]).unwrap();

        let exclude = root.join(".git/info/exclude");
        assert!(exclude.exists(), ".git/info/exclude should be created");
        let content = fs::read_to_string(&exclude).unwrap();
        assert!(content.contains(".agent/tmp/test.xml"));
    }

    #[test]
    fn test_empty_patterns_is_noop() {
        let repo = setup_fake_git_repo();
        let root = repo.path();
        let exclude = root.join(".git/info/exclude");

        ensure_local_excludes(root, &[]).unwrap();

        // File should not be created.
        assert!(
            !exclude.exists(),
            "No file should be created for empty input"
        );
    }

    #[test]
    fn test_mixed_approved_and_unapproved_only_adds_approved() {
        let repo = setup_fake_git_repo();
        let root = repo.path();
        let exclude = root.join(".git/info/exclude");

        ensure_local_excludes(
            root,
            &[
                ".agent/tmp/output.xml",
                "src/sensitive.rs",
                ".agent/logs-abc/trace.log",
            ],
        )
        .unwrap();

        let content = fs::read_to_string(&exclude).unwrap();
        assert!(content.contains(".agent/tmp/output.xml"), "Approved added");
        assert!(
            content.contains(".agent/logs-abc/trace.log"),
            "Approved added"
        );
        assert!(
            !content.contains("src/sensitive.rs"),
            "Unapproved not added"
        );
    }

    #[test]
    fn test_rejects_patterns_with_newlines_to_prevent_injection() {
        let repo = setup_fake_git_repo();
        let root = repo.path();
        let exclude = root.join(".git/info/exclude");

        ensure_local_excludes(root, &[".agent/tmp/ok.log", ".agent/tmp/x\nsrc/"]).unwrap();

        let content = fs::read_to_string(&exclude).unwrap();
        assert!(content.contains(".agent/tmp/ok.log"), "Valid pattern added");
        assert!(
            !content.lines().any(|l| l.trim() == "src/"),
            "Newline injection must not create extra ignore rules: {content}"
        );
    }
}
