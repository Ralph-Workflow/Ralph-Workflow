// git_helpers/repo/exclude/io.rs — boundary module for git local exclude management.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Git local exclude management.
//
// Manages `.git/info/exclude` to suppress agent-generated files from
// appearing in `git status` output without modifying shared `.gitignore`.
//
// # Safety
//
// Only paths that begin with approved prefixes are added to the exclude file.
// This prevents accidental suppression of user-owned files.

use std::path::Path;

use crate::git_helpers::git2_to_io_error;

mod io {
    pub type Result<T> = std::io::Result<T>;
}

/// Approved path prefixes that may be added to `.git/info/exclude`.
///
/// Only agent-internal artifact directories are permitted. User-owned files are
/// never written to the local exclude to avoid masking uncommitted work.
const APPROVED_PREFIXES: &[&str] = &[".agent/tmp/", ".agent/logs-"];

fn has_forbidden_chars(pattern: &str) -> bool {
    pattern.chars().any(char::is_control)
        || pattern.contains('\\')
        || pattern.contains(['*', '?', '[', ']', '{', '}'])
}

fn has_invalid_path_component(path: &Path) -> bool {
    path.components().any(|component| {
        matches!(
            component,
            std::path::Component::CurDir
                | std::path::Component::ParentDir
                | std::path::Component::RootDir
                | std::path::Component::Prefix(_)
        )
    })
}

fn is_safe_exclude_pattern(pattern: &str) -> bool {
    // `.git/info/exclude` uses gitignore syntax, where newlines create additional rules.
    // Reject control characters and glob metacharacters to avoid injection and overly-broad rules.
    if has_forbidden_chars(pattern) {
        return false;
    }
    let path = Path::new(pattern);
    if path.is_absolute() {
        return false;
    }
    !has_invalid_path_component(path)
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
fn filter_approved_patterns<'a>(patterns: &[&'a str]) -> Vec<&'a str> {
    patterns
        .iter()
        .copied()
        .filter(|p| APPROVED_PREFIXES.iter().any(|prefix| p.starts_with(prefix)))
        .filter(|p| is_safe_exclude_pattern(p))
        .collect()
}

fn compute_additions(approved: &[&str], existing: &str) -> String {
    let existing_lines: std::collections::HashSet<&str> = existing.lines().collect();
    approved
        .iter()
        .filter(|pattern| !existing_lines.contains(*pattern))
        .map(|pattern| format!("{pattern}\n"))
        .collect()
}

fn append_to_exclude_file(
    exclude_path: &Path,
    existing: &str,
    additions: &str,
) -> io::Result<()> {
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(exclude_path)?;
    if !existing.is_empty() && !existing.ends_with('\n') {
        std::io::Write::write_all(&mut file, b"\n")?;
    }
    std::io::Write::write_all(&mut file, additions.as_bytes())
}

fn resolve_exclude_path(repo_root: &Path) -> io::Result<(std::path::PathBuf, std::path::PathBuf)> {
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    let git_dir = repo.path().to_path_buf();
    let info_dir = git_dir.join("info");
    let exclude_path = info_dir.join("exclude");
    Ok((info_dir, exclude_path))
}

fn read_existing_exclude(exclude_path: &Path) -> io::Result<String> {
    if exclude_path.exists() { std::fs::read_to_string(exclude_path) } else { Ok(String::new()) }
}

fn append_new_patterns(
    approved: &[&str],
    exclude_path: &std::path::Path,
) -> io::Result<()> {
    let existing = read_existing_exclude(exclude_path)?;
    let additions = compute_additions(approved, &existing);
    if additions.is_empty() {
        Ok(())
    } else {
        append_to_exclude_file(exclude_path, &existing, &additions)
    }
}

