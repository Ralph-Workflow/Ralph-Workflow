// git_helpers/rebase/io.rs — boundary module for rebase operations.

/// Get the current working directory (boundary function for environment access).
pub(super) fn get_current_dir() -> std::io::Result<std::path::PathBuf> {
    std::env::current_dir()
}
