// pipeline/prompt/time/io.rs — boundary module for clock access.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

#[must_use]
pub fn current_timestamp_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}
