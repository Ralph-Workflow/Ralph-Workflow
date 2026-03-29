// git_helpers/branch/io.rs — boundary module for environment access in branch module.

/// Get the current working directory (boundary function for environment access).
pub(super) fn get_current_dir() -> std::io::Result<std::path::PathBuf> {
    std::env::current_dir()
}