pub fn ensure_local_excludes(repo_root: &Path, patterns: &[&str]) -> io::Result<()> {
    let approved = filter_approved_patterns(patterns);
    if approved.is_empty() {
        return Ok(());
    }
    // Callers of this helper pass a repository root; do not walk upward and risk mutating a
    // parent repository's `.git/info/exclude`.
    let (info_dir, exclude_path) = resolve_exclude_path(repo_root)?;
    std::fs::create_dir_all(&info_dir)?;
    append_new_patterns(&approved, &exclude_path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn setup_git_repo() -> TempDir {
        let dir = tempfile::tempdir().expect("tempdir");
        let _repo = git2::Repository::init(dir.path()).expect("init repo");
        dir
    }

    #[test]
    fn test_resolves_gitdir_when_dot_git_is_file() {
        // Worktrees and some git setups use a `.git` *file* that points at the real gitdir.
        // ensure_local_excludes must resolve the actual gitdir, not assume `.git/` is a directory.
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path();

        // Initialize a real repo so libgit2 can discover it.
        let _repo = git2::Repository::init(root).expect("init repo");

        // Move `.git/` aside and replace it with a `.git` file pointing to the real dir.
        let real_gitdir = root.join(".git-real");
        fs::rename(root.join(".git"), &real_gitdir).expect("move gitdir");
        fs::write(root.join(".git"), "gitdir: .git-real\n").expect("write .git file");

        ensure_local_excludes(root, &[".agent/tmp/test.xml"]).unwrap();

        let resolved_exclude = real_gitdir.join("info").join("exclude");
        assert!(
            resolved_exclude.exists(),
            "exclude must be written in real gitdir"
        );
        let content = fs::read_to_string(&resolved_exclude).unwrap();
        assert!(content.contains(".agent/tmp/test.xml"));

        // The synthetic `.git` file must not be treated as a directory.
        assert!(!root.join(".git").join("info").join("exclude").exists());
    }

    #[test]
    fn test_does_not_add_broad_agent_prefix_pattern() {
        // Safety regression test: catch-all `.agent/` patterns are too broad and can
        // mask important agent state from `git status`.
        let repo = setup_git_repo();
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
        let repo = setup_git_repo();
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
        let repo = setup_git_repo();
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
        let repo = setup_git_repo();
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
        let repo = setup_git_repo();
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
        let dir = setup_git_repo();
        let root = dir.path();
        // Remove `.git/info` to simulate missing directory.
        let info_dir = root.join(".git").join("info");
        if info_dir.exists() {
            fs::remove_dir_all(&info_dir).expect("remove .git/info");
        }

        ensure_local_excludes(root, &[".agent/tmp/test.xml"]).unwrap();

        let exclude = root.join(".git/info/exclude");
        assert!(exclude.exists(), ".git/info/exclude should be created");
        let content = fs::read_to_string(&exclude).unwrap();
        assert!(content.contains(".agent/tmp/test.xml"));
    }

    #[test]
    fn test_empty_patterns_is_noop() {
        let repo = setup_git_repo();
        let root = repo.path();
        let exclude = root.join(".git/info/exclude");

        let before = if exclude.exists() {
            fs::read_to_string(&exclude).unwrap()
        } else {
            String::new()
        };

        ensure_local_excludes(root, &[]).unwrap();

        let after = if exclude.exists() {
            fs::read_to_string(&exclude).unwrap()
        } else {
            String::new()
        };
        assert_eq!(after, before, "empty input must not mutate exclude file");
    }

    #[test]
    fn test_mixed_approved_and_unapproved_only_adds_approved() {
        let repo = setup_git_repo();
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
        let repo = setup_git_repo();
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

    #[test]
    fn test_does_not_discover_parent_repo_when_repo_root_is_not_repo_root() {
        // Safety regression: ensure_local_excludes must not walk upward and mutate a parent
        // repository when callers pass an incorrect repo_root.
        let dir = tempfile::tempdir().expect("tempdir");
        let parent_root = dir.path();
        let _repo = git2::Repository::init(parent_root).expect("init parent repo");

        let child = parent_root.join("child");
        fs::create_dir_all(&child).expect("create child dir");

        let exclude = parent_root.join(".git/info/exclude");
        let before = if exclude.exists() {
            fs::read_to_string(&exclude).unwrap()
        } else {
            String::new()
        };

        let err = ensure_local_excludes(&child, &[".agent/tmp/test.xml"]).err();
        assert!(
            err.is_some(),
            "expected error when repo_root is not repo root"
        );

        let after = if exclude.exists() {
            fs::read_to_string(&exclude).unwrap()
        } else {
            String::new()
        };
        assert_eq!(after, before, "parent repo exclude must not be modified");
    }
}
