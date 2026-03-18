//! Runtime module for interrupt - contains OS-boundary code.
//!
//! This module satisfies the dylint boundary-module check for code that uses
//! std::fs for cleanup operations during signal handling, std::process::exit
//! for termination, and interior mutability for global interrupt state.

use std::path::Path;
use std::sync::OnceLock;

use super::checkpoint::InterruptContext;

/// Global interrupt context for checkpoint saving on interrupt.
///
/// This is set during pipeline initialization and used by the interrupt
/// handler to save a checkpoint when the user presses Ctrl+C.
pub(crate) static INTERRUPT_CONTEXT: OnceLock<Option<InterruptContext>> = OnceLock::new();

/// Set the global interrupt context.
pub fn set_interrupt_context(context: InterruptContext) {
    let _ = INTERRUPT_CONTEXT.set(Some(context));
}

/// Clear the global interrupt context.
pub fn clear_interrupt_context() {
    let _ = INTERRUPT_CONTEXT.set(None);
}

/// Get the global interrupt context.
pub fn get_interrupt_context() -> Option<Option<InterruptContext>> {
    INTERRUPT_CONTEXT.get().cloned()
}

/// Exit the process with the standard SIGINT exit code.
///
/// This is called from the signal handler when immediate termination is required.
#[expect(
    clippy::exit,
    reason = "Signal handler requires immediate process termination"
)]
pub fn exit_sigint() -> ! {
    std::process::exit(130)
}

/// Restore prompt.md to writable mode using std::fs.
///
/// This is called from the signal handler to ensure the prompt file
/// is not left read-only if the process is interrupted.
#[cfg(unix)]
pub fn restore_prompt_md_writable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;

    fn make_writable(path: &Path) -> bool {
        let Ok(metadata) = std::fs::metadata(path) else {
            return false;
        };

        let mut perms = metadata.permissions();
        perms.set_mode(perms.mode() | 0o200);
        std::fs::set_permissions(path, perms).is_ok()
    }

    make_writable(path)
}

#[cfg(unix)]
pub fn restore_prompt_md_writable_in_repo(repo_root: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;

    fn make_writable(path: &Path) -> bool {
        let Ok(metadata) = std::fs::metadata(path) else {
            return false;
        };

        let mut perms = metadata.permissions();
        perms.set_mode(perms.mode() | 0o200);
        std::fs::set_permissions(path, perms).is_ok()
    }

    let prompt_path = repo_root.join("PROMPT.md");
    make_writable(&prompt_path)
}

#[cfg(not(unix))]
pub fn restore_prompt_md_writable(_path: &Path) -> bool {
    false
}

#[cfg(not(unix))]
pub fn restore_prompt_md_writable_in_repo(_repo_root: &Path) -> bool {
    false
}

/// Remove the .git/ralph directory using std::fs.
pub fn remove_ralph_dir(repo_root: &Path) {
    let _ = std::fs::remove_dir_all(repo_root.join(".git/ralph"));
}
