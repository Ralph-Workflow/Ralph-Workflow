/// Get the current working directory.
///
/// This is in a boundary module because it accesses the process environment.
#[must_use]
pub fn get_current_dir() -> Option<String> {
    std::env::current_dir()
        .ok()
        .map(|p| p.to_string_lossy().to_string())
}
