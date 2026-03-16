//! Runtime module for interrupt - contains OS-boundary code.
//!
//! This module satisfies the dylint boundary-module check for code that uses
//! std::fs for cleanup operations during signal handling.

/// Restore prompt.md to writable mode using std::fs.
///
/// This is called from the signal handler to ensure the prompt file
/// is not left read-only if the process is interrupted.
#[cfg(unix)]
pub fn restore_prompt_md_writable(path: &std::path::Path) -> bool {
    use std::os::unix::fs::PermissionsExt;

    fn make_writable(path: &std::path::Path) -> bool {
        let Ok(metadata) = std::fs::metadata(path) else {
            return false;
        };

        let mut perms = metadata.permissions();
        // Preserve existing mode bits but ensure owner write is enabled.
        perms.set_mode(perms.mode() | 0o200);
        std::fs::set_permissions(path, perms).is_ok()
    }

    make_writable(path)
}

#[cfg(unix)]
pub fn restore_prompt_md_writable_in_repo(repo_root: &std::path::Path) -> bool {
    use std::os::unix::fs::PermissionsExt;

    fn make_writable(path: &std::path::Path) -> bool {
        let Ok(metadata) = std::fs::metadata(path) else {
            return false;
        };

        let mut perms = metadata.permissions();
        // Preserve existing mode bits but ensure owner write is enabled.
        perms.set_mode(perms.mode() | 0o200);
        std::fs::set_permissions(path, perms).is_ok()
    }

    let prompt_path = repo_root.join("PROMPT.md");
    make_writable(&prompt_path)
}

#[cfg(not(unix))]
pub fn restore_prompt_md_writable(_path: &std::path::Path) -> bool {
    false
}

#[cfg(not(unix))]
pub fn restore_prompt_md_writable_in_repo(_repo_root: &std::path::Path) -> bool {
    false
}

/// Remove the .git/ralph directory using std::fs.
pub fn remove_ralph_dir(repo_root: &std::path::Path) {
    let _ = std::fs::remove_dir_all(repo_root.join(".git/ralph"));
}
