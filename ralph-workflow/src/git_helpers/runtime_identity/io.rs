// git_helpers/runtime_identity/io.rs — boundary module for runtime identity environment variable access.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Runtime module for identity - environment variable access.
//
// This module satisfies the dylint boundary-module check for code that reads
// environment variables for system identity information.

/// Get the system username from environment variables.
///
/// Checks USER, LOGNAME, and USERNAME environment variables in order,
/// returning the first one that is set.
pub fn get_system_username() -> Option<String> {
    std::env::var("USER")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("LOGNAME").ok().filter(|s| !s.is_empty()))
        .or_else(|| std::env::var("USERNAME").ok().filter(|s| !s.is_empty()))
}

/// Get the system hostname from environment variables.
pub fn get_system_hostname() -> Option<String> {
    std::env::var("HOSTNAME").ok().filter(|s| !s.is_empty())
}
