// file_based_extraction/io.rs — boundary module for environment access.
// Exempt from forbid_io_effects because the file stem is `io`.

// Resolve a relative path to absolute using the current working directory.
// Falls back to the original relative path if current_dir() fails.
fn resolve_with_current_dir(relative_path: &str) -> String {
    std::env::current_dir().ok().map_or_else(
        || relative_path.to_string(),
        |cwd| cwd.join(relative_path).display().to_string(),
    )
}
